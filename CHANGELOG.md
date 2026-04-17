# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.4.1] - 2026-04-17

### Added
- Docker deployment support with a multi-stage root `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and a development hot-reload compose override example

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
