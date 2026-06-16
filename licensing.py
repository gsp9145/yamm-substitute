"""CreatorCRM licensing — desktop product only.

Local-first by design: license state lives in DATA_DIR and is validated directly
against Dodo Payments' PUBLIC license endpoints (/licenses/activate,
/licenses/validate) — no secret API key, no backend/relay involved. A license key
is an unguessable secret, so the public validate endpoint is safe to call from the
client and can't be forged.

Paid mode is enabled only when a Dodo payment link is configured (config.PAID_MODE);
otherwise the app runs free/ungated (current beta).

States: 'oss' (self-host build), 'free' (beta, ungated), 'trial', 'trial_expired',
'active', 'invalid'.
"""
import json
import os
import socket
import time
import urllib.request
import urllib.error

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


def _device_name():
    try:
        return socket.gethostname() or 'CreatorCRM device'
    except Exception:
        return 'CreatorCRM device'


def _post_json(path, payload, timeout=12):
    url = f"{config.DODO_API_BASE.rstrip('/')}{path}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        # A real User-Agent is REQUIRED — Dodo is behind Cloudflare, which blocks
        # the default Python-urllib UA with a 403 (error 1010).
        headers={'Content-Type': 'application/json',
                 'Accept': 'application/json',
                 'User-Agent': f'CreatorCRM/{config.APP_VERSION}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _validate_key(license_key):
    """Call Dodo's public validate endpoint. Returns True/False."""
    try:
        data = _post_json('/licenses/validate', {'license_key': license_key})
        return bool(data.get('valid'))
    except urllib.error.HTTPError as e:
        if e.code in (400, 403, 404):
            return False
        raise


def ensure_trial_started():
    """Start the trial clock on first launch — only in paid mode. Free builds don't gate."""
    if not config.DESKTOP_MODE or not config.PAID_MODE:
        return
    state = _load()
    if 'trial_started' not in state and 'license_key' not in state:
        state['trial_started'] = time.time()
        _save(state)


def activate(license_key):
    """Activate a license key against Dodo's public API and store it locally.
    Returns (ok, message)."""
    license_key = (license_key or '').strip()
    if not config.PAID_MODE:
        return False, 'Billing is not enabled in this build.'
    if not license_key:
        return False, 'No license key provided.'

    state = _load()
    # Already activated on this device → just re-validate (avoid burning an activation slot).
    if state.get('license_key') == license_key and state.get('instance_id'):
        try:
            if _validate_key(license_key):
                state['last_validated'] = time.time()
                state.pop('revoked', None)
                _save(state)
                return True, 'Your subscription is active.'
            return False, 'This license is no longer valid.'
        except Exception as e:
            return False, f'Could not reach the licensing server: {e}'

    try:
        data = _post_json('/licenses/activate',
                          {'license_key': license_key, 'name': _device_name()})
    except urllib.error.HTTPError as e:
        if e.code in (400, 403, 404, 409, 422):
            return False, 'That license key is invalid, expired, or has no activations left.'
        return False, f'Activation failed (HTTP {e.code}). Please try again.'
    except Exception as e:
        return False, f'Could not reach the licensing server: {e}'

    state.update({
        'license_key': license_key,
        'instance_id': data.get('id') or data.get('instance_id') or data.get('license_key_instance_id'),
        'last_validated': time.time(),
    })
    state.pop('revoked', None)
    state.pop('trial_started', None)
    _save(state)
    return True, 'Subscription activated — thank you!'


def revalidate_if_due():
    """Weekly background revalidation; marks invalid if the subscription ended.
    Offline failures are ignored (offline grace window in status())."""
    if not config.PAID_MODE:
        return
    state = _load()
    if not state.get('license_key'):
        return
    if (time.time() - state.get('last_validated', 0)) / 86400 < REVALIDATE_EVERY_DAYS:
        return
    try:
        if _validate_key(state['license_key']):
            state['last_validated'] = time.time()
            state.pop('revoked', None)
        else:
            state['revoked'] = True
        _save(state)
    except Exception:
        pass  # offline — grace period applies


def status():
    """Current licensing status for UI + send gating."""
    if not config.DESKTOP_MODE:
        return {'state': 'oss'}
    if not config.PAID_MODE:
        return {'state': 'free'}
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
    """Sending allowed for OSS, free builds, an active license, or a live trial."""
    return status()['state'] in ('oss', 'free', 'trial', 'active')


def relay_credentials():
    """(tenant_id, tenant_secret) for the tracking relay — stubbed until the relay
    is deployed. Kept so tracking.py imports cleanly."""
    return None, None


def license_key():
    return _load().get('license_key')
