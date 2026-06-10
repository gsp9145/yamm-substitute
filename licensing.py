"""CreatorCRM licensing — desktop product only.

Local-first by design: this module stores license/trial state in DATA_DIR and
talks only to the product relay (which validates license keys with the payment
provider). No user data leaves the machine.

States: 'oss' (self-host build — licensing not applicable), 'trial',
'trial_expired', 'active', 'invalid'.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import config

TRIAL_DAYS = 7
OFFLINE_GRACE_DAYS = 14
REVALIDATE_EVERY_DAYS = 7

STATE_FILE = os.path.join(config.DATA_DIR, 'license.json')


def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def _post_json(url, payload, headers=None, timeout=10):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', **(headers or {})},
        method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def ensure_trial_started():
    """First desktop launch starts the trial clock."""
    if not config.DESKTOP_MODE:
        return
    state = _load()
    if 'trial_started' not in state and 'license_key' not in state:
        state['trial_started'] = time.time()
        _save(state)


def activate(license_key):
    """Activate a license: register the tenant with the relay (which validates
    the key with the payment provider). Stores tenant credentials locally.
    Returns (ok, message)."""
    if not config.RELAY_URL:
        return False, 'No relay configured in this build.'
    try:
        data = _post_json(f"{config.RELAY_URL.rstrip('/')}/api/register-tenant",
                          {'license_key': license_key})
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return False, 'That license key is not valid.'
        return False, f'Activation failed (HTTP {e.code}). Try again.'
    except Exception as e:
        return False, f'Could not reach activation server: {e}'

    state = _load()
    state.update({
        'license_key': license_key,
        'tenant_id': data['tenant_id'],
        'tenant_secret': data['tenant_secret'],
        'last_validated': time.time(),
    })
    _save(state)
    return True, 'License activated.'


def revalidate_if_due():
    """Weekly background revalidation with offline grace."""
    state = _load()
    if 'license_key' not in state:
        return
    age_days = (time.time() - state.get('last_validated', 0)) / 86400
    if age_days < REVALIDATE_EVERY_DAYS:
        return
    try:
        data = _post_json(f"{config.RELAY_URL.rstrip('/')}/api/register-tenant",
                          {'license_key': state['license_key']})
        state['tenant_id'] = data['tenant_id']
        state['tenant_secret'] = data['tenant_secret']
        state['last_validated'] = time.time()
        _save(state)
    except urllib.error.HTTPError as e:
        if e.code == 403:                     # provider says: no longer valid
            state['revoked'] = True
            _save(state)
    except Exception:
        pass                                  # offline — grace period applies


def status():
    """Current licensing status for UI + gating."""
    if not config.DESKTOP_MODE:
        return {'state': 'oss'}
    state = _load()
    if state.get('license_key'):
        if state.get('revoked'):
            return {'state': 'invalid'}
        offline_days = (time.time() - state.get('last_validated', 0)) / 86400
        if offline_days > REVALIDATE_EVERY_DAYS + OFFLINE_GRACE_DAYS:
            return {'state': 'invalid'}
        return {'state': 'active'}
    started = state.get('trial_started')
    if started:
        days_left = TRIAL_DAYS - (time.time() - started) / 86400
        if days_left > 0:
            return {'state': 'trial', 'days_left': int(days_left) + 1}
        return {'state': 'trial_expired'}
    return {'state': 'trial', 'days_left': TRIAL_DAYS}


def can_send():
    """Sending is allowed on active license or live trial (OSS always)."""
    return status()['state'] in ('oss', 'trial', 'active')


def relay_credentials():
    """(tenant_id, tenant_secret) for tracking tokens, or (None, None)."""
    state = _load()
    return state.get('tenant_id'), state.get('tenant_secret')


def license_key():
    return _load().get('license_key')
