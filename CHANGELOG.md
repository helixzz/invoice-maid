# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.5.3] - 2026-04-17

### Fixed
- **Outlook auth badge no longer flickers on page load** — badge and button now show a neutral gray "Checking..." spinner while the status is being fetched, then transition smoothly to the real state. No more jarring amber→green flash.
- **Stale "Running" scan log entries after service restart** — on startup the backend now marks any scan log that has no `finished_at` and no `error_message` as "Scan interrupted — service was restarted while scan was running". These previously showed as permanently "Running" in the Scan Operations tab.

## [0.5.2] - 2026-04-17

### Changed
- **Outlook authentication status badge** — each Outlook account row now shows a live "✓ Authenticated" (green) or "⚠ Not authenticated" (amber) badge, fetched from the backend on Settings load and updated immediately after a successful OAuth flow
- **Authenticate / Re-authenticate button** — blue primary button when not authenticated; gray secondary when already authenticated, making the action intent clear at a glance
- **Toast notifications wider and longer** — minimum 440px, maximum 600px; error toasts last 10 s, info toasts 7 s, success toasts 5 s

## [0.5.1] - 2026-04-17

### Fixed
- **OAuth login loop (root cause)** — `oauth_token_path` was NULL for Outlook accounts created before v0.2.1, causing the token to be silently discarded after every successful Microsoft authentication. Fixed by:
  - `POST /accounts/{id}/oauth/initiate` now auto-assigns `oauth_token_path` when it is NULL before starting the device flow
  - `_acquire_token_sync` raises immediately with a clear error when `oauth_token_path` is NULL, rather than silently loading an empty cache that always fails
  - Alembic migration `0005_backfill_oauth_token_path` backfills `oauth_token_path` for all existing Outlook accounts on next upgrade

## [0.5.0] - 2026-04-17

### Fixed
- **OAuth login loop** — `_attach_flow_task` now captures `oauth_token_path` and `outlook_account_type` as primitive strings before background execution, eliminating `MissingGreenlet` crashes from ORM attribute access after session expiry
- **OAuth token not saved after successful Microsoft login** — `_complete_device_flow_with_path_sync` force-writes the token cache when `access_token` is present, regardless of `has_state_changed`; old code silently skipped saving and caused authentication to loop
- **OAuth error detail hidden** — `pollOAuthStatus` now shows the `state.detail` message in an error toast when authorization fails or expires, instead of silently closing the modal
- **Upgrade script false-positive health check** — `invoice-maid-upgrade` now auto-detects the service port from `/etc/systemd/system/invoice-maid.service` instead of hardcoding port 8000; this caused the script to report "upgrade OK" while probing a stale old process on 8000 instead of the newly restarted service
- **Toast notifications too small and disappearing too fast** — toasts now use type-aware auto-durations (error: 7s, info: 5s, success: 4s) and have a proper minimum width of 360px, preventing messages from being squeezed into a few words per line

## [0.4.5] - 2026-04-17
## [0.5.0] - 2026-04-17


### Fixed
- Outlook personal account OAuth now uses Microsoft Graph Explorer client ID (14d82eec-...), supporting personal @outlook.com, @live.cn, @hotmail.com, @msn.com without Azure App Registration
- Scan job no longer aborts when one mailbox fails — each account scanned independently, failures logged per-account
- MissingGreenlet crash on scan failure resolved — failed accounts write error state via raw SQL after rollback
- Toast notifications wider (max-w-md) to prevent long messages wrapping into multiple lines
- Account form modal widened (sm:max-w-2xl) for Outlook personal/organizational radio group
- OAuth authentication modal widened (sm:max-w-xl) for device code display

### Changed
- OUTLOOK_PERSONAL_CLIENT_ID default changed from Azure CLI to Graph Explorer (14d82eec-...)

## [0.4.4] - 2026-04-17

### Added
- Outlook accounts now store `outlook_account_type` so Invoice Maid can distinguish personal Microsoft accounts from organizational Azure AD mailboxes

### Changed
- Outlook OAuth now selects the correct Microsoft public client ID and authority tenant for personal (`consumers`) vs organizational (`common`) accounts
- New Outlook accounts auto-detect account type from the mailbox domain while still allowing explicit organizational override

## [0.4.3] - 2026-04-17

### Fixed
- Outlook OAuth device code flow now uses the well-known Microsoft Office public client ID (`d3590ed6-52b3-4102-aeff-aad2292ab01c`) by default, allowing personal `@outlook.com`, `@live.com`, `@hotmail.com`, and `@live.cn` accounts to authenticate without needing an Azure App Registration
- `username` field for Outlook accounts now stores the mailbox email address instead of an Azure App Client ID (the client ID is now in config)
- Frontend Outlook account form now correctly labels the field "Microsoft Account Email" and shows the appropriate email placeholder

### Added
- `OUTLOOK_CLIENT_ID` environment variable (default: Microsoft Office well-known ID) to override for work/school Azure AD accounts
- `deploy/install.sh` — idempotent one-command production installer: creates system user, clones repo, builds venv, hashes admin password, writes `/etc/invoice-maid/invoice-maid.env`, runs Alembic migrations, installs systemd service, and optionally starts it. Supports headless (`--yes`), dry-run, random password, and version pinning flags.
- `deploy/invoice-maid-upgrade` — upgrade driver installed to `/usr/local/sbin/`: fetch latest tag, optional pre-backup, `pip install --upgrade`, `alembic upgrade head`, service restart, health probe with retries.
- Docker deployment support with a multi-stage root `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and a development hot-reload compose override example

### Changed
- `deploy/invoice-maid.service` hardened with `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `ProtectHome`, `ReadWritePaths`, `Restart=on-failure`; adds `{{ENV_FILE}}`, `{{PORT}}`, `{{DATA_DIR}}` placeholders; switches from `After=network.target` to `After=network-online.target`

## [0.4.0] - 2026-04-17

### Added
- Tiered email classification pipeline with zero-LLM Tier 1/2 heuristics and enriched Tier 3 LLM fallback
- `classification_tier` audit field on `ExtractionLog` plus Alembic migration `0003_v040_classifier_tier`
- Seeded `AppSettings` keys for classifier trusted senders and extra keywords

### Changed
- Scheduler scan pipeline now loads classifier settings once per run and only calls `ai.classify_email()` for ambiguous emails

## [0.3.0] - 2026-04-17

### Added
- Real-time scan progress: `GET /api/v1/scan/progress/stream` SSE endpoint pushes live updates for account, email, and attachment loops
- `GET /api/v1/scan/progress` polling snapshot fallback
- `ScanProgress` in-process singleton with per-account, per-email, and per-attachment signal points; weighted `overall_pct` computation
- `POST /api/v1/scan/trigger` now returns `409` when a scan is already in progress (concurrent scan guard via asyncio lock)
- JWT accepted via `?token=` query parameter for native `EventSource` connections (browsers cannot set Authorization headers on EventSource)
- `useScanProgress` Vue composable: SSE-first with 2 s polling fallback and auto-reconnect
- `ScanProgressBar` component: 3 nested progress bars (overall → per-account → per-email) with status line and done/error banners
- Scan Operations tab now shows live progress while scanning with final fetchLogs refresh on completion
- `LOG_LEVEL` environment variable (default `INFO`) for controlling application log verbosity
- `app.logging_config` module: installs root-logger stderr handler at startup, suppresses benign passlib bcrypt version traceback, sets per-library log levels
- Nginx SSE location block in deploy template (`proxy_buffering off`, `proxy_cache off`) — required for EventSource to work behind nginx

### Fixed
- Application loggers (`app.*`, `apscheduler`) previously had no root handler and silently dropped all `logger.info()` / `logger.warning()` calls; now routed to stderr and captured by the systemd journal
- Suppress benign `(trapped) error reading bcrypt version` passlib traceback on bcrypt ≥ 4.1

## [0.2.1] - 2026-04-17

### Fixed
- Outlook OAuth device code flow now starts only from dedicated account API endpoints instead of scan/test-connection
- Outlook account creation now auto-assigns a file-backed OAuth token cache path for single-worker deployments
- Outlook connection tests now return a specific authorization-required message when no cached token is available

### Added
- `POST /api/v1/accounts/{id}/oauth/initiate` to start or resume Outlook device authorization
- `GET /api/v1/accounts/{id}/oauth/status` to poll in-memory OAuth device flow state

## [0.2.0] - 2026-04-17

### Added
- Per-email extraction audit log with outcome tracking (saved, duplicate, skipped, parse error, not invoice)
- Manual invoice field correction UI with per-field audit trail and `CorrectionLog` model
- `is_manually_corrected` flag on invoices
- CSV export endpoint (`GET /invoices/export?format=csv`) with UTF-8 BOM and date filters
- Outbound webhooks on `invoice.created` with HMAC-SHA256 signature and `WebhookLog` model
- Saved views / smart filters (`GET/POST/DELETE /views`) persisting named search states
- Spend analytics: monthly spend, top sellers, count by type/method, average confidence
- Similar invoice discovery endpoint (`GET /invoices/{id}/similar`) via sqlite-vec KNN or FTS5 fallback
- AI model settings management via web UI (`GET/PUT /settings/ai`, `GET /settings/ai/models`)
- `AppSettings` model for database-backed runtime configuration with encrypted API key storage
- Project icon embedded as favicon, login page branding, and navigation bar logo
- Login rate limiting (10 req/min/IP via slowapi) with `Retry-After` on 429
- Rich health endpoint reporting DB, scheduler, sqlite-vec, invoice count, and last scan time
- Alembic migration `0002_v020_audit_and_corrections` for all new tables and columns
- Playwright E2E smoke tests with backend-backed deterministic data seeding

### Changed
- Exception handlers in email scanner now use specific exception types instead of bare `except Exception`
- APScheduler startup enforces single-worker guard with warning on multi-worker detection
- Log levels standardized across invoice parsers (DEBUG/INFO/WARNING/ERROR convention)
- Config type safety improved: removed `cast()` workarounds for required Pydantic fields
- Semantic search pagination now respects `page` and `size` parameters (was hardcoded to page=1)
- CORS tightened: removed `allow_credentials=True` with wildcard origins
- Frontend download flow preserves real file extension for XML/OFD invoices

### Fixed
- AppLayout rendering bug that caused protected pages to appear empty (RouterView → slot)
- POP3 scanner replaced non-existent `MailBoxPop3` with stdlib `poplib` parser
- Email body download links now ingested by scan pipeline
- Production embedding storage created during `init_db()` with graceful sqlite-vec fallback
- Scheduler failure-path logging no longer crashes on expired ORM objects after rollback

## [0.1.0] - 2026-04-17

### Added
- Email inbox scanning: IMAP, POP3, QQ Mail (app password), Microsoft Outlook (OAuth device code flow)
- AI-powered email classification using OpenAI-compatible LLM
- Invoice parsing for PDF, XML, and OFD formats
- QR code extraction for Chinese VAT invoice fields
- Structured field extraction: buyer, seller, amount, date, type, item description
- LLM-based field extraction with instructor for structured output
- Full-text search via SQLite FTS5
- Optional semantic search via sqlite-vec embeddings
- Web UI: invoice search with date filtering, PDF preview, single and batch ZIP download
- Single-user JWT authentication
- Scheduled periodic email scanning via APScheduler
- Systemd service and nginx reverse proxy templates
- LLM response caching by content hash
- Canonical invoice file naming: {buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf
