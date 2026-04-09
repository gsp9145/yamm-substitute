import base64
import hashlib
import hmac
import json
import re
import urllib.parse
from bs4 import BeautifulSoup
import config


def generate_tracking_token(campaign_contact_id):
    """Generate a signed tracking token for a campaign_contact."""
    cc_id = str(campaign_contact_id)
    signature = hmac.new(
        config.TRACKING_SECRET.encode(),
        cc_id.encode(),
        hashlib.sha256
    ).hexdigest()[:16]

    payload = json.dumps({'cc_id': campaign_contact_id, 'sig': signature})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def verify_tracking_token(token):
    """Verify and decode a tracking token. Returns campaign_contact_id or None."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token).decode())
        cc_id = str(payload['cc_id'])
        expected_sig = hmac.new(
            config.TRACKING_SECRET.encode(),
            cc_id.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        if hmac.compare_digest(payload['sig'], expected_sig):
            return payload['cc_id']
    except Exception:
        pass
    return None


def inject_tracking(html_body, campaign_contact_id):
    """Add open tracking pixel and rewrite links for click tracking.

    Returns the modified HTML body.
    If CLOUDFLARE_WORKER_URL is not configured, returns the original HTML unchanged.
    """
    worker_url = config.CLOUDFLARE_WORKER_URL
    if not worker_url:
        return html_body

    worker_url = worker_url.rstrip('/')
    token = generate_tracking_token(campaign_contact_id)

    # 1. Rewrite links for click tracking
    soup = BeautifulSoup(html_body, 'html.parser')
    for a_tag in soup.find_all('a', href=True):
        original_url = a_tag['href']
        if original_url.startswith(('http://', 'https://')) and worker_url not in original_url:
            encoded_url = base64.urlsafe_b64encode(original_url.encode()).decode()
            tracked_url = f"{worker_url}/click/{token}?url={encoded_url}"
            a_tag['href'] = tracked_url

    # 2. Append open tracking pixel
    pixel_url = f"{worker_url}/open/{token}"
    pixel_tag = soup.new_tag('img', src=pixel_url, width='1', height='1',
                              style='display:none;', alt='')

    body_tag = soup.find('body')
    if body_tag:
        body_tag.append(pixel_tag)
    else:
        soup.append(pixel_tag)

    return str(soup)


def personalize(template_str, contact):
    """Replace template variables with contact data."""
    replacements = {
        '{first_name}': contact.first_name or '',
        '{last_name}': contact.last_name or '',
        '{company}': contact.company or '',
        '{title}': contact.title or '',
        '{email}': contact.email or '',
    }

    result = template_str
    for var, val in replacements.items():
        result = result.replace(var, val)

    return result
