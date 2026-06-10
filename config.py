import os
import sys
import secrets
from dotenv import load_dotenv

# ─── Modes & directories ───
# OSS/self-host mode (default): everything lives next to the code, as before.
# Desktop mode (CREATORCRM_DESKTOP=1, set by the Tauri shell): user data lives in
# the OS app-data folder so the app bundle stays read-only.
FROZEN = getattr(sys, 'frozen', False)  # True when running as a PyInstaller binary
DESKTOP_MODE = os.getenv('CREATORCRM_DESKTOP', '1' if FROZEN else '') == '1'

# Where bundled read-only resources live (templates/static are handled in app.py)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

if DESKTOP_MODE:
    _default_data = os.path.expanduser('~/Library/Application Support/CreatorCRM') \
        if sys.platform == 'darwin' else os.path.join(os.path.expanduser('~'), '.creatorcrm')
    DATA_DIR = os.getenv('CREATORCRM_DATA_DIR', _default_data)
else:
    DATA_DIR = os.getenv('CREATORCRM_DATA_DIR', BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

# .env: in desktop mode the user-editable env lives in DATA_DIR
load_dotenv(os.path.join(DATA_DIR, '.env'))
if not DESKTOP_MODE:
    load_dotenv()  # also pick up repo-local .env in OSS mode

# Database
DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'crm.db')}"

# ─── Gmail ───
# IMPORTANT: gmail.send is a "sensitive" Google OAuth scope. Adding any
# read/modify/compose Gmail scope moves the app into the "restricted" tier,
# which requires a paid annual CASA security assessment. Do not add them.
GOOGLE_CREDENTIALS_FILE = os.path.join(DATA_DIR, 'credentials.json') if DESKTOP_MODE \
    else os.path.join(BASE_DIR, 'credentials.json')
GOOGLE_TOKEN_FILE = os.path.join(DATA_DIR, 'token.json')
GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid',
]
# Embedded OAuth client for the desktop product (installed-app flow; the client
# secret in an installed app is not confidential by design — Google docs).
# Supplied at build time; falls back to the user's own credentials.json (OSS mode).
EMBEDDED_OAUTH_CLIENT_ID = os.getenv('CREATORCRM_OAUTH_CLIENT_ID', '')
EMBEDDED_OAUTH_CLIENT_SECRET = os.getenv('CREATORCRM_OAUTH_CLIENT_SECRET', '')

DAILY_SEND_LIMIT = 1950  # Safety margin below 2000
SENDER_NAME = os.getenv('SENDER_NAME', '')  # Display name shown in inbox

# Timezone for display (all data stored in UTC, converted for display)
DISPLAY_TIMEZONE = os.getenv('DISPLAY_TIMEZONE', 'Asia/Kolkata')

# Tracking (Cloudflare Worker)
CLOUDFLARE_WORKER_URL = os.getenv('CLOUDFLARE_WORKER_URL', '')
TRACKING_SECRET = os.getenv('TRACKING_SECRET', '')

# Multi-tenant relay (desktop product) — the product-run worker URL.
# When set (desktop builds), tracking uses tenant tokens against this relay
# instead of the user's own worker.
RELAY_URL = os.getenv('CREATORCRM_RELAY_URL', '')

# Campaign defaults
DEFAULT_BATCH_SIZE = 50
DEFAULT_BATCH_DELAY = 60  # seconds between batches

# Domain warm-up: max emails per day based on account age
WARMUP_DAILY_MAX = int(os.getenv('WARMUP_DAILY_MAX', '0'))  # 0 = use DAILY_SEND_LIMIT

# CAN-SPAM compliance: physical mailing address (REQUIRED for commercial email)
PHYSICAL_ADDRESS = os.getenv('PHYSICAL_ADDRESS', '')

# Flask — auto-generate secrets if not set
def _get_or_create_secret(env_var, filename):
    """Get secret from env, or generate and persist one (in DATA_DIR)."""
    val = os.getenv(env_var, '')
    if val:
        return val
    secret_file = os.path.join(DATA_DIR, filename)
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    val = secrets.token_hex(32)
    with open(secret_file, 'w') as f:
        f.write(val)
    return val

SECRET_KEY = _get_or_create_secret('SECRET_KEY', '.secret_key')
if not TRACKING_SECRET:
    TRACKING_SECRET = _get_or_create_secret('TRACKING_SECRET', '.tracking_secret')

# Temporary upload directory for CSV imports (avoids cookie size limit)
UPLOAD_DIR = os.path.join(DATA_DIR, '.uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
