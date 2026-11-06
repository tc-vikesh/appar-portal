# TAP (Transcorp APAAR Portal): Codebase Report

Scope: `D:\Apaar\Dev\codebase\tap_project`, compared against `AGENTS.md`.
Method: static read of settings, URLs, all views, API clients, models, serializers, crypto, Docker and config files. **I did not run the app or the tests**, so nothing here is runtime-verified.

---

## 1. Summary

TAP is a Django 5.0 + DRF service that sits between ABC (student portal) and TWA (mobile app). It receives student records, collects Aadhaar OTP verification (Cashfree) and a name match, runs MIN KYC with M2P, syncs the student to TWA, and forwards status updates back to ABC.

The structure is clean and follows `AGENTS.md`. Every outbound call has an API-log table written in a `finally` block. The main concerns are:

- Security and data handling: the portal is unauthenticated, full Aadhaar numbers land in the session table, and the rate limits are weak.
- Production readiness: mock fallbacks write fake KYC data, Django 5.0 is end-of-life, and cookie/TLS settings are incomplete.
- Spec drift: the code has diverged from `AGENTS.md` in several places.

**About 6,800 lines of Python** across 6 apps, 76 test functions, 5 HTML templates.

| App | Role | Notable files | Lines |
|---|---|---|---|
| `applications` | `Student`, `ABCApiLog`, `AadhaarApiLog`; ABC-facing APIs; HMAC auth; AES-GCM crypto; Cashfree client | `views.py` (636), `models.py` (282), `aadhaar_client.py` (221) | ~1,500 |
| `portal` | Student-facing pages and AJAX endpoints | `views.py` (825) | ~850 |
| `m2p` | M2P client, RSA/AES crypto, `M2PApiLog` | `client.py` (403), `crypto.py` (173) | ~650 |
| `twa` | TWA sync/pull client, inbound webhooks, `TWAApiLog` | `client.py` (265), `views.py` (233) | ~600 |
| `webhooks` | Outbound ABC dispatcher, `WebhookLog` | `dispatcher.py` (178) | ~290 |
| `cms` | `CMSPage` for success-page content | `models.py` | tiny |

Tests: `portal` 28, `applications` 19, `twa` 14, `webhooks` 9, `m2p` 6, `cms` 0.

## 2. Flow as implemented

1. **ABC → TAP** `POST /v1/issuer-bank/application/receive`
   - HMAC auth, then optional AES-256-GCM decrypt, then the nested ABC payload is flattened.
   - The photo is downloaded to S3 (or local `media/`), the `Student` row is saved as `PROCESSING` / `PENDING`, and a `redirect_url` is returned.
   - It is idempotent on `apaar_id`.
2. **Portal landing** `GET /portal/<tracking_id>/` shows the student data and a consent form with 5 checkboxes.
3. **Aadhaar send-OTP** (Cashfree). The full 12 digits go in the session, and the last 4 digits, `ref_id` and consents are saved on the student.
4. **Aadhaar verify-OTP**: verify, then name match (≥90%), then M2P `generate_otp`, then redirect to `/otp/`.
5. **M2P OTP** (`OTPVerifyView`) calls `register_min_kyc`. On success it sets `kyc_status=MIN_KYC`, fires the ABC webhook, calls TWA `sync_onboard`, and redirects to the success page.
6. **TWA → TAP** webhooks (IP-whitelisted) update the statuses and forward them to ABC.
7. **Lockout**: 3 failed attempts lock the student for `OTP_LOCKOUT_MINUTES` (default 15). After the cooldown, Aadhaar data is wiped and the student can retry.

## 3. Findings

### High priority

| # | Finding | Where |
|---|---|---|
| H1 | **Full 12-digit Aadhaar stored in the session** (`aadhaar_full_<id>`). With Django's default DB-backed sessions this is plaintext Aadhaar in `django_session` until the session expires or the flow completes. This contradicts the "last 4 only" intent that the logs and DB follow. `AadhaarVerifyOTPView` also re-accepts the full number from the request body. | `portal/views.py:103, 159-160` |
| H2 | **Fake KYC data persisted on success.** If M2P omits the kit number or token, the code stores `"KIT-MOCK-12345"` and `"TOKEN-MOCK-abcde"` as the real values and sends them to TWA. | `portal/views.py:366-367` |
| H3 | **Portal has no authentication.** Access depends only on `tracking_id`, which is ABC's `APPLICATION_REFERENCE_NUMBER` (externally assigned, possibly sequential or guessable). Anyone with it can view the student's PII and photo, and trigger Aadhaar/M2P OTP SMS to that student. | `portal/views.py`, `applications/views.py:418` |
| H4 | **OTP rate limiting is session-only.** The 30 s throttle lives in `request.session`, so a client that drops its cookie bypasses it. This allows SMS bombing and Cashfree/M2P cost abuse. The M2P resend endpoint has no cap on total sends. | `portal/views.py:64-72, 450-458` |
| H5 | **Django 5.0.14 is end-of-life** (mainstream and security support ended in April 2025). `requests==2.31.0` is also behind known fixes. | `requirements.txt` |
| H6 | **Missing production cookie and TLS settings.** There is no `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT` or HSTS. `SECURE_PROXY_SSL_HEADER` is set unconditionally, which is only safe behind a proxy that strips the header. | `settings.py:171-178` |
| H7 | **Inbound HMAC has no replay or body protection.** The signature is over `client_id:timestamp` only, with a 15-minute window. A captured request can be replayed, and the body can be altered with the same headers. The TWA webhooks rely on `REMOTE_ADDR` allow-listing, which breaks behind an ALB (every request appears to come from the proxy) and is the only control on those endpoints. | `authentication.py`, `twa/views.py:24-28` |

### Medium priority

| # | Finding | Where |
|---|---|---|
| M1 | **Raw exception text returned to clients** (`str(exc)`, decrypt errors, health-check errors). This leaks internals. | `portal/views.py:127, 264, 273`; `applications/views.py:381, 635` |
| M2 | **Silent failure paths.** Response encryption failure falls back to plaintext with `pass`. TWA sync failure after MIN_KYC is swallowed, leaving `twa_synced=False` with no retry. TWA encryption failure silently sends the payload in plaintext, including `vcipToken`. | `applications/views.py:96-100`, `portal/views.py:394-395`, `twa/client.py:208-213` |
| M3 | **SSRF protection has a gap.** The photo URL's DNS is checked once, but `requests.get` follows redirects and re-resolves, so a redirect or DNS rebinding can reach internal hosts. The download is also synchronous inside the ABC request (10 s timeout). On failure it stores the original URL. | `applications/views.py:173-274` |
| M4 | **Race window in MIN KYC.** The row lock is released before the M2P call. Two concurrent submits could both call `register`. The attempt counter is incremented before the call and decremented only on exception. | `portal/views.py:302-424` |
| M5 | **Mock switches active by config alone.** With `MOCK_CASHFREE_APIS` / `MOCK_M2P_APIS` on, Aadhaar `123456789012` + OTP `123456` succeeds with no external call. Nothing prevents this in `ENV_TYPE=PROD`. | `aadhaar_client.py:70-75`, `m2p/client.py:199, 356` |
| M6 | **Debug-only view with hardcoded credentials and paths.** `M2PCryptoTestView` embeds `Basic admin:admin`, `D:\Apaar\...` key paths, and fires 36+ live calls. It is gated by `DEBUG` only. | `portal/views.py:553-821` |
| M7 | **Weak defaults and unmasked logs.** `TRANSCORP_PRIVATE_KEY_PASSPHRASE` defaults to `12345` (also in `.env.example`). `TWAClient._mask_payload` is a no-op, so `m2p_token`, DOB and address are stored in `twa_api_logs`. Inbound ABC logs store full plaintext PII. | `settings.py:191`, `twa/client.py:98-100` |
| M8 | **Non-dict request bodies cause 500s.** `data.get(...)` is called without checking `data` is a dict (for example a JSON list). | `applications/views.py:388` |
| M9 | **Loose M2P success test.** The mere presence of `entityId` in the result counts as success. | `portal/views.py:350-359`, `m2p/client.py:376-387` |

### Low priority and hygiene

- **No git repository.** The folder is not under version control.
- **Stray files:** 40+ `APAAR-E2E-*.png` in `media/photos/`, `grep.exe.stackdump` in two places, and `.pem` keys in `../keys/`. `tap_project/.gitignore` does not cover `*.pem` or `keys/`.
- **Duplication:**
  - `mask_aadhaar_in_payload` is copied in two modules.
  - `twa/crypto.py` and `applications/crypto.py` are near-identical.
  - The "KYC already done" condition is repeated six times.
  - `generate_otp` has an `if/else` with identical branches.
  - There is dead code (`random_pan`, unused imports).
  - `print()` is used for errors.
- **`python-dateutil`** is imported but not in `requirements.txt` (it arrives transitively via boto3). `gunicorn` is unpinned in the Dockerfile.
- **Container:** runs as root, `collectstatic` runs but no static server (e.g. WhiteNoise) is configured, and `--clear` runs on every start.
- **`USE_TZ=False`** while the spec says "TIMESTAMP WITH TIME ZONE". The code mixes `timezone.now()` with naive datetimes.
- **`is_locked()` mutates and saves** on a read path. It wipes Aadhaar state when a cooldown expires, even on a GET.
- **`DashboardStatsView`** ignores `VERIFIED` and any status outside the fixed list. Status updates from TWA accept any string with no state machine.
- **Tests:** 76 test functions exist but `cms` has none, and nothing covers replay, SSRF, concurrency or rate-limit bypass.

## 4. Drift from `AGENTS.md`

| Spec | Code |
|---|---|
| M2P generate OTP: `POST /kyc/customer/generate/otp` | `GET /Yappay/otp-manager/v2/generate/+91<mobile>` |
| Register `documentType: "AADHAAR"`, businessType `TCAPAAR` | `AADHAARREF` is used; businessType values differ between the spec's two examples |
| `tracking_id` "generated by TAP" | Taken from ABC's `APPLICATION_REFERENCE_NUMBER`; the request is rejected if missing |
| HMAC message `client_id:timestamp` (inbound and outbound) | Outbound webhook signs `client_id\n timestamp` (matches the ABC PHP reference per code comment) |
| 5 portal routes | Also `/otp/` and `/m2p/resend-otp/` (not documented) |
| Timestamps with time zone | `USE_TZ=False` |
| §6.3 and §3 describe the Aadhaar step | Matches code, but the spec says TAP "collects PAN" in §1; the PAN client is gone (migrations 0003-0005 renamed PAN fields) |
| Retries out of scope | `WebhookLog` has retry fields but nothing consumes them |

## 5. Recommended next steps (in order)

1. Remove the full Aadhaar from the session. Pass it only through the request body, or hold it in short-lived server-side cache and never in the DB.
2. Replace the `KIT-MOCK` / `TOKEN-MOCK` fallbacks with a hard failure. Add a startup guard that refuses `MOCK_*=True` when `ENV_TYPE=PROD`.
3. Add IP/tracking-ID-keyed rate limits (cache-based) to the send-OTP and resend endpoints, and consider an unguessable portal token.
4. Upgrade Django to a supported release and bump `requests`. Add the secure-cookie, SSL-redirect and HSTS settings.
5. Include the body hash in the HMAC and add a nonce or timestamp-uniqueness check. Read the client IP from `X-Forwarded-For` safely for the TWA allow-list.
6. Fix the SSRF path (`allow_redirects=False`, connect to the resolved IP) and move the photo download off the request path.
7. Return generic error messages to clients and keep details in the logs only.
8. Delete or move `M2PCryptoTestView`, clean `media/` and stackdumps, put the project under git, and update `AGENTS.md` to match the current M2P contract.
