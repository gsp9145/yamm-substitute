-- CreatorCRM Relay — D1 schema
-- Apply: wrangler d1 execute creatorcrm-relay --remote --file schema.sql
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id   TEXT PRIMARY KEY,
  secret      TEXT NOT NULL,
  license_hash TEXT UNIQUE NOT NULL,
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   TEXT NOT NULL,
  cc          INTEGER NOT NULL,
  event_type  TEXT NOT NULL,
  url         TEXT,
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant_id, created_at);
