# CreatorCRM

A free, self-hosted email outreach CRM. Send up to 2,000 emails/day via Gmail API with open and click tracking — no third-party fees.

## Features

- **Contact Management** — Import from CSV, tag, search, filter, bulk actions
- **Email Templates** — HTML editor with live preview, starter templates, personalization variables
- **Campaign Management** — Batch sending, pause/resume, tag filtering, exclude previous campaigns
- **Open & Click Tracking** — Via Cloudflare Workers (free tier), 1-minute polling
- **Dashboard** — Sent/open/click stats, daily sending progress, campaign overview
- **Communication History** — Full email history per contact with rendered preview

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/creator-crm.git
cd creator-crm
pip install -r requirements.txt
cp .env.example .env
```

### 2. Set up Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable the **Gmail API**
4. Go to **APIs & Services > Credentials > Create OAuth Client ID** (Desktop app)
5. Download the JSON and save as `credentials.json` in this folder

### 3. Configure

Edit `.env` with your sender name and timezone.

### 4. Run

```bash
python app.py
```

Open **http://localhost:5050** — click **Settings > Connect Gmail** to authorize.

### 5. (Optional) Set up tracking

Deploy the Cloudflare Worker for open/click tracking:

```bash
npm install -g wrangler
cd cloudflare-worker
wrangler login
wrangler d1 create crm-tracking
# Update database_id in wrangler.toml
wrangler d1 execute crm-tracking --remote --command "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_contact_id INTEGER NOT NULL, event_type TEXT NOT NULL, url TEXT, ip_address TEXT, user_agent TEXT, created_at TEXT DEFAULT (datetime('now')));"
wrangler deploy
```

Copy the Worker URL to `CLOUDFLARE_WORKER_URL` in `.env`.

## Email Deliverability

Before sending campaigns, set up DNS records for your domain:

- **SPF** — TXT record: `v=spf1 include:_spf.google.com ~all`
- **DKIM** — Enable in Google Workspace Admin > Gmail > Authenticate email
- **DMARC** — TXT record: `v=DMARC1; p=none; rua=mailto:dmarc@yourdomain.com`

Start with the warm-up schedule (20 emails/day, increase weekly).

## Tech Stack

- **Backend:** Flask + SQLAlchemy + SQLite
- **Frontend:** Jinja2 + Bootstrap 5
- **Email:** Gmail API with OAuth2
- **Tracking:** Cloudflare Workers + D1
- **Cost:** $0

## License

MIT
