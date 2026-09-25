# Spec: Analytics bot classification & engagement-gated humans

**Status:** Draft for the Journal Watch **application** repo (Django). The
analytics middleware and `analytics_analyticsevent` model live there, not in
this infra repo. This document is the implementation brief.

**Author context:** Written after a production audit (2026-06-22) that
cross-checked the app's analytics classifier against Cloudflare Web Analytics.

---

## 1. Problem

The current classifier massively over-counts humans, and writes a DB row +
creates a session for every hit (including bots). Evidence (last 30 days unless
noted):

| Signal | Value | Note |
|---|---|---|
| Distinct `visitor_id` (raw) | ~51,500 | cookieless bots mint a fresh `jwvid` per hit |
| Classified **not automated** | ~1,645 | the dashboard's "humans" |
| **Engagement-gated** humans (scroll≥50% or interaction) | **~482** | the real floor |
| Cloudflare total visits | 2,260 | edge, JS-beacon based |
| Cloudflare from **Singapore + China** | **1,760 (78%)** | datacenter / bot geographies |
| Cloudflare from AU+NZ+US | ~340 | human geographies |
| Active matched subscribers (90d) | 143 | confirmed real people |

Two concrete classifier leaks found:
- **`Gatus/1.0`** (an uptime monitor) classified as *human* — 75 "visitors".
- A **Windows/Chrome user-agent monoculture** (`Windows NT 10.0; Win64; x64`
  Chrome, hundreds of visitors) matching Cloudflare's Singapore/China traffic —
  i.e. **headless bots that execute JavaScript**, so `js_verified` alone is not
  a human signal.

### Why this matters (three problems, one root cause)
1. **Wrong numbers** — reported humans are ~3× inflated.
2. **DB bloat** — a row per bot hit grew `analytics_analyticsevent` to ~95k
   rows and inflated `django_session` (now 58 MB → truncated; sessions are in
   Redis). See the existing `analytics.0022_schedule_prune_automated_events`.
3. **No edge caching / slow for real users** — setting `jwvid`/`sessionid` +
   `Vary: Cookie` on every response forces `cf-cache-status: DYNAMIC`, so every
   page round-trips to the LA origin (~+150 ms for ANZ users). Fixing the
   cookie-on-every-bot-hit behaviour is the **same change** that unlocks edge
   caching.

---

## 2. Goals / non-goals

**Goals**
- Classify automated traffic accurately using **network + UA + behaviour**, not
  JS execution alone.
- Stop writing analytics rows and creating sessions for classified bots.
- Define and report **"engaged humans"** as the headline KPI.
- Stop emitting per-user cookies / `Vary: Cookie` on cacheable anonymous pages.

**Non-goals**
- Blocking bots (that's Cloudflare's job — see §7). We only need to *not count*
  and *not persist* them.
- Perfect classification. Aim: app "engaged humans" within ~±20% of Cloudflare
  human-geography visits.

---

## 3. Classification design (layered, cheap → strong)

Evaluate in order; first decisive layer wins. Store the deciding reason.

### Layer 0 — User-agent hard signals (deterministic)
Mark `automated` if the UA:
- is empty, or matches a **monitor/library** pattern:
  `gatus`, `uptime-kuma`, `pingdom`, `betteruptime`, `curl`, `wget`,
  `python-requests`, `httpx`, `aiohttp`, `go-http-client`, `okhttp`, `java/`,
  `node-fetch`, `axios`, `headlesschrome`, `phantomjs`, `puppeteer`, `playwright`,
  `bot`, `crawler`, `spider`, `scrapy`, `slurp`, `bingpreview`.
- Keep this list in settings; it's the cheapest, highest-precision filter.
  (`Gatus/1.0` would have been caught here.)

### Layer 1 — Network signals (strong; needs the real client IP)
The real IP is `CF-Connecting-IP` (Cloudflare). Country is `CF-IPCountry`.
- **Datacenter/hosting ASN ⇒ automated.** Look up ASN from the real IP using a
  local **MaxMind GeoLite2-ASN** DB (free, shipped in the image, refreshed
  monthly). Maintain a small allowlist of "eyeball" exceptions if needed; flag
  the rest of known hosting ASNs (AWS, GCP, Azure, Alibaba, OVH, Hetzner,
  DigitalOcean, Linode, Tencent, etc.) as automated. **This is the single most
  effective signal** — it catches the JS-executing Singapore/China fleet that
  `js_verified` misses.
- Optionally weight by **country**: `CF-IPCountry` in a high-bot set
  (SG, CN, VN, …) is *suspicious* but **not decisive** on its own (there are
  real expat/international readers) — use it only to raise confidence thresholds,
  never to hard-classify.
- If Cloudflare **Bot Management** score is available (`cf.bot_management.score`,
  Enterprise) or a Worker-injected `cf.client.bot`, prefer it over ASN guessing.
  On the free/Pro plan, rely on ASN + UA.

### Layer 2 — Behavioural / engagement gate (defines "human-confirmed")
A visitor is only **`engaged_human`** once they emit a real signal in the
session:
- `scroll_depth >= 50`, **or**
- any of: `review_engaged`, `search`, `search_result_click`,
  `review_full_text_click`, `review_share_*`, `cpd_tracking_toggle`,
  `journal_select`, **or**
- `duration_ms` above a threshold (e.g. ≥ 10 s), **or**
- `subscriber_id IS NOT NULL` (matched subscriber — always human).

`human_confidence` ladder: `bot` → `suspected_bot` → `probable_human`
→ `engaged_human` → `known_subscriber_human`.

---

## 4. Write-path & cookie changes

### 4.1 Don't persist bots
- If Layer 0/1 ⇒ `automated`, **do not write** an `AnalyticsEvent` row and **do
  not create a Django session**. (Optionally keep a cheap counter — e.g. a Redis
  `INCR` per day per reason — so totals are still observable without 50k rows.)
- This directly cuts the DB growth and removes the per-bot session churn.

### 4.2 Don't mint per-user cookies on cacheable anonymous pages
- Only set the `jwvid` analytics cookie **after** a human/engagement signal, or
  set it from a dedicated **JS beacon endpoint** (e.g. `POST /track`) rather than
  on every HTML response. Cookieless bots then stop minting fresh `jwvid`s.
- Do **not** create a `sessionid` for anonymous GETs.
- For anonymous content responses, send
  `Cache-Control: public, s-maxage=120, stale-while-revalidate=600` and **drop
  `Vary: Cookie`**. (CSRF: have htmx fetch the token from a small *uncached*
  endpoint, or `@csrf_exempt` the public newsletter POST with its own bot
  protection — so cached HTML carries no per-user token.)
- This is the prerequisite for the parked **edge-caching** work; doing it here
  fixes analytics *and* latency together.

---

## 5. Data model / reporting

- Add a boolean `engaged_human` (or derive at query time) so the dashboard can
  filter cheaply. Backfill is optional.
- Keep the existing `analytics.0022` prune for automated events; consider a
  shorter retention (e.g. 30–90 days) for `automated=true` rows, or don't store
  them at all per §4.1.
- **Canonical KPI query — "engaged humans" (use this everywhere instead of
  `NOT automated`):**
  ```sql
  SELECT count(DISTINCT visitor_id)
  FROM analytics_analyticsevent
  WHERE NOT automated
    AND timestamp >= now() - interval '30 days'
    AND visitor_id IN (
      SELECT visitor_id FROM analytics_analyticsevent
      WHERE timestamp >= now() - interval '30 days'
        AND (scroll_depth >= 50
             OR subscriber_id IS NOT NULL
             OR event_type IN ('review_engaged','search','search_result_click',
                  'review_full_text_click','review_share_copy_link',
                  'review_share_native','review_share_email',
                  'cpd_tracking_toggle','journal_select'))
    );
  ```
- Default dashboards to **AU + NZ + known international**; keep an "all
  geographies" toggle for transparency.

---

## 6. Cloudflare-side enablers (config, not code)

- **Trust Cloudflare IPs** in Django so `CF-Connecting-IP` is the real client
  (e.g. via a restored-IP middleware that only trusts CF's published ranges;
  the origin only accepts traffic from Cloudflare anyway).
- Enable the **"Add visitor location headers" Managed Transform** (free) for
  `CF-IPCountry` etc.
- Enable **Bot Fight Mode** (free) / **Super Bot Fight Mode** (Pro) to
  challenge/drop automated traffic *before* it reaches the origin — fewer bots
  to classify, less origin load, and it helps the latency/caching story too.
- (Optional) A WAF/Cache rule to bypass/booby-trap the analytics beacon for
  obvious datacenter ASNs.

---

## 7. Rollout

1. **Shadow mode** — compute the new `automated` decision and store the reason,
   but keep counting/writing as today. Run ~1–2 weeks.
2. **Compare** — the new "engaged humans" should land near Cloudflare's
   human-geography visits (~few hundred/month). Confirm `Gatus` and the
   SG/CN Windows-Chrome fleet now flag `automated`.
3. **Enforce** — switch dashboards to the engaged-human KPI; enable §4.1
   write-skipping; ship the §4.2 cookie/cache-header changes.
4. **Then** the parked edge-caching Cloudflare Cache Rule can go live.

---

## 8. Acceptance criteria

- `Gatus/1.0` and library/monitor UAs classify `automated`. ✔
- Traffic from datacenter ASNs (incl. the SG/CN fleet) classifies `automated`. ✔
- Reported "engaged humans" (30d) within ~±20% of Cloudflare AU+NZ+US visits. ✔
- No `AnalyticsEvent` row or Django session created for classified bots. ✔
- Anonymous content responses carry **no** `Set-Cookie` and **no** `Vary:
  Cookie`, and a `public, s-maxage` cache header. ✔
- `analytics_analyticsevent` row growth rate drops materially (bots no longer
  persisted). ✔

---

## 9. Risks & edge cases

- **GeoLite2-ASN staleness** — refresh monthly; ASN→datacenter mapping isn't
  exhaustive. Pair with UA + behaviour so no single layer is load-bearing.
- **Real readers behind VPN/datacenter IPs** (e.g. corporate/hospital proxies,
  Apple Private Relay) — the **engagement gate rescues them**: if they scroll or
  interact, they count regardless of ASN. Keep ASN as "suspected", let
  behaviour confirm.
- **CSRF on cached pages** — must be handled (uncached token endpoint or
  exempting the public newsletter POST) before enabling caching, or anonymous
  form submits break.
- **Don't over-block** — classification here only affects *counting/persistence*,
  not access; blocking stays at Cloudflare where it's reversible.
