import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import text
from database import get_fresh_session
from models import DailySendLog
import config


class GmailAuthError(Exception):
    """Raised when Gmail authentication fails or token is expired."""
    pass


class GmailRateLimitError(Exception):
    """Raised when daily send limit is reached."""
    pass


def get_credentials():
    """Get or refresh Gmail OAuth2 credentials."""
    creds = None
    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_FILE, config.GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise GmailAuthError(
                    f"Gmail token expired and refresh failed: {e}. "
                    "Please reconnect Gmail in Settings."
                )
        else:
            if not os.path.exists(config.GOOGLE_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"credentials.json not found at {config.GOOGLE_CREDENTIALS_FILE}. "
                    "Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GOOGLE_CREDENTIALS_FILE, config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=8095, open_browser=True)

        with open(config.GOOGLE_TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return creds


def get_gmail_service():
    """Build and return Gmail API service."""
    creds = get_credentials()
    return build('gmail', 'v1', credentials=creds)


def authorize_gmail():
    """Force re-authorization (used by Settings page)."""
    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        os.remove(config.GOOGLE_TOKEN_FILE)
    get_credentials()


_cached_sender_email = None

def get_sender_email():
    """Get the email address of the connected Gmail account (cached after first call)."""
    global _cached_sender_email
    if _cached_sender_email:
        return _cached_sender_email
    service = get_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    _cached_sender_email = profile.get('emailAddress', 'unknown')
    return _cached_sender_email


def get_effective_daily_limit():
    """Get the effective daily limit (warm-up or full)."""
    if config.WARMUP_DAILY_MAX > 0:
        return min(config.WARMUP_DAILY_MAX, config.DAILY_SEND_LIMIT)
    return config.DAILY_SEND_LIMIT


def get_today_send_count():
    """Get number of emails sent today. Uses fresh session to always get real count."""
    db = get_fresh_session()
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        log = db.query(DailySendLog).filter_by(date_str=today).first()
        return log.count if log else 0
    finally:
        db.close()


def increment_send_count():
    """Atomically increment today's send counter using SQL UPDATE."""
    db = get_fresh_session()
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        result = db.execute(
            text("UPDATE daily_send_log SET count = count + 1 WHERE date_str = :d"),
            {"d": today}
        )
        if result.rowcount == 0:
            db.execute(
                text("INSERT OR IGNORE INTO daily_send_log (date_str, count) VALUES (:d, 1)"),
                {"d": today}
            )
        db.commit()
    finally:
        db.close()


def can_send():
    """Check if we're under the daily send limit."""
    return get_today_send_count() < get_effective_daily_limit()


def send_email(to, subject, html_body, unsubscribe_url=None, reply_to=None):
    """Send a single email via Gmail API with proper deliverability headers.

    Returns the Gmail message ID on success.
    Raises GmailAuthError, GmailRateLimitError, or other exceptions on failure.
    """
    if not can_send():
        raise GmailRateLimitError(
            f"Daily send limit ({get_effective_daily_limit()}) reached. Try again tomorrow."
        )

    try:
        service = get_gmail_service()
    except GmailAuthError:
        raise
    except Exception as e:
        raise GmailAuthError(f"Failed to connect to Gmail: {e}")

    msg = MIMEMultipart('alternative')
    msg['To'] = to
    msg['Subject'] = subject

    # Set From with display name so inbox shows "Buzzlee" not "team@buzzlee.club"
    if config.SENDER_NAME:
        sender_email = get_sender_email()
        msg['From'] = f'{config.SENDER_NAME} <{sender_email}>'

    if reply_to:
        msg['Reply-To'] = reply_to

    # CAN-SPAM compliant headers
    if unsubscribe_url:
        msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'

    # Plain text fallback (improves deliverability)
    import re
    plain_text = re.sub(r'<[^>]+>', '', html_body)
    plain_text = re.sub(r'\s+', ' ', plain_text).strip()
    msg.attach(MIMEText(plain_text, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        result = service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()
    except HttpError as e:
        if e.resp.status == 401 or e.resp.status == 403:
            raise GmailAuthError(f"Gmail auth error ({e.resp.status}): {e}")
        raise

    increment_send_count()
    return result.get('id')
