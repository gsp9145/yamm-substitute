import os
import csv
import io
import uuid
import re
from datetime import datetime, timezone
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   jsonify, session as flask_session)
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload
from email_validator import validate_email, EmailNotValidError
from database import init_db, Session
from models import (Contact, Tag, contact_tag, EmailTemplate, Campaign,
                    CampaignContact, TrackingEvent, DailySendLog)
import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB upload limit
app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF tokens don't expire with session

# Enable CSRF protection on all POST forms
csrf = CSRFProtect(app)


# ─── Initialize DB on startup ───
with app.app_context():
    init_db()


# ─── Timezone filter for templates ───
from zoneinfo import ZoneInfo

@app.template_filter('localtime')
def localtime_filter(dt, fmt='%b %d, %H:%M'):
    """Convert a UTC datetime to local timezone for display."""
    if dt is None:
        return '-'
    try:
        utc = ZoneInfo('UTC')
        local_tz = ZoneInfo(config.DISPLAY_TIMEZONE)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=utc)
        local_dt = dt.astimezone(local_tz)
        return local_dt.strftime(fmt)
    except Exception:
        return dt.strftime(fmt) if dt else '-'


@app.template_filter('localtime_full')
def localtime_full_filter(dt):
    """Convert UTC datetime to local timezone with full date."""
    return localtime_filter(dt, '%b %d, %Y %H:%M')


# ─── Template context processor ───
@app.context_processor
def inject_globals():
    """Inject global template variables. Uses the request-scoped session
    which will be cleaned up by teardown_appcontext — do NOT close it here."""
    db = Session()
    contact_count = db.query(func.count(Contact.id)).scalar()
    campaign_count = db.query(func.count(Campaign.id)).scalar()
    template_count = db.query(func.count(EmailTemplate.id)).scalar()
    return dict(
        total_contacts=contact_count,
        total_campaigns=campaign_count,
        total_templates=template_count,
    )


# ─── Teardown ───
@app.teardown_appcontext
def remove_session(exception=None):
    if exception:
        Session.rollback()
    Session.remove()


# ═══════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════

@app.route('/')
def dashboard():
    db = Session()
    total_contacts = db.query(func.count(Contact.id)).scalar()
    total_campaigns = db.query(func.count(Campaign.id)).scalar()
    total_templates = db.query(func.count(EmailTemplate.id)).scalar()
    active_campaigns = db.query(Campaign).options(joinedload(Campaign.campaign_contacts)).filter(Campaign.status == 'sending').all()
    recent_campaigns = db.query(Campaign).options(joinedload(Campaign.campaign_contacts)).order_by(Campaign.created_at.desc()).limit(5).all()

    total_sent = db.query(func.coalesce(func.sum(Campaign.total_sent), 0)).scalar()
    total_opened = db.query(func.coalesce(func.sum(Campaign.total_opened), 0)).scalar()
    total_clicked = db.query(func.coalesce(func.sum(Campaign.total_clicked), 0)).scalar()

    # Today's send count
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    daily_log = db.query(DailySendLog).filter_by(date_str=today).first()
    today_sent = daily_log.count if daily_log else 0

    return render_template('dashboard.html',
                           total_contacts=total_contacts,
                           total_campaigns=total_campaigns,
                           total_templates=total_templates,
                           active_campaigns=active_campaigns,
                           recent_campaigns=recent_campaigns,
                           total_sent=total_sent,
                           total_opened=total_opened,
                           total_clicked=total_clicked,
                           today_sent=today_sent,
                           daily_limit=config.DAILY_SEND_LIMIT)


# ═══════════════════════════════════════════
#  CONTACTS
# ═══════════════════════════════════════════

@app.route('/contacts')
def contact_list():
    db = Session()
    search = request.args.get('search', '').strip()
    tag_filter = request.args.get('tag', '').strip()
    status_filter = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = db.query(Contact)

    if search:
        like = f'%{search}%'
        query = query.filter(or_(
            Contact.email.ilike(like),
            Contact.first_name.ilike(like),
            Contact.last_name.ilike(like),
            Contact.company.ilike(like),
        ))
    if tag_filter:
        query = query.filter(Contact.tags.any(Tag.name == tag_filter))
    if status_filter:
        query = query.filter(Contact.status == status_filter)

    total = query.count()
    contacts = query.options(joinedload(Contact.tags)).order_by(Contact.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    tags = db.query(Tag).order_by(Tag.name).all()
    total_pages = (total + per_page - 1) // per_page

    return render_template('contacts/list.html',
                           contacts=contacts, tags=tags,
                           search=search, tag_filter=tag_filter, status_filter=status_filter,
                           page=page, total_pages=total_pages, total=total)


@app.route('/contacts/<int:contact_id>')
def contact_detail(contact_id):
    db = Session()
    contact = db.query(Contact).options(
        joinedload(Contact.tags),
        joinedload(Contact.campaign_contacts).joinedload(CampaignContact.campaign).joinedload(Campaign.template),
        joinedload(Contact.campaign_contacts).joinedload(CampaignContact.tracking_events),
    ).get(contact_id)
    if not contact:
        flash('Contact not found.', 'danger')
        return redirect(url_for('contact_list'))
    tags = db.query(Tag).order_by(Tag.name).all()
    return render_template('contacts/detail.html', contact=contact, tags=tags)


@app.route('/contacts/<int:contact_id>/edit', methods=['POST'])
def contact_edit(contact_id):
    db = Session()
    contact = db.query(Contact).get(contact_id)
    if not contact:
        flash('Contact not found.', 'danger')
        return redirect(url_for('contact_list'))

    contact.first_name = request.form.get('first_name', '').strip()
    contact.last_name = request.form.get('last_name', '').strip()
    contact.email = request.form.get('email', '').strip()
    contact.company = request.form.get('company', '').strip()
    contact.title = request.form.get('title', '').strip()
    contact.notes = request.form.get('notes', '').strip()
    contact.status = request.form.get('status', 'active')

    # Update tags
    tag_ids = request.form.getlist('tag_ids')
    contact.tags = db.query(Tag).filter(Tag.id.in_(tag_ids)).all() if tag_ids else []

    db.commit()
    flash('Contact updated.', 'success')
    return redirect(url_for('contact_detail', contact_id=contact.id))


@app.route('/contacts/bulk-tag', methods=['POST'])
def contacts_bulk_tag():
    db = Session()
    contact_ids = request.form.getlist('contact_ids', type=int)
    tag_id = request.form.get('tag_id', type=int)
    if not contact_ids or not tag_id:
        flash('No contacts or tag selected.', 'warning')
        return redirect(url_for('contact_list'))
    tag = db.query(Tag).get(tag_id)
    if not tag:
        flash('Tag not found.', 'danger')
        return redirect(url_for('contact_list'))
    contacts = db.query(Contact).filter(Contact.id.in_(contact_ids)).all()
    count = 0
    for contact in contacts:
        if tag not in contact.tags:
            contact.tags.append(tag)
            count += 1
    db.commit()
    flash(f'Tagged {count} contacts with "{tag.name}".', 'success')
    return redirect(url_for('contact_list'))


@app.route('/contacts/bulk-delete', methods=['POST'])
def contacts_bulk_delete():
    db = Session()
    contact_ids = request.form.getlist('contact_ids', type=int)
    if not contact_ids:
        flash('No contacts selected.', 'warning')
        return redirect(url_for('contact_list'))
    deleted = db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(synchronize_session=False)
    db.commit()
    flash(f'{deleted} contacts deleted.', 'success')
    return redirect(url_for('contact_list'))


@app.route('/contacts/delete-all', methods=['POST'])
def contacts_delete_all():
    db = Session()
    count = db.query(Contact).count()
    db.query(Contact).delete()
    db.query(Tag).delete()
    db.commit()
    flash(f'All {count} contacts and tags deleted.', 'success')
    return redirect(url_for('contact_list'))


@app.route('/contacts/<int:contact_id>/delete', methods=['POST'])
def contact_delete(contact_id):
    db = Session()
    contact = db.query(Contact).get(contact_id)
    if contact:
        db.delete(contact)
        db.commit()
        flash('Contact deleted.', 'success')
    return redirect(url_for('contact_list'))


@app.route('/contacts/add', methods=['POST'])
def contact_add():
    db = Session()
    email = request.form.get('email', '').strip().lower()
    if not email:
        flash('Email is required.', 'danger')
        return redirect(url_for('contact_list'))

    # Validate email format
    try:
        validated = validate_email(email, check_deliverability=False)
        email = validated.normalized
    except EmailNotValidError as e:
        flash(f'Invalid email: {e}', 'danger')
        return redirect(url_for('contact_list'))

    existing = db.query(Contact).filter_by(email=email).first()
    if existing:
        flash(f'Contact with email {email} already exists.', 'warning')
        return redirect(url_for('contact_detail', contact_id=existing.id))

    contact = Contact(
        email=email,
        first_name=request.form.get('first_name', '').strip(),
        last_name=request.form.get('last_name', '').strip(),
        company=request.form.get('company', '').strip(),
        title=request.form.get('title', '').strip(),
    )
    db.add(contact)
    db.commit()
    flash('Contact added.', 'success')
    return redirect(url_for('contact_detail', contact_id=contact.id))


# ─── CSV Import ───

@app.route('/contacts/import', methods=['GET', 'POST'])
def contact_import():
    if request.method == 'GET':
        return render_template('contacts/import.html', step='upload')

    # Step 1: Upload file and show column mapping
    if 'csv_file' in request.files:
        file = request.files['csv_file']
        if not file.filename or not file.filename.endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return render_template('contacts/import.html', step='upload')

        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        preview_rows = []
        for i, row in enumerate(reader):
            if i >= 5:
                break
            preview_rows.append(row)

        # Store CSV on disk (not in session cookie — avoids 4KB cookie limit)
        import_id = str(uuid.uuid4())
        csv_path = os.path.join(config.UPLOAD_DIR, f'{import_id}.csv')
        with open(csv_path, 'w') as f:
            f.write(content)
        flask_session['import_id'] = import_id
        flask_session['import_headers'] = headers

        db_fields = ['skip', 'email', 'first_name', 'last_name', 'company', 'title', 'notes', 'tags']
        return render_template('contacts/import.html', step='mapping',
                               headers=headers, preview_rows=preview_rows, db_fields=db_fields)

    # Step 2: Process with column mapping — kicks off background import
    if 'mapping' in request.form:
        import_id = flask_session.get('import_id', '')
        headers = flask_session.get('import_headers', [])
        csv_path = os.path.join(config.UPLOAD_DIR, f'{import_id}.csv') if import_id else ''
        if not import_id or not os.path.exists(csv_path):
            flash('CSV data lost. Please re-upload.', 'danger')
            return redirect(url_for('contact_import'))

        # Build mapping: csv_column -> db_field
        mapping = {}
        for h in headers:
            field = request.form.get(f'map_{h}', 'skip')
            if field != 'skip':
                mapping[h] = field

        if 'email' not in mapping.values():
            flash('You must map at least one column to "email".', 'danger')
            return redirect(url_for('contact_import'))

        # Invert: db_field -> csv_column
        field_to_col = {v: k for k, v in mapping.items()}

        # Tags to auto-apply to all imported contacts
        import_tags_str = request.form.get('import_tags', '').strip()
        import_tag_names = [t.strip() for t in import_tags_str.split(',') if t.strip()] if import_tags_str else []

        flask_session.pop('import_id', None)
        flask_session.pop('import_headers', None)

        # Count total rows for progress tracking
        with open(csv_path) as f:
            total_rows = sum(1 for _ in f) - 1  # minus header

        # Store import job state in a progress file
        import json
        progress_path = os.path.join(config.UPLOAD_DIR, f'{import_id}_progress.json')
        with open(progress_path, 'w') as f:
            json.dump({'status': 'running', 'total': total_rows, 'processed': 0,
                        'imported': 0, 'updated': 0, 'skipped': 0}, f)

        # Run import in background thread
        import threading
        def background_import():
            from database import get_fresh_session
            db = get_fresh_session()
            imported = 0
            updated = 0
            skipped = 0
            processed = 0
            CHUNK_SIZE = 500

            try:
                with open(csv_path) as f:
                    reader = csv.DictReader(f)
                    tag_cache = {}  # cache tag lookups

                    for row in reader:
                        email_col = field_to_col.get('email')
                        email = row.get(email_col, '').strip().lower() if email_col else ''
                        if not email:
                            skipped += 1
                            processed += 1
                            continue

                        existing = db.query(Contact).filter_by(email=email).first()
                        if existing:
                            for db_field in ['first_name', 'last_name', 'company', 'title', 'notes']:
                                csv_col = field_to_col.get(db_field)
                                if csv_col and row.get(csv_col, '').strip():
                                    setattr(existing, db_field, row[csv_col].strip())
                            contact = existing
                            updated += 1
                        else:
                            contact = Contact(email=email)
                            for db_field in ['first_name', 'last_name', 'company', 'title', 'notes']:
                                csv_col = field_to_col.get(db_field)
                                if csv_col:
                                    setattr(contact, db_field, row.get(csv_col, '').strip())
                            db.add(contact)
                            imported += 1

                        # Handle per-row tags from CSV column
                        tags_col = field_to_col.get('tags')
                        if tags_col and row.get(tags_col, '').strip():
                            tag_names = [t.strip() for t in row[tags_col].split(',') if t.strip()]
                            for tag_name in tag_names:
                                if tag_name not in tag_cache:
                                    tag = db.query(Tag).filter_by(name=tag_name).first()
                                    if not tag:
                                        tag = Tag(name=tag_name)
                                        db.add(tag)
                                        db.flush()
                                    tag_cache[tag_name] = tag
                                tag = tag_cache[tag_name]
                                if tag not in contact.tags:
                                    contact.tags.append(tag)

                        # Apply import-level tags to every contact
                        for itn in import_tag_names:
                            if itn not in tag_cache:
                                tag = db.query(Tag).filter_by(name=itn).first()
                                if not tag:
                                    tag = Tag(name=itn)
                                    db.add(tag)
                                    db.flush()
                                tag_cache[itn] = tag
                            tag = tag_cache[itn]
                            if tag not in contact.tags:
                                contact.tags.append(tag)

                        processed += 1

                        # Commit every CHUNK_SIZE rows
                        if processed % CHUNK_SIZE == 0:
                            db.commit()
                            with open(progress_path, 'w') as pf:
                                json.dump({'status': 'running', 'total': total_rows,
                                           'processed': processed, 'imported': imported,
                                           'updated': updated, 'skipped': skipped}, pf)

                # Final commit
                db.commit()
                with open(progress_path, 'w') as pf:
                    json.dump({'status': 'complete', 'total': total_rows,
                               'processed': processed, 'imported': imported,
                               'updated': updated, 'skipped': skipped}, pf)
            except Exception as e:
                db.rollback()
                with open(progress_path, 'w') as pf:
                    json.dump({'status': 'error', 'total': total_rows,
                               'processed': processed, 'imported': imported,
                               'updated': updated, 'skipped': skipped,
                               'error': str(e)}, pf)
            finally:
                db.close()
                # Clean up CSV file after processing
                if os.path.exists(csv_path):
                    os.remove(csv_path)

        thread = threading.Thread(target=background_import, daemon=True)
        thread.start()

        return redirect(url_for('import_progress', import_id=import_id))

    return redirect(url_for('contact_import'))


@app.route('/contacts/import/progress/<import_id>')
def import_progress(import_id):
    return render_template('contacts/import_progress.html', import_id=import_id)


@app.route('/api/import-progress/<import_id>')
def api_import_progress(import_id):
    import json
    progress_path = os.path.join(config.UPLOAD_DIR, f'{import_id}_progress.json')
    if not os.path.exists(progress_path):
        return jsonify({'status': 'not_found'})
    with open(progress_path) as f:
        data = json.load(f)
    # Clean up progress file if complete
    if data.get('status') in ('complete', 'error'):
        try:
            os.remove(progress_path)
        except OSError:
            pass
    return jsonify(data)


# ─── Tags ───

@app.route('/tags')
def tag_list():
    from models import contact_tag
    db = Session()
    tags = db.query(Tag, func.count(contact_tag.c.contact_id)).outerjoin(
        contact_tag, Tag.id == contact_tag.c.tag_id
    ).group_by(Tag.id).order_by(Tag.name).all()
    return render_template('tags.html', tags=tags)


@app.route('/tags/add', methods=['POST'])
def tag_add():
    db = Session()
    name = request.form.get('name', '').strip()
    redirect_to = request.form.get('redirect_to', 'contacts')
    if name:
        existing = db.query(Tag).filter_by(name=name).first()
        if not existing:
            db.add(Tag(name=name))
            db.commit()
            flash(f'Tag "{name}" created.', 'success')
        else:
            flash(f'Tag "{name}" already exists.', 'warning')
    if redirect_to == 'tags':
        return redirect(url_for('tag_list'))
    return redirect(url_for('contact_list'))


@app.route('/tags/<int:tag_id>/delete', methods=['POST'])
def tag_delete(tag_id):
    db = Session()
    tag = db.query(Tag).get(tag_id)
    if tag:
        db.delete(tag)
        db.commit()
        flash('Tag deleted.', 'success')
    return redirect(url_for('contact_list'))


# ═══════════════════════════════════════════
#  EMAIL TEMPLATES
# ═══════════════════════════════════════════

@app.route('/templates')
def template_list():
    db = Session()
    templates = db.query(EmailTemplate).order_by(EmailTemplate.updated_at.desc()).all()
    return render_template('email_templates/list.html', templates=templates)


@app.route('/templates/new', methods=['GET', 'POST'])
def template_new():
    if request.method == 'GET':
        return render_template('email_templates/editor.html', template=None)

    db = Session()
    template = EmailTemplate(
        name=request.form.get('name', '').strip() or 'Untitled',
        subject=request.form.get('subject', '').strip(),
        body_html=request.form.get('body_html', ''),
    )
    db.add(template)
    db.commit()
    flash('Template created.', 'success')
    return redirect(url_for('template_edit', template_id=template.id))


@app.route('/templates/<int:template_id>/edit', methods=['GET', 'POST'])
def template_edit(template_id):
    db = Session()
    template = db.query(EmailTemplate).get(template_id)
    if not template:
        flash('Template not found.', 'danger')
        return redirect(url_for('template_list'))

    if request.method == 'POST':
        template.name = request.form.get('name', '').strip() or 'Untitled'
        template.subject = request.form.get('subject', '').strip()
        template.body_html = request.form.get('body_html', '')
        template.updated_at = datetime.now(timezone.utc)
        db.commit()
        flash('Template saved.', 'success')
        return redirect(url_for('template_edit', template_id=template.id))

    return render_template('email_templates/editor.html', template=template)


@app.route('/templates/<int:template_id>/delete', methods=['POST'])
def template_delete(template_id):
    db = Session()
    template = db.query(EmailTemplate).get(template_id)
    if template:
        db.delete(template)
        db.commit()
        flash('Template deleted.', 'success')
    return redirect(url_for('template_list'))


@app.route('/templates/<int:template_id>/preview')
def template_preview(template_id):
    db = Session()
    template = db.query(EmailTemplate).get(template_id)
    if not template:
        return 'Template not found', 404

    # Preview with sample data
    sample = {
        '{first_name}': 'John',
        '{last_name}': 'Doe',
        '{company}': 'Acme Inc',
        '{title}': 'CEO',
        '{email}': 'john@example.com',
    }
    html = template.body_html
    for var, val in sample.items():
        html = html.replace(var, val)
    return html


# ═══════════════════════════════════════════
#  CAMPAIGNS
# ═══════════════════════════════════════════

@app.route('/campaigns')
def campaign_list():
    db = Session()
    campaigns = db.query(Campaign).options(
        joinedload(Campaign.template),
        joinedload(Campaign.campaign_contacts),
    ).order_by(Campaign.created_at.desc()).all()
    return render_template('campaigns/list.html', campaigns=campaigns)


@app.route('/campaigns/new', methods=['GET', 'POST'])
def campaign_new():
    db = Session()
    if request.method == 'GET':
        templates = db.query(EmailTemplate).order_by(EmailTemplate.name).all()
        tags = db.query(Tag).order_by(Tag.name).all()
        past_campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
        return render_template('campaigns/create.html', templates=templates, tags=tags,
                               past_campaigns=past_campaigns)

    # Create campaign — validate template exists first
    template_id = request.form.get('template_id', type=int)
    if not template_id or not db.query(EmailTemplate).get(template_id):
        flash('Please select a valid email template.', 'danger')
        return redirect(url_for('campaign_new'))

    campaign = Campaign(
        name=request.form.get('name', '').strip() or 'Untitled Campaign',
        template_id=template_id,
        batch_size=request.form.get('batch_size', config.DEFAULT_BATCH_SIZE, type=int),
        batch_delay=request.form.get('batch_delay', config.DEFAULT_BATCH_DELAY, type=int),
    )
    db.add(campaign)
    db.flush()

    # Build contact query — start with active contacts
    query = db.query(Contact).filter(Contact.status == 'active')

    # Filter by tags if selected
    tag_ids = request.form.getlist('tag_ids')
    if tag_ids:
        query = query.filter(Contact.tags.any(Tag.id.in_(tag_ids)))

    # Exclude contacts from previous campaigns if selected
    exclude_campaign_ids = request.form.getlist('exclude_campaign_ids', type=int)
    if exclude_campaign_ids:
        already_contacted = db.query(CampaignContact.contact_id).filter(
            CampaignContact.campaign_id.in_(exclude_campaign_ids)
        ).scalar_subquery()
        query = query.filter(~Contact.id.in_(already_contacted))

    contacts = query.all()

    for contact in contacts:
        cc = CampaignContact(campaign_id=campaign.id, contact_id=contact.id)
        db.add(cc)

    db.commit()
    flash(f'Campaign created with {len(contacts)} recipients.', 'success')
    return redirect(url_for('campaign_detail', campaign_id=campaign.id))


@app.route('/campaigns/<int:campaign_id>')
def campaign_detail(campaign_id):
    db = Session()
    campaign = db.query(Campaign).options(
        joinedload(Campaign.template),
        joinedload(Campaign.campaign_contacts),
    ).get(campaign_id)
    if not campaign:
        flash('Campaign not found.', 'danger')
        return redirect(url_for('campaign_list'))

    # Per-contact stats
    campaign_contacts = db.query(CampaignContact).options(
        joinedload(CampaignContact.contact),
        joinedload(CampaignContact.tracking_events),
    ).filter_by(
        campaign_id=campaign.id
    ).order_by(CampaignContact.sent_at.desc()).all()

    sent_count = sum(1 for cc in campaign_contacts if cc.status == 'sent')
    pending_count = sum(1 for cc in campaign_contacts if cc.status == 'pending')
    failed_count = sum(1 for cc in campaign_contacts if cc.status == 'failed')

    return render_template('campaigns/detail.html',
                           campaign=campaign,
                           campaign_contacts=campaign_contacts,
                           sent_count=sent_count,
                           pending_count=pending_count,
                           failed_count=failed_count)


@app.route('/campaigns/<int:campaign_id>/review')
def campaign_review(campaign_id):
    db = Session()
    campaign = db.query(Campaign).options(
        joinedload(Campaign.template),
        joinedload(Campaign.campaign_contacts),
    ).get(campaign_id)
    if not campaign or not campaign.template:
        flash('Campaign or template not found.', 'danger')
        return redirect(url_for('campaign_list'))

    # Show 3 sample personalized emails
    sample_ccs = db.query(CampaignContact).options(joinedload(CampaignContact.contact)).filter_by(
        campaign_id=campaign.id
    ).limit(3).all()

    samples = []
    for cc in sample_ccs:
        c = cc.contact
        subject = campaign.template.subject
        body = campaign.template.body_html
        for var, val in {'{first_name}': c.first_name, '{last_name}': c.last_name,
                         '{company}': c.company, '{title}': c.title, '{email}': c.email}.items():
            subject = subject.replace(var, val or '')
            body = body.replace(var, val or '')
        samples.append({'contact': c, 'subject': subject, 'body': body})

    return render_template('campaigns/review.html', campaign=campaign, samples=samples)


@app.route('/campaigns/<int:campaign_id>/start', methods=['POST'])
def campaign_start(campaign_id):
    db = Session()
    campaign = db.query(Campaign).get(campaign_id)
    if not campaign:
        flash('Campaign not found.', 'danger')
        return redirect(url_for('campaign_list'))

    if campaign.status not in ('draft', 'paused'):
        flash('Campaign cannot be started.', 'warning')
        return redirect(url_for('campaign_detail', campaign_id=campaign.id))

    # Check Gmail is connected
    if not os.path.exists(config.GOOGLE_TOKEN_FILE):
        flash('Please connect Gmail first in Settings.', 'danger')
        return redirect(url_for('settings'))

    campaign.status = 'sending'
    campaign.started_at = campaign.started_at or datetime.now(timezone.utc)
    db.commit()

    # Start the batch sender
    from campaign_sender import start_campaign_job
    start_campaign_job(campaign.id)

    flash('Campaign started! Emails are being sent in batches.', 'success')
    return redirect(url_for('campaign_detail', campaign_id=campaign.id))


@app.route('/campaigns/<int:campaign_id>/pause', methods=['POST'])
def campaign_pause(campaign_id):
    db = Session()
    campaign = db.query(Campaign).get(campaign_id)
    if not campaign:
        flash('Campaign not found.', 'danger')
        return redirect(url_for('campaign_list'))

    if campaign.status == 'sending':
        campaign.status = 'paused'
        db.commit()

        from campaign_sender import stop_campaign_job
        stop_campaign_job(campaign.id)

        flash('Campaign paused.', 'info')
    return redirect(url_for('campaign_detail', campaign_id=campaign.id))


@app.route('/campaigns/<int:campaign_id>/delete', methods=['POST'])
def campaign_delete(campaign_id):
    db = Session()
    campaign = db.query(Campaign).get(campaign_id)
    if not campaign:
        flash('Campaign not found.', 'danger')
        return redirect(url_for('campaign_list'))

    if campaign.status == 'sending':
        from campaign_sender import stop_campaign_job
        stop_campaign_job(campaign.id)
    db.delete(campaign)
    db.commit()
    flash('Campaign deleted.', 'success')
    return redirect(url_for('campaign_list'))


# ═══════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════

@app.route('/settings')
def settings():
    gmail_connected = os.path.exists(config.GOOGLE_TOKEN_FILE)
    gmail_email = None
    if gmail_connected:
        try:
            from gmail_service import get_sender_email
            gmail_email = get_sender_email()
        except Exception:
            gmail_email = '(unable to read)'

    return render_template('settings.html',
                           gmail_connected=gmail_connected,
                           gmail_email=gmail_email,
                           worker_url=config.CLOUDFLARE_WORKER_URL)


@app.route('/settings/connect-gmail', methods=['POST'])
def connect_gmail():
    try:
        from gmail_service import authorize_gmail
        authorize_gmail()
        flash('Gmail connected successfully!', 'success')
    except Exception as e:
        flash(f'Gmail connection failed: {str(e)}', 'danger')
    return redirect(url_for('settings'))


@app.route('/settings/disconnect-gmail', methods=['POST'])
def disconnect_gmail():
    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        os.remove(config.GOOGLE_TOKEN_FILE)
        flash('Gmail disconnected.', 'info')
    return redirect(url_for('settings'))


@app.route('/settings/test-email', methods=['POST'])
def test_email():
    to_email = request.form.get('email', '').strip()
    if not to_email:
        flash('Please enter an email address.', 'danger')
        return redirect(url_for('settings'))
    try:
        from gmail_service import send_email
        send_email(
            to=to_email,
            subject='CreatorCRM Test Email',
            html_body='<h2>It works!</h2><p>Your Gmail integration is set up correctly.</p>',
        )
        flash(f'Test email sent to {to_email}!', 'success')
    except Exception as e:
        flash(f'Failed to send: {str(e)}', 'danger')
    return redirect(url_for('settings'))


# ═══════════════════════════════════════════
#  TRACKING CALLBACK (from Cloudflare Worker)
# ═══════════════════════════════════════════

@app.route('/api/track', methods=['POST'])
@csrf.exempt  # Uses its own HMAC-based auth, not session cookies
def track_event():
    """Receive tracking events from Cloudflare Worker or polling job."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'no data'}), 400

    secret = request.headers.get('X-Tracking-Secret', '')
    if secret != config.TRACKING_SECRET:
        return jsonify({'error': 'unauthorized'}), 403

    db = Session()
    events = data if isinstance(data, list) else [data]
    for evt in events:
        cc_id = evt.get('campaign_contact_id')
        if not cc_id:
            continue
        cc = db.query(CampaignContact).get(cc_id)
        if not cc:
            continue

        event_type = evt.get('event_type', 'open')

        tracking_event = TrackingEvent(
            campaign_contact_id=cc_id,
            event_type=event_type,
            url=evt.get('url'),
            ip_address=evt.get('ip'),
            user_agent=evt.get('user_agent'),
        )
        db.add(tracking_event)

        # Update campaign counters — only count FIRST open/click per contact
        existing_count = db.query(func.count(TrackingEvent.id)).filter(
            TrackingEvent.campaign_contact_id == cc_id,
            TrackingEvent.event_type == event_type,
        ).scalar()

        campaign = cc.campaign
        if existing_count <= 1:  # This is the first event of this type
            if event_type == 'open':
                campaign.total_opened = (campaign.total_opened or 0) + 1
            elif event_type == 'click':
                campaign.total_clicked = (campaign.total_clicked or 0) + 1

    db.commit()
    return jsonify({'status': 'ok', 'processed': len(events)})


# ═══════════════════════════════════════════
#  CONTACTS CSV EXPORT
# ═══════════════════════════════════════════

@app.route('/contacts/export')
def contact_export():
    db = Session()
    contacts = db.query(Contact).options(joinedload(Contact.tags)).order_by(Contact.email).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['email', 'first_name', 'last_name', 'company', 'title', 'status', 'tags'])
    for c in contacts:
        tags = ', '.join(t.name for t in c.tags)
        writer.writerow([c.email, c.first_name, c.last_name, c.company, c.title, c.status, tags])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=contacts_export.csv'}
    )


# ═══════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
#  TRACKING POLLER (pulls events from Cloudflare Worker)
# ═══════════════════════════════════════════

def poll_tracking_events():
    """Pull new tracking events from Cloudflare Worker D1 and store locally."""
    import requests as http_requests
    if not config.CLOUDFLARE_WORKER_URL:
        return

    worker_url = config.CLOUDFLARE_WORKER_URL.rstrip('/')
    try:
        resp = http_requests.get(
            f'{worker_url}/api/events',
            params={'secret': config.TRACKING_SECRET, 'since': '2000-01-01', 'limit': '500'},
            timeout=10,
            verify=False,
        )
        if resp.status_code != 200:
            return

        events = resp.json()
        if not events:
            return

        from database import get_fresh_session
        db = get_fresh_session()
        try:
            latest_id = 0
            for evt in events:
                cc_id = evt.get('campaign_contact_id')
                if not cc_id:
                    continue
                cc = db.query(CampaignContact).get(cc_id)
                if not cc:
                    continue

                event_type = evt.get('event_type', 'open')

                # Use the ACTUAL timestamp from Cloudflare (when the event really happened)
                # not the time we're polling it
                actual_time = None
                cf_time = evt.get('created_at')
                if cf_time:
                    try:
                        actual_time = datetime.strptime(cf_time, '%Y-%m-%d %H:%M:%S').replace(
                            tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass

                tracking_event = TrackingEvent(
                    campaign_contact_id=cc_id,
                    event_type=event_type,
                    url=evt.get('url'),
                    ip_address=evt.get('ip_address'),
                    user_agent=evt.get('user_agent'),
                    created_at=actual_time or datetime.now(timezone.utc),
                )
                db.add(tracking_event)

                # Handle unsubscribe — mark contact as unsubscribed
                if event_type == 'unsubscribe':
                    contact = cc.contact
                    if contact:
                        contact.status = 'unsubscribed'
                else:
                    # Only count first open/click per contact
                    existing = db.query(func.count(TrackingEvent.id)).filter(
                        TrackingEvent.campaign_contact_id == cc_id,
                        TrackingEvent.event_type == event_type,
                    ).scalar()
                    if existing <= 1:
                        campaign = cc.campaign
                        if event_type == 'open':
                            campaign.total_opened = (campaign.total_opened or 0) + 1
                        elif event_type == 'click':
                            campaign.total_clicked = (campaign.total_clicked or 0) + 1

                latest_id = max(latest_id, evt.get('id', 0))

            db.commit()

            # Clean up processed events from Cloudflare
            if latest_id > 0 and events:
                latest_time = events[-1].get('created_at', '')
                if latest_time:
                    http_requests.delete(
                        f'{worker_url}/api/events',
                        params={'secret': config.TRACKING_SECRET, 'before': latest_time},
                        timeout=10,
                        verify=False,
                    )
        finally:
            db.close()

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Tracking poll error: {e}")


def start_tracking_poller():
    """Start background job to poll tracking events every 5 minutes."""
    if not config.CLOUDFLARE_WORKER_URL:
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    poller = BackgroundScheduler()
    poller.add_job(poll_tracking_events, 'interval', minutes=1, id='tracking_poller')
    poller.start()
    import logging
    logging.getLogger(__name__).info("Tracking poller started (every 5 min)")


# ═══════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════

if __name__ == '__main__':
    import os
    # Only start poller in the main process (not Flask reloader child)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        start_tracking_poller()

    print("\n  ✦ CreatorCRM running at http://localhost:5050\n")
    app.run(debug=True, port=5050)
