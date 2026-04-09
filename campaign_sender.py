"""Campaign batch sender.

CRITICAL SAFETY RULES (never violate these):
1. NEVER send to the same contact twice in the same campaign.
2. ALWAYS use a fresh, independent DB session per batch (not scoped_session).
3. ALWAYS commit status='sent' IMMEDIATELY after each successful send, BEFORE
   moving to the next contact.
4. ALWAYS re-verify contact status from DB right before sending.
5. If anything goes wrong, PAUSE the campaign — never silently continue.
"""

import logging
import time
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import joinedload
from sqlalchemy import text
from database import get_fresh_session
from models import Campaign, CampaignContact
from gmail_service import (send_email, can_send, GmailAuthError,
                           GmailRateLimitError)
from tracking import personalize, inject_tracking
import config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Global scheduler — lazy start
_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        logger.info("APScheduler started")
    return _scheduler


MAX_RETRIES = 3


def send_next_batch(campaign_id):
    """Send the next batch of emails for a campaign.

    Uses its own independent DB session (not the Flask scoped session)
    to avoid ANY stale data issues.
    """
    db = get_fresh_session()
    try:
        # ── Load campaign with fresh data ──
        campaign = db.query(Campaign).options(
            joinedload(Campaign.template),
        ).filter_by(id=campaign_id).first()

        if not campaign:
            logger.error(f"Campaign {campaign_id} not found. Stopping job.")
            stop_campaign_job(campaign_id)
            return

        if campaign.status != 'sending':
            logger.info(f"Campaign {campaign_id} status is '{campaign.status}', not 'sending'. Stopping job.")
            stop_campaign_job(campaign_id)
            return

        # ── Check daily limit ──
        if not can_send():
            campaign.status = 'paused'
            db.commit()
            logger.warning(f"Campaign {campaign_id} paused: daily send limit reached.")
            stop_campaign_job(campaign_id)
            return

        # ── Get pending contacts (fresh query, no cache) ──
        pending = db.query(CampaignContact).options(
            joinedload(CampaignContact.contact),
        ).filter(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status.in_(['pending', 'retry'])
        ).limit(campaign.batch_size).all()

        if not pending:
            campaign.status = 'completed'
            campaign.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Campaign {campaign_id} completed. No more pending contacts.")
            stop_campaign_job(campaign_id)
            return

        template = campaign.template
        if not template:
            logger.error(f"Campaign {campaign_id} has no template. Pausing.")
            campaign.status = 'paused'
            db.commit()
            stop_campaign_job(campaign_id)
            return

        # ── Send each email one by one ──
        sent_count = 0
        for cc in pending:
            # SAFETY: Re-read this specific record from DB to get absolute latest status
            fresh_status = db.execute(
                text("SELECT status FROM campaign_contact WHERE id = :id"),
                {"id": cc.id}
            ).scalar()

            if fresh_status not in ('pending', 'retry'):
                logger.info(f"SAFETY: Contact {cc.contact_id} status is '{fresh_status}', skipping.")
                continue

            # Check daily limit before each send
            if not can_send():
                logger.warning(f"Daily limit reached mid-batch. Pausing campaign {campaign_id}.")
                campaign.status = 'paused'
                db.commit()
                stop_campaign_job(campaign_id)
                return

            contact = cc.contact
            if not contact or contact.status != 'active':
                cc.status = 'skipped'
                db.commit()
                continue

            try:
                # Personalize
                subject = personalize(template.subject, contact)
                body = personalize(template.body_html, contact)

                # CAN-SPAM footer
                if config.PHYSICAL_ADDRESS:
                    footer = (
                        '<div style="margin-top:30px;padding-top:15px;border-top:1px solid #eee;'
                        'font-size:12px;color:#999;text-align:center;">'
                        f'{config.PHYSICAL_ADDRESS}<br>'
                        '<a href="{{unsubscribe_url}}" style="color:#999;">Unsubscribe</a>'
                        '</div>'
                    )
                    if '</body>' in body.lower():
                        body = body.replace('</body>', footer + '</body>')
                        body = body.replace('</BODY>', footer + '</BODY>')
                    else:
                        body += footer

                # Tracking
                body = inject_tracking(body, cc.id)

                # Unsubscribe URL
                unsubscribe_url = None
                if config.CLOUDFLARE_WORKER_URL:
                    from tracking import generate_tracking_token
                    token = generate_tracking_token(cc.id)
                    unsubscribe_url = f"{config.CLOUDFLARE_WORKER_URL.rstrip('/')}/unsubscribe/{token}"

                # ── SEND ──
                message_id = send_email(
                    to=contact.email,
                    subject=subject,
                    html_body=body,
                    unsubscribe_url=unsubscribe_url,
                )

                # ── IMMEDIATELY mark as sent and commit ──
                # This is the most critical line. We commit RIGHT AWAY so that
                # even if the process crashes after this, the DB knows this
                # contact was already emailed.
                cc.status = 'sent'
                cc.sent_at = datetime.now(timezone.utc)
                cc.message_id = message_id
                campaign.total_sent = (campaign.total_sent or 0) + 1
                db.commit()

                sent_count += 1
                logger.info(f"Sent to {contact.email} (campaign {campaign_id}, cc_id {cc.id})")

                # Small delay between sends for natural pattern
                if sent_count < len(pending):
                    time.sleep(3)

            except GmailAuthError as e:
                logger.error(f"Gmail auth error: {e}. Pausing campaign {campaign_id}.")
                campaign.status = 'paused'
                db.commit()
                stop_campaign_job(campaign_id)
                return

            except GmailRateLimitError:
                logger.warning(f"Rate limit hit. Pausing campaign {campaign_id}.")
                campaign.status = 'paused'
                db.commit()
                stop_campaign_job(campaign_id)
                return

            except Exception as e:
                logger.error(f"Failed to send to {contact.email}: {e}")
                retry_count = cc.retry_count or 0
                if retry_count < MAX_RETRIES:
                    cc.status = 'retry'
                    cc.retry_count = retry_count + 1
                    logger.info(f"Will retry {contact.email} (attempt {retry_count + 1}/{MAX_RETRIES})")
                else:
                    cc.status = 'failed'
                    logger.warning(f"Permanently failed: {contact.email} after {MAX_RETRIES} retries")
                db.commit()

        logger.info(f"Campaign {campaign_id}: batch done. Sent {sent_count} in this batch.")

        # ── Check if campaign is now complete ──
        remaining = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.status.in_(['pending', 'retry'])
        ).count()

        if remaining == 0:
            campaign.status = 'completed'
            campaign.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Campaign {campaign_id} fully completed.")
            stop_campaign_job(campaign_id)

    except Exception as e:
        logger.error(f"UNEXPECTED ERROR in campaign {campaign_id}: {e}", exc_info=True)
        # Try to pause the campaign on any unexpected error
        try:
            campaign = db.query(Campaign).filter_by(id=campaign_id).first()
            if campaign and campaign.status == 'sending':
                campaign.status = 'paused'
                db.commit()
                logger.info(f"Campaign {campaign_id} paused due to unexpected error.")
        except Exception:
            pass
        stop_campaign_job(campaign_id)
    finally:
        db.close()


def start_campaign_job(campaign_id):
    """Start the periodic batch-sending job for a campaign."""
    sched = get_scheduler()

    db = get_fresh_session()
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    delay = campaign.batch_delay if campaign else 60
    db.close()

    job_id = f'campaign_{campaign_id}'

    # Remove any existing job for this campaign
    try:
        if sched.get_job(job_id):
            sched.remove_job(job_id)
    except Exception:
        pass

    sched.add_job(
        send_next_batch,
        'interval',
        seconds=delay,
        args=[campaign_id],
        id=job_id,
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    logger.info(f"Started campaign job {job_id} (batch every {delay}s)")


def stop_campaign_job(campaign_id):
    """Stop the batch-sending job for a campaign."""
    sched = get_scheduler()
    job_id = f'campaign_{campaign_id}'
    try:
        if sched.get_job(job_id):
            sched.remove_job(job_id)
            logger.info(f"Stopped campaign job {job_id}")
    except Exception as e:
        logger.error(f"Error stopping job {job_id}: {e}")
