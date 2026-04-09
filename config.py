import os
import secrets
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Database
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'crm.db')}"

# Gmail
GOOGLE_CREDENTIALS_FILE = os.path.join(BASE_DIR, 'credentials.json')
GOOGLE_TOKEN_FILE = os.path.join(BASE_DIR, 'token.json')
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.send',
                'https://www.googleapis.com/auth/gmail.readonly']
DAILY_SEND_LIMIT = 1950  # Safety margin below 2000
SENDER_NAME = os.getenv('SENDER_NAME', '')  # Display name shown in inbox

# Timezone for display (all data stored in UTC, converted for display)
# Set to your timezone offset string, e.g., "Asia/Kolkata" for IST
DISPLAY_TIMEZONE = os.getenv('DISPLAY_TIMEZONE', 'Asia/Kolkata')

# Tracking (Cloudflare Worker)
CLOUDFLARE_WORKER_URL = os.getenv('CLOUDFLARE_WORKER_URL', '')
TRACKING_SECRET = os.getenv('TRACKING_SECRET', '')

# Campaign defaults
DEFAULT_BATCH_SIZE = 50
DEFAULT_BATCH_DELAY = 60  # seconds between batches

# Domain warm-up: max emails per day based on account age
# Override with WARMUP_DAILY_MAX env var, or set to 0 to disable warm-up limits
WARMUP_DAILY_MAX = int(os.getenv('WARMUP_DAILY_MAX', '0'))  # 0 = use DAILY_SEND_LIMIT

# CAN-SPAM compliance: physical mailing address (REQUIRED for commercial email)
PHYSICAL_ADDRESS = os.getenv('PHYSICAL_ADDRESS', '')

# Flask — auto-generate secrets if not set
def _get_or_create_secret(env_var, filename):
    """Get secret from env, or generate and persist one."""
    val = os.getenv(env_var, '')
    if val:
        return val
    secret_file = os.path.join(BASE_DIR, filename)
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
UPLOAD_DIR = os.path.join(BASE_DIR, '.uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
