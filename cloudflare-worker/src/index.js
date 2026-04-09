/**
 * CreatorCRM Tracking Worker
 *
 * Handles:
 * - GET /open/<token>       → log open event, return 1x1 transparent GIF
 * - GET /click/<token>?url= → log click event, redirect to original URL
 * - GET /api/events?since=  → return events since timestamp (for Flask polling)
 * - DELETE /api/events?before= → cleanup processed events
 *
 * D1 Schema (run once via wrangler d1 execute):
 *   CREATE TABLE IF NOT EXISTS events (
 *     id INTEGER PRIMARY KEY AUTOINCREMENT,
 *     campaign_contact_id INTEGER NOT NULL,
 *     event_type TEXT NOT NULL,
 *     url TEXT,
 *     ip_address TEXT,
 *     user_agent TEXT,
 *     created_at TEXT DEFAULT (datetime('now'))
 *   );
 */

// 1x1 transparent GIF (43 bytes)
const TRANSPARENT_GIF = new Uint8Array([
  0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00,
  0x80, 0x00, 0x00, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x21,
  0xf9, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x00, 0x00,
  0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44,
  0x01, 0x00, 0x3b,
]);

function decodeToken(token) {
  try {
    const json = atob(token.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ─── CORS headers for API endpoints ───
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Tracking-Secret',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // ─── Open Tracking ───
    if (path.startsWith('/open/')) {
      const token = path.split('/open/')[1];
      const payload = decodeToken(token);

      if (payload && payload.cc_id) {
        try {
          await env.DB.prepare(
            'INSERT INTO events (campaign_contact_id, event_type, ip_address, user_agent) VALUES (?, ?, ?, ?)'
          )
            .bind(
              payload.cc_id,
              'open',
              request.headers.get('CF-Connecting-IP') || '',
              request.headers.get('User-Agent') || ''
            )
            .run();
        } catch (e) {
          console.error('Failed to log open event:', e);
        }
      }

      return new Response(TRANSPARENT_GIF, {
        headers: {
          'Content-Type': 'image/gif',
          'Cache-Control': 'no-store, no-cache, must-revalidate',
        },
      });
    }

    // ─── Click Tracking ───
    if (path.startsWith('/click/')) {
      const token = path.split('/click/')[1];
      const payload = decodeToken(token);
      const encodedUrl = url.searchParams.get('url');

      let originalUrl = 'https://example.com';
      try {
        originalUrl = atob(encodedUrl.replace(/-/g, '+').replace(/_/g, '/'));
      } catch {
        // fallback
      }

      if (payload && payload.cc_id) {
        try {
          await env.DB.prepare(
            'INSERT INTO events (campaign_contact_id, event_type, url, ip_address, user_agent) VALUES (?, ?, ?, ?, ?)'
          )
            .bind(
              payload.cc_id,
              'click',
              originalUrl,
              request.headers.get('CF-Connecting-IP') || '',
              request.headers.get('User-Agent') || ''
            )
            .run();
        } catch (e) {
          console.error('Failed to log click event:', e);
        }
      }

      return Response.redirect(originalUrl, 302);
    }

    // ─── Unsubscribe Handler ───
    if (path.startsWith('/unsubscribe/')) {
      const token = path.split('/unsubscribe/')[1];
      const payload = decodeToken(token);

      if (payload && payload.cc_id) {
        try {
          await env.DB.prepare(
            'INSERT INTO events (campaign_contact_id, event_type, ip_address, user_agent) VALUES (?, ?, ?, ?)'
          )
            .bind(
              payload.cc_id,
              'unsubscribe',
              request.headers.get('CF-Connecting-IP') || '',
              request.headers.get('User-Agent') || ''
            )
            .run();
        } catch (e) {
          console.error('Failed to log unsubscribe event:', e);
        }
      }

      // Show a simple confirmation page
      const html = `<!DOCTYPE html>
<html>
<head><title>Unsubscribed</title>
<style>
  body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; margin: 0; background: #f5f5f5; }
  .card { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);
          text-align: center; max-width: 400px; }
  h2 { color: #333; }
  p { color: #666; }
</style>
</head>
<body>
  <div class="card">
    <h2>You've been unsubscribed</h2>
    <p>You will no longer receive emails from us. This may take up to 24 hours to take effect.</p>
  </div>
</body>
</html>`;
      return new Response(html, {
        headers: { 'Content-Type': 'text/html' },
      });
    }

    // ─── API: Get Events (for Flask polling) ───
    if (path === '/api/events' && request.method === 'GET') {
      const secret = url.searchParams.get('secret');
      if (secret !== env.TRACKING_SECRET) {
        return new Response(JSON.stringify({ error: 'unauthorized' }), {
          status: 403,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }

      const since = url.searchParams.get('since') || '2000-01-01';
      const limit = parseInt(url.searchParams.get('limit') || '1000');

      const result = await env.DB.prepare(
        'SELECT * FROM events WHERE created_at > ? ORDER BY id ASC LIMIT ?'
      )
        .bind(since, limit)
        .all();

      return new Response(JSON.stringify(result.results || []), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ─── API: Delete Events (cleanup after polling) ───
    if (path === '/api/events' && request.method === 'DELETE') {
      const secret = url.searchParams.get('secret');
      if (secret !== env.TRACKING_SECRET) {
        return new Response(JSON.stringify({ error: 'unauthorized' }), {
          status: 403,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        });
      }

      const before = url.searchParams.get('before');
      if (before) {
        await env.DB.prepare('DELETE FROM events WHERE created_at <= ?').bind(before).run();
      }

      return new Response(JSON.stringify({ status: 'ok' }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    // ─── Health Check ───
    if (path === '/health') {
      return new Response(JSON.stringify({ status: 'ok', service: 'crm-tracker' }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    return new Response('CreatorCRM Tracker', { status: 200 });
  },
};
