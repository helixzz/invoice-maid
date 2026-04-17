# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- Application loggers (`app.*`, `apscheduler`) now emit to stderr and are captured by the systemd journal. Previously only uvicorn access/error logs were visible because uvicorn configures only its own logger tree and the root logger had no handler, silently dropping `logger.info(...)` calls from application code.
- Suppress the benign `(trapped) error reading bcrypt version` traceback from passlib when using bcrypt >= 4.1.

### Added
- `LOG_LEVEL` environment variable (default `INFO`) for controlling application log verbosity. Accepts DEBUG, INFO, WARNING, ERROR, CRITICAL. Invalid values fall back to INFO.
- `app.logging_config` module that installs a root-logger stderr handler at process startup with sensible per-library level overrides (SQLAlchemy engine at WARNING, passlib at ERROR).

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
