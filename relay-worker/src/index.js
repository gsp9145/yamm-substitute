/**
 * CreatorCRM Relay — multi-tenant tracking worker (paid desktop product).
 *
 * One deployment serves all customers. Privacy: the relay only ever sees
 * (tenant_id, cc, event_type, url?, ts) — cc is a row number meaningful only
 * inside the user's local database. No contacts, names, or content.
 *
 * Endpoints:
 *   POST /api/register-tenant   {license_key} → {tenant_id, tenant_secret}
 *   GET  /open/<token>          → log open, return 1x1 GIF
 *   GET  /click/<token>?url=    → log click, redirect
 *   GET  /unsubscribe/<token>   → log unsubscribe, confirmation page
 *   GET  /api/events?since=     → tenant's events (auth: X-License-Key)
 *   DELETE /api/events?before=  → cleanup tenant's events (auth: X-License-Key)
 *
 * Token format: base64url({t, cc, sig}) — sig = HMAC-SHA256(tenant_secret, `${t}:${cc}`)[:16hex]
 *
 * D1 schema (schema.sql):
 *   tenants(tenant_id TEXT PRIMARY KEY, secret TEXT NOT NULL,
 *           license_hash TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT (datetime('now')));
 *   events(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
 *          cc INTEGER NOT NULL, event_type TEXT NOT NULL, url TEXT,
 *          created_at TEXT DEFAULT (datetime('now')));
 *   CREATE INDEX idx_events_tenant ON events(tenant_id, created_at);
 *
 * Env vars:
 *   DODO_API_KEY            — when set, license keys are validated against Dodo Payments
 *   ALLOW_UNVERIFIED_TENANTS — "1" to accept any license key (dev / pre-launch only)
 */

const GIF = new Uint8Array([
  0x47,0x49,0x46,0x38,0x39,0x61,0x01,0x00,0x01,0x00,0x80,0x00,0x00,0xff,0xff,
  0xff,0x00,0x00,0x00,0x21,0xf9,0x04,0x01,0x00,0x00,0x00,0x00,0x2c,0x00,0x00,
  0x00,0x00,0x01,0x00,0x01,0x00,0x00,0x02,0x02,0x44,0x01,0x00,0x3b,
]);

const enc = new TextEncoder();

async function hmacHex(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function sha256Hex(msg) {
  const d = await crypto.subtle.digest('SHA-256', enc.encode(msg));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function b64uDecode(s) {
  return atob(s.replace(/-/g, '+').replace(/_/g, '/'));
}

function decodeToken(token) {
  try { return JSON.parse(b64uDecode(token)); } catch { return null; }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}

/** Decode token, fetch tenant secret, verify signature. Returns {t, cc} or null. */
async function verifyToken(env, token) {
  const p = decodeToken(token);
  if (!p || !p.t || !p.cc || !p.sig) return null;
  const row = await env.DB.prepare('SELECT secret FROM tenants WHERE tenant_id = ?')
    .bind(p.t).first();
  if (!row) return null;
  const expected = (await hmacHex(row.secret, `${p.t}:${p.cc}`)).slice(0, 16);
  return expected === p.sig ? p : null;
}

/** Resolve tenant by license key (X-License-Key header). */
async function tenantFromLicense(env, request) {
  const key = request.headers.get('X-License-Key');
  if (!key) return null;
  const hash = await sha256Hex(key);
  return env.DB.prepare('SELECT tenant_id FROM tenants WHERE license_hash = ?')
    .bind(hash).first();
}

/** Validate a license key with the payment provider (Dodo), or allow-all in dev. */
async function licenseIsValid(env, licenseKey) {
  if (env.DODO_API_KEY) {
    const resp = await fetch('https://live.dodopayments.com/licenses/validate', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${env.DODO_API_KEY}`,
                 'Content-Type': 'application/json' },
      body: JSON.stringify({ license_key: licenseKey }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    return data.valid === true;
  }
  return env.ALLOW_UNVERIFIED_TENANTS === '1';
}

async function logEvent(env, p, type, url = null) {
  await env.DB.prepare(
    'INSERT INTO events (tenant_id, cc, event_type, url) VALUES (?, ?, ?, ?)'
  ).bind(p.t, p.cc, type, url).run();
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ─── Tenant registration (called once at license activation) ───
    if (path === '/api/register-tenant' && request.method === 'POST') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad request' }, 400); }
      const licenseKey = body.license_key;
      if (!licenseKey) return json({ error: 'license_key required' }, 400);
      if (!(await licenseIsValid(env, licenseKey))) {
        return json({ error: 'invalid license' }, 403);
      }
      const hash = await sha256Hex(licenseKey);
      const existing = await env.DB.prepare(
        'SELECT tenant_id, secret FROM tenants WHERE license_hash = ?').bind(hash).first();
      if (existing) {
        return json({ tenant_id: existing.tenant_id, tenant_secret: existing.secret });
      }
      const tenantId = (await sha256Hex(`tenant:${hash}`)).slice(0, 12);
      const secret = crypto.randomUUID().replace(/-/g, '') +
                     crypto.randomUUID().replace(/-/g, '');
      await env.DB.prepare(
        'INSERT INTO tenants (tenant_id, secret, license_hash) VALUES (?, ?, ?)'
      ).bind(tenantId, secret, hash).run();
      return json({ tenant_id: tenantId, tenant_secret: secret });
    }

    // ─── Open tracking ───
    if (path.startsWith('/open/')) {
      const p = await verifyToken(env, path.slice('/open/'.length));
      if (p) { try { await logEvent(env, p, 'open'); } catch (e) { console.error(e); } }
      return new Response(GIF, {
        headers: { 'Content-Type': 'image/gif',
                   'Cache-Control': 'no-store, no-cache, must-revalidate' },
      });
    }

    // ─── Click tracking ───
    if (path.startsWith('/click/')) {
      const p = await verifyToken(env, path.slice('/click/'.length));
      let target = 'https://example.com';
      try { target = b64uDecode(url.searchParams.get('url') || ''); } catch {}
      if (!/^https?:\/\//.test(target)) target = 'https://example.com';
      if (p) { try { await logEvent(env, p, 'click', target); } catch (e) { console.error(e); } }
      return Response.redirect(target, 302);
    }

    // ─── Unsubscribe ───
    if (path.startsWith('/unsubscribe/')) {
      const p = await verifyToken(env, path.slice('/unsubscribe/'.length));
      if (p) { try { await logEvent(env, p, 'unsubscribe'); } catch (e) { console.error(e); } }
      return new Response(
        `<!DOCTYPE html><html><head><title>Unsubscribed</title></head>
<body style="font-family:-apple-system,Arial,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#F6F2EA;color:#1C1A16">
<div style="background:#fff;padding:40px;border-radius:12px;max-width:420px;text-align:center;box-shadow:0 2px 12px rgba(0,0,0,.08)">
<h2>You've been unsubscribed</h2><p style="color:#666">You will no longer receive these emails. This may take up to 24 hours to take effect.</p>
</div></body></html>`,
        { headers: { 'Content-Type': 'text/html' } });
    }

    // ─── Events API (per-tenant, license-key auth) ───
    if (path === '/api/events') {
      const tenant = await tenantFromLicense(env, request);
      if (!tenant) return json({ error: 'unauthorized' }, 403);

      if (request.method === 'GET') {
        const since = url.searchParams.get('since') || '2000-01-01';
        const limit = Math.min(parseInt(url.searchParams.get('limit') || '1000'), 5000);
        const result = await env.DB.prepare(
          `SELECT id, cc AS campaign_contact_id, event_type, url, created_at
           FROM events WHERE tenant_id = ? AND created_at > ? ORDER BY id ASC LIMIT ?`
        ).bind(tenant.tenant_id, since, limit).all();
        return json(result.results || []);
      }
      if (request.method === 'DELETE') {
        const before = url.searchParams.get('before');
        if (before) {
          await env.DB.prepare('DELETE FROM events WHERE tenant_id = ? AND created_at <= ?')
            .bind(tenant.tenant_id, before).run();
        }
        return json({ status: 'ok' });
      }
    }

    if (path === '/health') return json({ status: 'ok', service: 'creatorcrm-relay' });
    return new Response('CreatorCRM Relay', { status: 200 });
  },
};
