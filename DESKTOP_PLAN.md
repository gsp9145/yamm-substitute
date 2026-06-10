# CreatorCRM Desktop — Implementation Plan

**Version:** 1.0 · **Created:** 2026-06-10 · **Branch:** `full-app` (fallback: `main` @ `v0.1.0`)

## Decisions (locked)

| Decision | Choice |
|---|---|
| Architecture | **Local-first desktop app** (Tauri shell) — data + Gmail token stay on the user's machine |
| Model | **Open-core**: free OSS self-host tier (existing repo) + paid desktop app |
| Platform | **macOS only** for v1 (Windows after revenue covers the cert) |
| Price | **$5/month** subscription |
| Name | **CreatorCRM** (unchanged) |
| Payments | **Dodo Payments** (merchant of record, India-native, built-in license keys). Hedge: Polar.sh |
| Gmail scope | **`gmail.send` ONLY** — sensitive tier, no CASA audit. NEVER add readonly/modify/compose |

## Verified facts driving this plan (researched 2026-06-10)

- `gmail.send` = **sensitive** scope → verification is ~2–4 weeks, ~$0, no annual audit.
  Sources: developers.google.com/workspace/gmail/api/auth/scopes, support.google.com/cloud/answer/13464325
- **Testing-mode refresh tokens expire in 7 days** → cannot sell while unverified. Verify FIRST.
- Stripe India: invite-only (unavailable). Lemon Squeezy: PayPal-only payouts to India (~6% extra).
  Paddle: AUP bans "Mass Marketing Products" — rejection risk. **Dodo**: ~14% effective on $5,
  license API, FIRA/FIRC export docs for Indian bank payouts.
- Positioning for payment review: **"CRM for creators with email campaigns via the user's own
  Gmail"** — never "cold email tool."

---

## Phase 0 — Compliance & accounts (START IMMEDIATELY — longest lead time)

These run in parallel with development; Google verification is the critical path (~2–4 weeks).

1. **Domain + privacy policy.** Buy/choose the product domain (e.g. `creatorcrm.app`).
   Host: homepage (adapt existing `landing/`), privacy policy **on the same domain**
   (must disclose how the app accesses/uses/stores Google user data; emphasize local-only
   storage, no server-side data). Verify domain in Google Search Console.
2. **Google Cloud OAuth app** (the product's own, separate from personal dev project):
   consent screen with name/logo/support email, scope = `gmail.send` only.
   Record an unlisted YouTube **demo video** of the OAuth flow + how sending works.
   Submit brand verification (2–3 days) then sensitive-scope verification (~10 business days
   + slack). **Gate: do not launch sales until verified** (7-day token expiry in testing mode).
3. **Dodo Payments account**: sign up, then email support for product pre-approval using the
   creator-CRM framing. Set up the $5/mo product + license key config. (If rejected → Polar.sh.)
4. **Apple Developer Program** ($99/yr) — needed for code signing + notarization.

**Deliverables:** verified OAuth app, approved Dodo product, Apple membership, live privacy policy.

---

## Phase 1 — Desktop shell (Tauri + Python sidecar) — ~1–2 weeks

Reuse the entire existing Flask app; no rewrite.

1. **Bundle the backend**: PyInstaller → single `creatorcrm-backend` binary (Flask + SQLAlchemy
   + Gmail client). Strip dev-only bits (debug mode, reloader).
2. **Tauri v2 app**: spawns the backend as a **sidecar** on a free localhost port, waits for
   health check, opens the existing web UI in the native window. Kills sidecar on quit.
3. **App data directory**: move SQLite DB, token, config from repo folder to
   `~/Library/Application Support/CreatorCRM/` (env var override so OSS/self-host mode
   keeps working from the repo).
4. **One-click Google sign-in**: replace the `credentials.json` upload flow with the app's
   built-in OAuth client using the **loopback + PKCE installed-app flow** (Google's standard
   for desktop; the embedded client ID is non-secret by design). Token stored locally,
   `gmail.send` scope only.
5. **Menu-bar niceties (cheap wins):** app icon, native menu, "campaign running" state.

**Existing code reused:** `app.py`, all templates, `static/style.css`, `gmail_service.py`
(modified auth bootstrap), `campaign_sender.py`, `tracking.py`, `models.py`.

**Verification:** fresh Mac (or new user account) → install app → sign in with Google →
import CSV → send a test campaign → data exists only under Application Support.

---

## Phase 2 — Multi-tenant tracking relay — ~3–5 days

One Cloudflare Worker owned by the product (`track.creatorcrm.app`), replacing per-user setup.

**Design:**
- Tracking token: `base64({ t: tenant_id, cc: campaign_contact_id, sig: HMAC })`,
  signed with a **per-tenant secret** (derived at license activation, stored locally + in
  worker KV/D1 keyed by tenant).
- Worker endpoints: `/open/<token>` (pixel), `/click/<token>?url=…` (redirect),
  `/unsubscribe/<token>`, `/api/events?since=…` (auth: license key → returns only that
  tenant's events), `/api/register-tenant` (called once at activation, auth: license key
  validated against Dodo).
- D1 stores only `(tenant_id, cc, event_type, url?, ts)` — **no PII ever** (cc is a row
  number meaningful only in the user's local DB). Document this in the privacy policy.
- App side: `tracking.py` gains tenant-aware token minting; poller authenticates with the
  license key. OSS/self-host mode keeps the bring-your-own-worker path (config switch).

**Verification:** two licenses on two machines → events route to the correct app only;
forged tokens (wrong sig / other tenant) rejected.

---

## Phase 3 — Licensing & checkout — ~3–5 days

1. **In-app activation:** first-run screen asks for license key → Dodo License API `activate`
   (device-bound). Weekly `validate` with **14-day offline grace**; clear UI state for
   expired/cancelled.
2. **Trial:** 7-day free trial without card (local countdown + relay registration marked
   trial) — keeps the funnel cheap for creators.
3. **Checkout:** Dodo hosted checkout linked from the landing page; webhook → license key
   emailed automatically (Dodo built-in).
4. **Landing page update:** "Launch app" CTA → **"Start free trial / Buy — $5/mo"** +
   download link; add pricing FAQ entries (cancel anytime, data stays yours).

---

## Phase 4 — Package & ship — ~1 week

1. `codesign` with Developer ID cert + **notarize** the DMG (`notarytool`), staple ticket.
2. Distribute via **GitHub Releases** (free hosting); landing page download button points there.
3. README: add "Download for Mac" alongside the OSS quick-start (open-core split made explicit:
   OSS = bring your own Google credentials + worker; paid = one-click everything).
4. **First-10-users push:** sell to warm network (creator/marketing contacts), watch onboarding
   stumbles, fix the top 3.

**Launch gate checklist:** Google verification approved · Dodo product approved · notarized
DMG installs cleanly on a fresh Mac · trial→paid→activation→tracking works end-to-end ·
privacy policy live.

---

## Phase 5 — Post-validation (only after real users)

- Tauri auto-updater (signed updates from GitHub Releases)
- Onboarding wizard polish for non-technical creators; in-app deliverability guidance (SPF/DKIM)
- Windows build (cert ~$100–300/yr) when revenue justifies
- Optional: Razorpay/UPI add-on for Indian customers later; community (Discord), docs site

---

## Costs & unit economics

| Item | Cost |
|---|---|
| Apple Developer | $99/yr |
| Domain | ~$15/yr |
| Cloudflare relay | ~$0 (free tier) → $5/mo at scale |
| Google verification | $0 |
| Dodo fees | ~14% of revenue (~$0.70 per $5) |
| **Net per subscriber** | **~$4.30/mo** · break-even ≈ 3 subscribers |

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Google verification rejected/slow | Local-only data story + send-only scope is the strongest possible case; start Phase 0 first; can develop in testing mode meanwhile |
| Dodo rejects product | Pre-approval email before integrating; Polar.sh fallback (same MoR + licensing model) |
| Scope creep into restricted tier | Hard rule in this doc + PLAN.md: `gmail.send` only |
| Gmail spam/abuse by users | Keep warm-up limits, daily caps, unsubscribe links mandatory (already built); AUP in ToS |
| PyInstaller/Tauri sidecar fragility | Spike this first in Phase 1 (it's the only novel technical piece); fallback = Electron + bundled Python, same architecture |
| Dodo is a young company | Low payout threshold; abstract the license-check behind one module so swapping MoR is cheap |
