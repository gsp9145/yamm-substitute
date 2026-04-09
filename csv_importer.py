"""CSV import utilities. The main import logic lives in app.py routes.
This module provides helpers for programmatic/CLI imports."""

import csv
import io
from database import Session
from models import Contact, Tag


def import_csv_string(csv_content, column_mapping):
    """Import contacts from a CSV string with the given column mapping.

    Args:
        csv_content: CSV file content as a string.
        column_mapping: Dict mapping db fields to CSV column names.
            e.g., {'email': 'Email Address', 'first_name': 'First', 'company': 'Company'}

    Returns:
        Dict with counts: {'imported': N, 'updated': N, 'skipped': N}
    """
    db = Session()
    reader = csv.DictReader(io.StringIO(csv_content))

    imported = 0
    updated = 0
    skipped = 0

    for row in reader:
        email_col = column_mapping.get('email')
        if not email_col:
            continue
        email = row.get(email_col, '').strip()
        if not email:
            skipped += 1
            continue

        existing = db.query(Contact).filter_by(email=email).first()
        if existing:
            for db_field in ['first_name', 'last_name', 'company', 'title', 'notes']:
                csv_col = column_mapping.get(db_field)
                if csv_col and row.get(csv_col, '').strip():
                    setattr(existing, db_field, row[csv_col].strip())
            contact = existing
            updated += 1
        else:
            contact = Contact(email=email)
            for db_field in ['first_name', 'last_name', 'company', 'title', 'notes']:
                csv_col = column_mapping.get(db_field)
                if csv_col:
                    setattr(contact, db_field, row.get(csv_col, '').strip())
            db.add(contact)
            imported += 1

        # Tags
        tags_col = column_mapping.get('tags')
        if tags_col and row.get(tags_col, '').strip():
            tag_names = [t.strip() for t in row[tags_col].split(',') if t.strip()]
            for tag_name in tag_names:
                tag = db.query(Tag).filter_by(name=tag_name).first()
                if not tag:
                    tag = Tag(name=tag_name)
                    db.add(tag)
                    db.flush()
                if tag not in contact.tags:
                    contact.tags.append(tag)

    db.commit()
    db.close()
    return {'imported': imported, 'updated': updated, 'skipped': skipped}
