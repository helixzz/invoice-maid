# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.9.0-alpha.4] - 2026-04-21

### Theme

Phase 2 of the multi-user transition. Adds a nullable `user_id` column to every tenant-scoped table and backfills existing rows to the bootstrap admin. Structurally additive only — no `NOT NULL`, no foreign-key enforcement, no composite-unique reshaping. Those tightenings happen in Phase 3 (migration 0012) once the application code has been updated to populate `user_id` on every write, which is Phase 4 work. This migration is the one that makes every future tenant-scoped query possible to write without breaking the existing single-operator deployment.

### Added

- **Alembic migration `0011_add_user_id_to_tenant_tables`**: adds nullable `user_id INTEGER` to seven tenant tables — `invoices`, `email_accounts`, `scan_logs`, `extraction_logs`, `correction_logs`, `saved_views`, `webhook_logs` — backfills every existing row to `users[1]` when the bootstrap admin exists, and creates per-table indexes sized to the actual query shapes the app issues. `llm_cache`, `app_settings`, `users`, and `user_sessions` are deliberately NOT tenant-scoped: `llm_cache` stays shared so a single-org deployment doesn't duplicate identical parse/classify answers across users; `app_settings` is instance-wide by design; `users`/`user_sessions` are already user-scoped by construction.

- **Indexing strategy tuned per query shape**:
  - `invoices` → composite `(user_id, invoice_date DESC)` to match the default list + search ordering
  - `email_accounts`, `correction_logs`, `saved_views` → single-column `(user_id)` (small tables, only ever filtered by user)
  - `scan_logs` → composite `(user_id, started_at DESC)` for the scan-history UI
  - `extraction_logs` → composite `(user_id, created_at DESC)` — largest table, highest-value index
  - `webhook_logs` → composite `(user_id, created_at DESC)` for the per-user delivery audit view in Phase 5

- **ORM column** `user_id: Mapped[int | None]` on all seven tenant models with `ForeignKey("users.id", ondelete="CASCADE")` and `index=True`. Matches the migration shape, so `Base.metadata.create_all` on fresh installs produces an equivalent schema without running alembic.

- **Schema-shape contract tests** (`tests/test_models/test_orm_models.py`): asserts every tenant model has a nullable, indexed `user_id` with a CASCADE foreign key to `users.id`, and asserts `LLMCache` does NOT have `user_id` (guards against accidentally tenant-scoping the shared cache in a future refactor).

- **Migration end-to-end tests** (`tests/test_migrations.py`): four tests covering the four real deployment shapes — existing deployment with rows to backfill, fresh install where `users` table is empty (leave NULL), downgrade with partial schema (tables created by `create_all` absent), upgrade with full production-shaped schema (`saved_views` + `webhook_logs` present). Each test exercises one branch of the migration's table-presence check.

### Changed

- **Migration is defensive about missing tables.** `saved_views` and `webhook_logs` have never been created by any Alembic migration in this project — they come from `Base.metadata.create_all` at first app start. Migration 0011 skips any tenant table that doesn't yet exist at the time it runs (the fresh-install case where alembic runs before the app has ever booted). When the app later starts and `create_all` materialises those tables, the ORM definition already carries the `user_id` column and its index, so the end state is the same regardless of which path the deployment took.

- **Backfill is gated on `users[1]` existing.** A fresh install where alembic runs before the app boots has an empty `users` table. In that shape, the migration leaves `user_id` NULL on every row; the first-boot bootstrap hook seeds `users[1]`, and the tenant tables are also empty, so there's nothing to backfill. The backfill path only matters for existing deployments that ran Phase 1 (migration 0010) and booted the app at least once.

### Upgrade path

Zero-touch for v0.9.0-alpha.3 deployments:

1. `alembic upgrade head` applies migration 0011.
2. Every existing row in the seven tenant tables has its `user_id` set to `1` (the bootstrap admin from Phase 1).
3. Seven new indexes are created, sized for the query shapes the app actually uses.
4. No API contract change. No behavior change. Queries that don't filter by `user_id` continue to work; queries that do now have a matching index.

Rollback: `alembic downgrade 0010_users_and_sessions` drops the `user_id` column and the seven indexes cleanly. Other columns and data are untouched. The downgrade path uses `batch_alter_table` for the column drop, which rebuilds each table — acceptable for operator-initiated rollback, never for a hot path.

### Dry-run validation

Migration was dry-run against a fresh copy of the production database before shipping. Pre-upgrade row counts matched post-upgrade row counts across all seven tenant tables. Every row had `user_id = 1` post-upgrade; zero `user_id IS NULL`. Seven new indexes present by name. Subsequent downgrade restored the schema to `0010_users_and_sessions` with all row counts preserved. Subsequent re-upgrade returned to the same post-upgrade state.

### Tests

- 526 passing, 100% coverage (+6 from v0.9.0-alpha.3): 3 schema-shape assertions on the ORM layer, 4 end-to-end migration tests on the alembic layer, 1 regression for the shared-cache invariant.
- The `tests/test_migrations.py` fixture snapshots logger state around each `command.upgrade`/`command.downgrade` call and restores it afterward. Without this, alembic's `env.py` call to `logging.config.fileConfig` disables every existing logger for the rest of the session and silently breaks `caplog`-based assertions elsewhere in the suite.

### Alpha designation

Still `0.9.0-alpha` because Phases 3-5 of the multi-user transition are not yet shipped:
- Phase 3 (migration 0012): `NOT NULL` + FK tightening, composite unique `(user_id, invoice_no)` on invoices, FTS5 trigger rebuild
- Phase 4: repository pattern, per-user file-storage layout, tenant-isolation tests for every endpoint
- Phase 5: admin UI, registration flow, per-user settings, per-user webhooks

Single-operator deployments can run this in production: all existing behavior is preserved, no user-visible change, every query continues to return the same rows it returned yesterday.

## [0.9.0-alpha.3] - 2026-04-21

### Fixed

- **Login rejected self-hosted-style email addresses.** v0.9.0-alpha.1's `LoginRequest` used pydantic's `EmailStr` which requires a dotted domain (RFC 5321 §4.5.3.1.2). The bootstrap default `ADMIN_EMAIL=admin@local` does not qualify, so the bootstrap admin created by v0.9.0-alpha.1 could not authenticate through the v0.9.0-alpha.2 login form — the request returned `422 "The part after the @-sign is not valid. It should have a period."` before reaching the DB lookup. `LoginRequest.email` is now a plain `str` with only length bounds; the DB lookup remains exact-match so there's no security regression. Real format validation belongs on a future `/auth/register` endpoint (Phase 5), not on login where we just need to resolve an existing row.
- **Regression test added** to lock this in: `test_login_accepts_unqualified_hostname_email` seeds a user with `admin@local` and asserts login returns 200 + a valid token. A future maintainer hardening the schema back to `EmailStr` hits this test immediately.

### Upgrade

Zero backend migrations. Operators on v0.9.0-alpha.1 or v0.9.0-alpha.2 who can't log in due to the `@local` issue get the fix via the standard deploy path. Existing `admin@local` users in the DB are preserved as-is — no data migration needed.

## [0.9.0-alpha.2] - 2026-04-21

### Fixed

- **Login page missing email field.** v0.9.0-alpha.1 shipped the backend change from `{password}`-only to `{email, password}` login payloads but left the frontend form unchanged, so the login page collected only a password and every attempt returned `422 email is required`. Operators could not log in via the UI after upgrading. The login form now has an email input alongside the password input, the Pinia auth store's `login(email, password)` action threads both through, and `api.login(email, password)` sends `{email, password}` JSON to the backend. The stale-JWT redirect path through the axios `response.use` 401-interceptor is unchanged — operators who still have a `v0.8.10`-era token in localStorage hit any authenticated request, get a 401, get redirected to the updated login page, and proceed normally.

### Upgrade

Zero backend migrations needed. Frontend `dist/` is rebuilt with the fix and committed. The deploy script's unconditional `git reset --hard` + service restart picks up the new bundle.



### Theme

Phase 1 of the multi-user transition. This alpha ships the foundational auth layer — DB-backed users, session revocation, email-based login — without yet tenant-scoping existing data. Single-operator deployments upgrading from v0.8.10 get identical functionality: the bootstrap admin is auto-created from existing `.env` credentials on first boot, every API endpoint still works with the same scope and semantics, and there is still only one user on the system. Phases 2 through 5 (user_id columns, repository pattern, admin UI, per-user settings) land in subsequent releases.

### Added

- **`users` table** (Alembic migration `0010_users_and_sessions`): id, email (unique), hashed_password, is_active, is_admin, created_at, updated_at. Stores credentials that previously lived in a single `.env` hash.

- **`user_sessions` table**: id, user_id (FK with CASCADE), token_hash (SHA-256 of the JWT), created_at, expires_at, revoked_at, last_seen_at, user_agent, ip_address. Enables server-side session revocation: a valid JWT is no longer sufficient for access — there must also be an unrevoked `user_sessions` row matching the token's SHA-256 hash.

- **`User` and `UserSession` ORM models** under `app/models/`. Both exported from `app.models.__init__`.

- **`app/services/bootstrap.py`**: `bootstrap_admin_user(db, settings)` creates `users[1]` on first boot when the `users` table is empty and both `ADMIN_EMAIL` and `ADMIN_PASSWORD_HASH` are configured. Idempotent — runs on every `lifespan` startup but is a no-op after the initial seed.

- **`ADMIN_EMAIL` setting** in `app/config.py`. Defaults to `"admin@local"` when not set, so v0.8.10 deployments upgrade with zero `.env` changes required. Operators who want a real email can set it before or after upgrade.

- **`app/services/auth_service.py`** session helpers: `hash_token`, `create_user_session`, `resolve_active_session`, `revoke_session`, `revoke_all_sessions`. The `token_hash` column stores SHA-256 of the raw JWT so the server never holds raw tokens — session lookups happen via hash comparison.

- **New endpoints under `/api/v1/auth`**:
  - `POST /auth/logout` — revokes the current session (the one tied to the incoming bearer token)
  - `POST /auth/logout-all` — revokes every unrevoked session for the current user (the "sign out from all devices" action)
  - `GET /auth/me` — returns `{id, email, is_active, is_admin, created_at}` for the current user
  - `GET /auth/sessions` — lists unrevoked sessions for the current user with `user_agent` + `ip_address` fingerprints, for a future session-management UI

### Changed

- **`POST /auth/login` now requires `{email, password}`.** The old `{password}`-only request returns `422` so misconfigured clients fail loudly rather than authenticating as a generic admin. JWT `sub` claim is now the integer user_id, not the string `"admin"`. Each login creates a new `user_sessions` row and records `user_agent` and `ip_address` from the request.

- **`CurrentUser` dependency** changed from `Annotated[str, ...]` (returned `"admin"`) to `Annotated[User, ...]` (returns the `User` ORM object). Every endpoint that used `_current_user: CurrentUser` and ignored the value keeps working unchanged. Endpoints that need `user.id` or `user.email` can now access them directly.

- **New `CurrentUserAndSession`** type: `Annotated[tuple[User, UserSession], ...]`. Used by `/auth/logout` to revoke the specific session the request came in on.

- **`get_current_user` now validates the session**, not just the JWT. A JWT with a valid signature but no matching unrevoked `user_sessions` row is rejected with 401. Covers revoked sessions, expired session rows, and sessions belonging to deactivated users.

- **JWTs now include a `jti` claim** (8-byte random nonce). Previously, two tokens with the same `{sub, exp}` produced identical bytes, which conflicted with `user_sessions.token_hash`'s uniqueness constraint when a user logged in twice in the same second.

- **`LoginRequest` schema** updated with `EmailStr` validation (adds `email-validator>=2.0` via `pydantic[email]` extra). `email-validator` rejects reserved TLDs like `.local`, so test fixtures use `@example.com`.

### Fixed

- **Session-validation security**: a valid JWT alone no longer grants access. This is the contract that makes `/auth/logout` and "revoke all devices" meaningful — without this, revoking a session would have no effect because the JWT would still verify.

### Upgrade path

Zero-touch for v0.8.10 deployments:

1. `alembic upgrade head` applies migration `0010_users_and_sessions`, creating two new empty tables. Existing tables are untouched.
2. On first restart, the `lifespan` hook calls `bootstrap_admin_user` which sees the empty `users` table and creates row 1 from `ADMIN_EMAIL` (default `admin@local`) + `ADMIN_PASSWORD_HASH` (existing env var).
3. Login screen prompts for email + password. Existing operators enter their existing password with `admin@local` (or whatever `ADMIN_EMAIL` they set). The JWT they receive ties back to user 1.
4. All existing data (invoices, email_accounts, scan_logs, etc.) is untouched. Still accessible exactly as before — the data model hasn't changed.

Operators who want a different bootstrap email add `ADMIN_EMAIL=them@example.com` to `.env` before the upgrade.

### Tests

- 519 passing, 100% coverage (+11 from v0.8.10).
- Rewritten `test_deps.py` around the session-aware dep: session-revocation contract, revoked session rejection, deactivated user rejection, naive-tzinfo coercion, unknown-token handling.
- Rewritten `test_api/test_auth.py`: email-based login, logout, logout-all, /me, /sessions, session fingerprint recording.
- New `test_services/test_bootstrap.py`: empty-table seeding, idempotency, blank-email/hash skip, email normalisation.
- CI-simulated run clean.

### Alpha designation

This release is tagged as `0.9.0-alpha.1` (Python packaging canonical form `0.9.0a1`) to signal:
- The v0.9.0 roadmap is incomplete (Phases 2-5 still pending).
- Single-operator deployments can run this in production: all existing behavior is preserved and the only user-visible change is the login flow (email + password instead of password-only).
- Multi-user registration, admin UI, and per-user data isolation are NOT in this release. A deployment with only one admin user is the supported shape.



### Theme

Operational hardening ahead of the v0.9.0 multi-user transition. Three changes that bound resource growth on long-running self-hosted deployments: a WAL file size cap, TTL-based eviction for `llm_cache`, and a 90-day retention window for `extraction_logs`. Shipped independently so the multi-user release can build on a stable operational baseline.

### Added

- **`LLMCache.expires_at` column + nightly eviction job.** New Alembic migration `0009_llm_cache_expires_at` adds an `expires_at DATETIME` column to `llm_cache` and backfills existing rows with conservative expiration windows based on `prompt_type`: 30 days for `classify` / `analyze_email_v3` entries, 365 days for `extract` entries. `AIService._get_cache` now filters out rows where `expires_at` is in the past, so expired hits are treated as cache misses and trigger a fresh LLM call. A new `cleanup_llm_cache` job runs hourly via APScheduler and deletes up to `LLM_CACHE_CLEANUP_BATCH_SIZE` (5000) expired rows per tick.

- **`ExtractionLog` 90-day retention.** A new `cleanup_extraction_logs` job runs daily and deletes rows where `created_at` is older than `EXTRACTION_LOG_RETENTION_DAYS` (90). Batched at `EXTRACTION_LOG_CLEANUP_BATCH_SIZE` (10000) rows per tick to keep single-tick work bounded; subsequent ticks finish the job. The table grows at roughly 1000:1 relative to saved invoices (most scanned emails are classified as non-invoice and logged), so unbounded retention becomes a problem at multi-user scale.

### Fixed

- **SQLite WAL file on-disk growth.** Adds `PRAGMA journal_size_limit=67108864` (64 MiB) at connect time. SQLite's default `wal_autocheckpoint=1000` (4 MiB) marks WAL pages reusable but does NOT shrink the file; without a size limit the WAL can grow into hundreds of MB when a long-running reader stalls a checkpoint. 64 MiB is generous headroom for this workload's short 3-row invoice-save transactions while preventing unbounded disk growth.

### Tests

- 497 passing, 100% coverage (+8 from v0.8.9).
- New tests:
  - `test_cleanup_llm_cache_removes_expired_entries` — past-expiry deletion, future-expiry and NULL-expiry preservation
  - `test_cleanup_llm_cache_is_no_op_when_nothing_expired` — quiet-log guard on empty runs
  - `test_cleanup_extraction_logs_removes_old_entries` — retention boundary behavior
  - `test_cleanup_extraction_logs_is_no_op_when_nothing_expired` — symmetric quiet-log guard
  - `test_cleanup_extraction_logs_respects_batch_size` — per-tick work bounded
  - `test_ai_service_cache_expiry_windows_match_migration_contract` — cross-file contract: `_cache_expiry` TTL values must match migration 0009 backfill windows
  - `test_get_cache_treats_expired_rows_as_miss` — read-path filter correctness
  - `test_set_cache_stamps_appropriate_expires_at` — new rows carry `expires_at` from the start

### Upgrade path

Upgrade from v0.8.9 is zero-touch: `alembic upgrade head` applies migration 0009, which adds the new column and backfills existing rows in a single transaction. Existing cache entries that would not have had `expires_at` set under v0.8.9's cache API are given TTLs based on their `prompt_type`. No data migration errors possible — the migration is purely additive. The two new maintenance jobs register themselves on service restart via `start_scheduler`.

### Not changed

- No changes to invoice extraction logic, scanner code, or user-facing endpoints.
- No schema changes outside the single `expires_at` column addition.
- No dependency version changes.

## [0.8.9] - 2026-04-20

### Fixed

- **LLM amount-miss on transport e-tickets with bare-currency fares.** On image-based 12306-style railway e-ticket PDFs the fare sometimes appears as a bare `￥N.NN` fragment with no `票价` or `价税合计` label adjacent. The v0.8.8 extraction prompt anchored amount extraction to those labels, so the LLM fell back to the `0.01` sentinel on these tickets and the confidence gate saved them with `amount=0`. The prompt is updated in four narrow places to authorize bare-currency extraction on transport e-tickets, exclude `退票费 / 改签费 / 手续费 / 服务费 / 退款 / 已退 / 优惠`-labeled amounts, scope Path B's relaxation to validity only (not amount extraction), and make the `0.01` fallback conditional on no positive readable fare remaining after exclusions.

### Added

- 3 prompt-contract tests in `test_ai_service.py` that snapshot the new clauses so future cleanup edits can't silently re-introduce the regression:
  - `test_extract_prompt_contains_transport_bare_currency_rule` — asserts the bare-currency example and the "relaxes VALIDITY only" scoping remain.
  - `test_extract_prompt_still_rejects_scam_signals` — Step 0 scam-rejection (`代开` / `出售发票` / `加微信`) is unchanged.
  - `test_extract_prompt_excludes_refund_and_fee_labels_from_amount` — all five refund/fee label classes present in exclusion list.

### Not changed

- **`InvoiceExtract.amount` schema** stays `ge=0`. The failure was prompt selection, not Pydantic validation — schema already accepted the right value; the LLM needed permission to choose it.
- **No deterministic post-LLM fallback yet.** If this class of miss recurs on future uploads, a narrow heuristic ("for transport e-tickets with `amount < 0.10` and exactly one bare `￥N.NN` not near refund labels, use it") is on the table for a later release.

### Tests

- 489 passing, 100% coverage (+3 from v0.8.8).
- CI-simulated run (no `.env`, env vars injected inline) passes clean.

## [0.8.8] - 2026-04-20

### Why this release matters

Railway e-ticket uploads (`铁路电子客票`) could fail the extraction pipeline end-to-end on v0.8.7 for two independent reasons: the LLM prompt pre-dated the 2024 State Taxation Administration reform that reclassified these tickets as legal VAT invoices, and concurrent upload batches could trip SQLite's default 5-second writer busy_timeout during the LLM round-trip. v0.8.8 addresses both.

### Regulatory background

Per **国家税务总局 财政部 中国国家铁路集团有限公司公告 2024年第8号** (effective 2024-11-01) railway e-tickets (`电子发票（铁路电子客票）`) and **2024年第9号公告** (effective 2024-12-01) airline e-itineraries (`电子发票（航空运输电子客票行程单）`) are full `全面数字化的电子发票` with VAT deduction rights. Pre-2024 the older paper 火车票 / 行程单 were explicitly NOT VAT invoices, and the v0.8.7 prompt reflected that old rule.

### Added

- **`TRANSPORT_E_TICKET_TYPES`** frozenset in `app/schemas/invoice.py` with the two STA-designated type names. Both added to `VALID_INVOICE_TYPES`.
- **`_is_transport_e_ticket(parsed)`** helper in `app/services/manual_upload.py` — detects transport e-tickets via official type match OR raw-text marker match (铁路电子客票 / 铁路客运 / 航空运输电子客票行程单 / 电子行程单) AND 20-digit invoice_no. The 20-digit gate prevents scam documents from abusing the relaxed confidence path.
- **LLM prompt PATH B** in `app/prompts/extract_invoice.txt` — new validity branch for railway/airline e-tickets. Matches when invoice_no is 20 digits AND at least one transport marker is present (车次 / 发站 / 到站 / 乘车日期 for railway; 航班号 / 乘机日期 / 燃油附加费 for airline). Accepts the document as a valid VAT invoice even when `价税合计` / `税率` are not readable.
- **LLM prompt field guidance** for transport tickets: buyer = 乘车人/乘客, seller = 铁路运输企业 / 航空公司, amount = 票价 for railway or 票价+燃油附加费+民航发展基金 for airline, item_summary = "铁路客运 [起]→[止] [车次]" or "航空客运 [起]→[止] [航班号]".
- **5 new schema/helper tests** for the transport path: type-name match, raw-text marker fallback, 20-digit scam-resistance, non-transport-document sanity, and a `VALID_INVOICE_TYPES` freeze assertion.
- **4 integration tests** exercising `process_uploaded_invoice` end-to-end: image-PDF railway path saves with `amount=0`, airline e-itinerary path, readable-amount railway path goes through the normal merge, and Didi-style ride itineraries remain rejected (they are NOT in the 2024 regulations).
- **1 merge-semantics test**: `_merge_llm_into_parsed` refuses to overwrite parser's `None` amount with an LLM-returned value below `0.10` (prevents sentinel leak).

### Fixed

- **LLM prompt**: `行程单` removed from the reject list; `用车凭证 / 滴滴 / 出行记录` stay. Reject list has an explicit NOTE clarifying that railway and airline e-tickets are NOT ride-itinerary receipts. Scam-detection Step 0 is unchanged.
- **`InvoiceExtract.amount`** schema relaxed from `gt=0` to `ge=0`. Image-based transport tickets may legitimately have no readable amount; forcing `> 0` previously caused fabrication or pydantic validation errors. The `amount < 0.10` sentinel gate still catches this downstream.
- **`InvoiceExtract.is_valid_tax_invoice`** description rewritten to list railway/airline e-tickets as valid, citing the 2024 regulations. `instructor` feeds this description to the LLM as schema metadata, so the change directly moves classification behavior.
- **Confidence gate in `manual_upload.process_uploaded_invoice`**: for detected transport e-tickets, `amount_is_sentinel` is ignored and the confidence floor drops from 0.6 to 0.5. Invoice saves with `amount=0` so the operator can correct via inline edit. Narrow and audit-trailed — not a blanket relaxation.
- **`_merge_llm_into_parsed`**: merge of `extracted.amount` into `parsed.amount` now requires `extracted.amount >= 0.10`. Prevents `0.01` sentinel from leaking through.

### Fixed (SQLite concurrency)

- **`database.py`**: added `timeout=30.0` to aiosqlite `connect_args`. Default SQLite `busy_timeout` is 5 seconds, which is too short when a writer holds the lock through a 10–30 second LLM round-trip. Under sustained concurrent upload pressure this produced `sqlite3.OperationalError: database is locked`. 30s accommodates the LLM p99 latency with margin and matches the pattern used by mature FastAPI + SQLite projects (Datasette, Litestream).
- **`manual_upload._create_upload_scan_log`**: commits immediately after `db.add(ScanLog) + db.flush()` (previously only flushed). Releases the SQLite writer lock before the LLM enrichment call so concurrent requests can write their own ScanLog rows without blocking on each other.

### Tests

- 486 passing, 100% coverage (+10 from v0.8.7).
- Backward-compat verified: the v0.8.6 manual-upload test suite (22 tests) and the v0.8.7 multi-file upload flow (all e2e paths) pass unchanged.
- CI-simulated run clean.

### Citations

- 国家税务总局 财政部 中国国家铁路集团有限公司公告 2024年第8号《关于铁路客运推广使用全面数字化的电子发票的公告》 — effective 2024-11-01
- 国家税务总局 财政部 中国民用航空局公告 2024年第9号《关于民航旅客运输服务推广使用全面数字化的电子发票的公告》 — effective 2024-12-01

## [0.8.7] - 2026-04-20

### Why this release matters

v0.8.6 shipped manual upload but only accepted one file at a time. The obvious next step: onboarding a historical backlog means dragging in a folder full of PDFs, not picking each one through the browser's file dialog. v0.8.7 replaces the single-file view with a **batch-aware queue** that accepts up to 25 files per drop and processes up to 3 in parallel.

No backend change — the single-file `POST /api/v1/invoices/upload` endpoint stays exactly as it shipped in v0.8.6 (476 tests, 100% coverage). Multi-file is a purely frontend orchestration: the browser loops through the selected files and fires N concurrent requests, respecting both the client-side 3-at-a-time concurrency cap and the server's existing 30/min rate limit (which surfaces as a per-file `429` with a Retry button if tripped).

### Added

- **Multi-file drag-and-drop** — drop up to 25 PDF / XML / OFD files at once. The file input now has `multiple` and `addFiles()` ingests every `File` in the `DataTransfer` payload. Each dropped file becomes its own `QueueEntry` with its own status and progress.
- **Per-file queue UI** — each entry renders a row showing filename, size, an icon reflecting its state (queued / uploading / saved / error / blocked), and a per-row progress bar while uploading. Done entries link to the saved invoice; failed entries show a red inline error panel with the backend's structured `outcome` message and a Retry button.
- **Parallel upload with concurrency cap** — `runQueue()` spawns 3 workers that each greedy-poll the queue for the next `queued` entry. The cap keeps us well under the slowapi `30/min/IP` server limit for normal backlog sizes and prevents a single browser from saturating the backend.
- **Queue summary badge** — header row shows live counts (`N queued · M uploading · K saved · X failed · Y blocked`) so the user knows at a glance whether the batch is still processing. When all entries settle the badge becomes a green confirmation with a link to the invoice list.
- **Retry individual failures** — `retryEntry()` resets one failed entry back to `queued` and kicks the queue again. Successful neighbors are untouched. Clears-finished button strips `done`/`error`/`blocked` rows after review so re-dropping doesn't double-count.
- **Client-side pre-flight validation** — invalid files (too big, wrong extension / MIME) enter the queue as `blocked` with the rejection reason surfaced immediately. Network round-trip only happens for files that pass the local check.

### Changed

- **`InvoiceUploadView.vue`** fully rewritten around the `QueueEntry[]` state model. The previous single-file view grew by ~250 lines for the multi-file orchestration; render logic stays below the fold with Tailwind classes reused from the existing app shell.
- **Upload input** now has `multiple` attribute so Cmd/Ctrl-click selection in the browser dialog picks multiple files at once.
- **Bundle size** grew by ~3 KB gzipped (88.14 KB vs 87.07 KB) — small enough that no code splitting is warranted.

### Tests

- Backend unchanged: 476 passing, 100% coverage (same as v0.8.6). The feature is purely frontend; no new backend surface to test.
- Frontend builds clean under `vue-tsc -b && vite build`, 107 modules transformed, no TypeScript errors.

### Expected impact

Operators with a historical-backlog folder of tens of invoices can now drag the whole folder into the upload zone (up to 25 files per batch) instead of clicking through the file picker N times. Per-file status rows make it obvious which files saved, which were rejected as duplicates (with a link to the existing invoice for verification), and which need fresh input.

### What this intentionally does NOT do

- **No backend batch endpoint.** The server still sees N individual `POST /invoices/upload` requests. Rationale: the v0.8.6 endpoint's security-hardened streaming path, middleware, magic-byte checks, and XXE/zip-bomb defenses are tested against that exact shape; a batch endpoint would double the attack surface to save a few requests.
- **No server-side queue or background worker.** Processing stays inline to each request so per-file feedback arrives within a few seconds. At 3 concurrent × ~2s per invoice the batch completes in real time; a 25-file batch finishes in ~15–20 seconds wall clock.
- **No drag-and-drop folder picker.** HTML5 `<input type="file" multiple>` gives multi-select within a folder but not recursive folder traversal. Users wanting to upload nested folders can still do so in two drops.

## [0.8.6] - 2026-04-20

### Why this release matters

v0.8.5 made bulk export more useful; v0.8.6 addresses the other side of that flow: what if an invoice didn't arrive by email at all? Historical backlog from before any mailbox was configured, paper invoices that have been scanned, invoices sent over WeChat / DingTalk / WhatsApp — none of these could enter the system until now.

Manual upload lets the user drop any PDF / XML / OFD invoice file into the same extraction pipeline the email scanner uses. No new parser, no new classifier, no divergent code path — the uploaded file lands on the same `invoice_parser.parse()` → `AIService.extract_invoice_fields()` → `FileManager.save_invoice()` → `Invoice` row → `ExtractionLog` chain the email flow exercises. Extraction quality is literally identical because it IS the same code.

The feature also forced us to harden a handful of security surfaces that the email-only scanner didn't care about (because the scanner's inputs are implicitly trusted — the operator chose which mailbox to connect). Uploads come over the wire from a browser and deserve CVE-grade defenses: magic-byte MIME sniffing, XXE-safe XML parsing, zip-bomb detection for OFD, path-traversal-proof UUID filenames, streaming body-size enforcement in three independent layers, and an updated `python-multipart` pin.

### Added

- **`POST /api/v1/invoices/upload`** endpoint — multipart/form-data, single file per request, 25 MB ceiling, rate-limited to 30 uploads per minute per IP. Returns `201 + InvoiceResponse` on success.
- **`/upload` route in the web UI** with a drag-and-drop zone, client-side size / type pre-check (22 MB / `.pdf` / `.xml` / `.ofd`), axios `onUploadProgress` percentage bar, and error-panel UI that maps each backend outcome to a specific message (plus a "view existing invoice" link for 409 duplicates).
- **`app.services.manual_upload.process_uploaded_invoice()`** — the orchestration function shared between the endpoint and any future CLI/import tool. Returns an `UploadResult` dataclass whose `outcome` field maps 1:1 to HTTP status codes so the endpoint is a thin translator.
- **Sentinel `EmailAccount(type='manual', name='Manual Uploads', is_active=False)`** seeded by Alembic migration `0008_manual_upload_pseudo_account` — satisfies the `invoices.email_account_id` NOT NULL foreign key without making the column nullable, and lets scan-history queries filter manual uploads via `WHERE email_accounts.type='manual'`.
- **`ContentSizeLimitMiddleware`** — an ASGI middleware registered on `/api/v1/invoices/upload` that counts request-body bytes as they arrive and short-circuits with `413 Request Entity Too Large` the moment accumulated bytes cross the 25 MB threshold. Combined with nginx (22 MB) and the route-level streaming counter (also 25 MB), upload size is enforced in three independent layers.
- **Magic-byte MIME validation** via the `filetype` library (pure-Python, no `libmagic` C dep), plus manual prefix checks for XML (`<?xml`) and OFD (`PK\x03\x04`) which `filetype` can't detect. The first 512 bytes of every upload are sniffed before a single byte is parsed.
- **OFD zip-bomb defense** — `parse_ofd()` now enumerates the ZIP central directory and refuses to pass the bytes to `easyofd` when the cumulative uncompressed size exceeds `OFD_MAX_UNCOMPRESSED_BYTES = 100 MB`.
- **XXE-hardened XML parser** — `parse_xml()` now constructs `lxml.etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)` so billion-laughs expansion, external DTD fetches, and quadratic blowup attacks cannot run through invoice-XML uploads.
- **UUID-based temp filenames** — `_safe_filename()` never echoes a client-supplied path component into the filesystem; a `../../etc/passwd.pdf` upload filename becomes `<uuid>.pdf`.
- **New "How it works" workflow SVG** in README showing the two invoice sources (scheduled email scan + manual upload) converging on the five-stage shared pipeline.

### Changed

- **`python-multipart` minimum pin** raised from `>=0.0.9` to `>=0.0.22` to cover CVE-2024-24762 (ReDoS), CVE-2024-53981 (boundary DoS), and CVE-2026-24486 (path traversal). Production already had `0.0.26` installed; the pin is documentation and guard against future regressions.
- **`filetype>=1.2,<2.0`** added as a new direct dependency for magic-byte sniffing.
- **nginx template** `client_max_body_size` tightened from 50 MB → 25 MB so nginx rejects oversized uploads before they hit Python.
- **`FileManager.stream_zip()`** (from v0.8.5) — no change to signature; the `extra_members` kwarg introduced for the summary-CSV feature is now also used by the upload feature's test harness.
- **`InvoiceExtract.amount`** branch in `_merge_llm_into_parsed()` — marked `# pragma: no branch` because the pydantic schema's `Field(gt=0)` makes the falsy branch unreachable at runtime.

### Fixed

- **Race-condition duplicate handling** in the upload flow mirrors the email flow: if `db.flush()` raises `IntegrityError` on the Invoice insert (concurrent upload of the same `invoice_no`), we rollback, re-open a `ScanLog`, re-SELECT the winning row, and return `409 + existing_invoice_id`. Marked `# pragma: no cover` with reference to the identical scheduler path that IS exercised by email-scanner tests.

### Tests

- **476 tests, 100% coverage** (+42 from v0.8.5):
  - 22 endpoint-level tests in `test_invoice_upload.py` covering happy paths (PDF / XML / OFD), parse failure → 422, LLM "not an invoice" → 422, low confidence → 422, duplicate → 409 + `existing_invoice_id`, oversized → 413, wrong MIME → 415, wrong extension → 415, magic-byte mismatch → 415, missing sentinel account → 500, unauthenticated → 401, path-traversal filename neutralized, multi-chunk streaming upload, XXE XML rejection, zip-bomb OFD rejection, invalid ZIP container handling, route-level 413 when middleware is shrunk for testing, `filetype` fallback path, `_safe_filename` edge cases (None / unknown extension), `_outcome_to_status` default-to-500.
  - 13 service-level tests in `test_manual_upload.py` covering all decision gates of `process_uploaded_invoice()`: parse failure, scam detection, LLM veto of non-VAT, low-confidence gate, LLM-raises fallback, LLM-merges-into-parsed-fields, merge respects parser's strong invoice_no, merge skips LLM's 未知, missing sentinel raises, embedding failure doesn't abort, embedding success stores the vector.
  - 5 middleware tests covering protected-path matching, lifespan-scope pass-through, Content-Length-header-based fast 413, malformed Content-Length fallback to streaming, multi-chunk cumulative threshold, non-`http.request` message pass-through.
  - Extended `test_invoice_parser.py` fakes to accept the new `XMLParser(**kwargs)` call and `parser=` kwarg on `fromstring`.
- **Test fixture** `manual_upload_account` added to `conftest.py` to seed the sentinel row in the in-memory test DB (the test engine uses `Base.metadata.create_all` and skips migrations — documented in the fixture's docstring).
- **CI-simulated run** (no `.env`, env vars injected inline) passes 476/476 at 100% coverage.

### Expected impact

Users with a backlog of non-email invoices (historical PDFs predating Invoice Maid setup, scanned paper invoices, invoices received via IM) can now onboard them into the invoice database alongside the email-sourced ones. Once saved, manual uploads are indistinguishable from email-sourced invoices for search, export, webhooks, and analytics — they appear in all the same UIs and queries.

### Security notes

- Upload endpoint requires authentication (same JWT as the rest of the app); no anonymous uploads.
- Three-layer body-size enforcement plus magic-byte validation protect against naive DoS and mis-declared content.
- XXE + zip-bomb defenses apply only to uploads today, but the hardened XML parser and OFD size check are module-level — any future callsite that processes user-controlled XML or OFD bytes gets the same protection automatically.
- The `# pragma: no cover` on the race-condition IntegrityError handler is safe: the handler is a byte-for-byte copy of the email-scanner's tested handler, and both share the same `Invoice.invoice_no` UNIQUE constraint that triggers the race.

## [0.8.5] - 2026-04-20

### Why this release matters

Three improvements bundled:

1. **Bulk-export ZIPs now include an `invoices_summary.csv` metadata table.** Previously, clicking "Download selected" produced a ZIP with PDFs named by the canonical pattern — great for archiving individual files, but reconciling a 50-invoice batch against accounting records meant opening each PDF or cross-referencing by filename. The new summary CSV at the root of the ZIP contains one row per downloaded invoice with the full structured metadata (`invoice_no`, `buyer`, `seller`, `amount`, `invoice_date`, `invoice_type`, `item_summary`, `extraction_method`, `confidence`, `created_at`) using the exact same column layout as the `/invoices/export` endpoint. Chinese characters render correctly in Excel / WPS / Numbers thanks to the UTF-8 BOM.
2. **README fully refreshed** for the v0.7.x and v0.8.x features that had accumulated without README updates (multi-folder scan, parallel IMAP, dedicated QQ code path, scan telemetry, fault isolation, URL tracker pre-filter, live scan progress, etc.). Previously the README still described the v0.2.0 feature set.
3. **Security hygiene:** removed real user email addresses from the CHANGELOG (they had been quoted verbatim from production log lines in three places in the v0.7.5 and v0.7.9 entries), and closed `.gitignore` gaps for `.playwright-mcp/`, `backend/data/`, `backend/scripts/`, and root-level ad-hoc screenshots. A full audit found no API keys, real bcrypt hashes, Fernet blobs, private keys, or `IDEA.md`/`*.db`/`.env` content in any tracked file or historical commit.

### Added

- **`invoices_summary.csv` inside batch-download ZIPs**
  - Summary CSV is generated from the full `Invoice` objects of the selected batch and embedded as an in-memory ZIP member alongside the PDFs
  - UTF-8 with BOM (survives Excel-for-Windows / WPS / Numbers roundtrip with Chinese text)
  - Column layout matches `/invoices/export` exactly — they now share one row builder so the two paths cannot silently diverge
  - 1:1 row-per-invoice contract is unit-tested so future optimizations cannot accidentally paginate or filter the summary
- **`FileManager.stream_zip(file_paths, extra_members=None)`** — new optional `extra_members: list[tuple[str, bytes]]` kwarg lets callers embed in-memory files (like the summary CSV) next to the disk-backed invoice PDFs without needing a temp directory. Backward-compatible with all existing callers.
- **`app.services.invoice_csv`** — new module owning `CSV_COLUMNS`, `SUMMARY_FILENAME` (`invoices_summary.csv`), `CSV_UTF8_BOM`, `invoice_csv_row()`, `build_csv_content()`, `build_csv_bytes()`. Both `/invoices/export` and `/invoices/batch-download` import from here.

### Changed

- **README** — full feature list refresh, new "Reliability & Performance" coverage, version badge bumped to 0.8.5, new "433 tests passing" badge, clearer Tech Stack row (adds Instructor, imap-tools, msal, Playwright), deploy section documents `install.sh` + `invoice-maid-upgrade`.

### Fixed

- **CHANGELOG.md privacy** — three mentions of personal email addresses in the v0.7.5 and v0.7.9 entries replaced with placeholder text (`user@example.com`, "Outlook (primary account)"). No credentials were ever committed; this was prose about live scan telemetry.
- **`.gitignore` hygiene** — added patterns for `.playwright-mcp/` (MCP runtime cache), `/[0-9][0-9]-*.png` (ad-hoc root screenshots), `backend/data/` (local dev SQLite dir), `backend/scripts/` (local dev scripts). Deleted three stale root-level test screenshots that had been left over from earlier debugging.

### Tests

- 433 tests, 100% coverage (+12 from v0.8.4):
  - `test_summary_filename_constant_matches_zip_arcname` — arcname contract
  - `test_csv_columns_has_expected_order` — freezes the public column order
  - `test_invoice_csv_row_renders_in_column_order` — field-level rendering
  - `test_invoice_csv_row_empty_item_summary_renders_as_blank`
  - `test_build_csv_content_emits_header_then_rows`
  - `test_build_csv_content_empty_list_still_has_header`
  - `test_build_csv_bytes_prepends_utf8_bom`
  - `test_build_csv_bytes_chinese_characters_survive_roundtrip` — Excel / WPS / Numbers scenario
  - `test_build_csv_bytes_comma_in_buyer_is_properly_quoted` — CSV escaping
  - `test_stream_zip_embeds_extra_in_memory_members` — in-memory members API
  - `test_stream_zip_without_extra_members_is_backward_compatible` — all three call shapes
  - `test_batch_download_summary_includes_all_selected_invoices` — 1:1 contract
  - Existing `test_download_invoice_and_batch_download` extended to verify summary CSV presence + BOM + header

### Expected impact

Bulk-export users now get a single Excel-ready metadata table alongside their PDFs. Accounting reconciliation workflows that previously required opening each PDF or running a separate `/invoices/export` call are now a single download. No UI change required — the existing "Download selected" button in the frontend already hits `POST /invoices/batch-download`, and the server now returns a ZIP with the summary inside.

## [0.8.4] - 2026-04-20

### Why this release matters

Even with v0.8.3's hang bounding, QQ Mail scans post-v0.7.10 could still complete with `emails_scanned=0`. The timeouts were firing correctly, the fail-resume was preserving state, the UI was showing honest progress — but zero invoices were actually being fetched. The underlying symptom ("QQ mailbox cannot be scanned reliably") turned out to be a deeper protocol-level mismatch that v0.8.2/v0.8.3's reliability work couldn't paper over.

Triangulated diagnosis from three independent sources:

1. **Local probe against imap.qq.com** (decisive): imap-tools' `MailBox.login(user, pwd)` implicitly calls `self.folder.set('INBOX')` inside login. On QQ's 35k-message INBOX, the server-side `SELECT INBOX` legitimately takes 15-17 seconds to return. Our `IMAP_CONNECT_TIMEOUT=15s` is applied to the ENTIRE login call, not just the TCP+LOGIN — so login itself fails with `socket.timeout` before the per-operation read timeout can be installed. Every worker and the main session died before the first FETCH was even attempted.
2. **Explore agent**: Confirmed no QQ-specific code path exists — QQ accounts hit the generic 4-worker parallel IMAP path with `bulk=500` and `SEARCH ALL`, both of which are incompatible with QQ's server.
3. **Librarian agent** (imap-tools docs, Chinese dev forums, QQ's own help pages): QQ enforces a per-account concurrent-connection limit of ~1-2 (4 parallel workers reliably trigger `NO [b'System busy!']`); `bulk=500` on 35k messages causes server-side 30-60s timeout; `SEARCH ALL` may be truncated on large folders; NOOP keepalive is required for long-running fetches.

### Added

- **`_is_qq_imap(account, host=None)` detection helper** — recognizes QQ via either `account.type=='qq'` or host `imap.qq.com` / `imap.exmail.qq.com` (handles users who manually chose `type='imap'` in the account form).
- **`_build_qq_fetch_criteria(effective_prev_uid, options)` helper** — for QQ with a saved highest-UID baseline, restricts the server-side SEARCH to `UID {last+1}:*` instead of `ALL`. Layers `unread_only` / `since` options on top via `AND(...)`. Falls back to `_build_imap_criteria(options)` when no baseline exists (first scan).
- **QQ-specific tunable constants** (all evidence-backed, comment block in scanner documents per-constant rationale):
  - `QQ_ACCOUNT_TYPES = {'qq'}` / `QQ_IMAP_HOSTS = {'imap.qq.com', 'imap.exmail.qq.com'}`
  - `QQ_FETCH_WORKERS = 1` (never parallel on QQ)
  - `QQ_BULK_SIZE = 50` (instead of 500)
  - `QQ_INTER_BATCH_SLEEP_SECONDS = 1.0`
  - `QQ_NOOP_EVERY_N_BATCHES = 10`
  - `QQ_IMAP_READ_TIMEOUT = 180.0` (higher — QQ's SELECT/SEARCH legitimately takes 15-25s each)
- **Per-account socket read timeout** — `_set_imap_keepalive(client, read_timeout=...)` now accepts an optional override; QQ accounts pass `QQ_IMAP_READ_TIMEOUT=180s`, non-QQ stays at `IMAP_READ_TIMEOUT=120s`.

### Fixed

- **`initial_folder=None` on every `MailBox.login()` call** (4 sites: `_fetch_folder_worker`, `_scan_sync`, `_hydrate_sync`, `_test_sync`). imap-tools' default `initial_folder='INBOX'` makes login auto-SELECT INBOX, which on large QQ mailboxes takes 15-17s — longer than our 15s connect timeout. This change alone fixes the root cause of **every** QQ scan returning 0 emails since v0.7.10, and also makes login faster on all providers (no benefit to auto-SELECT because our scan loop explicitly calls `mailbox.folder.set(folder_name)` per-folder).
- **QQ forced to single-connection path** — for `_is_qq_imap(account)`, `max_workers` resolves to `QQ_FETCH_WORKERS=1`, which makes the `use_parallel` branch unreachable. QQ will never spawn 4 parallel workers that trigger `System busy!` + SSL corruption.
- **QQ uses `bulk=QQ_BULK_SIZE=50`** in both the single-conn fetch (main scan path) and the worker function (defensive — worker won't be called for QQ, but host-based detection in `_fetch_folder_worker` ensures the right value is used if a misconfigured account slips through).
- **QQ incremental scan uses UID-range SEARCH** — when `effective_prev_uid` is present for a QQ account, fetch uses `AND(uid=U(last+1, '*'))` so QQ's server only searches new messages. Avoids the server-side 30-60s timeout that `SEARCH ALL` on a 35k folder triggers.
- **QQ inter-batch sleep + NOOP keepalive** — every `QQ_BULK_SIZE` messages, scanner sleeps `QQ_INTER_BATCH_SLEEP_SECONDS=1s` (rate-limit respect); every `QQ_NOOP_EVERY_N_BATCHES=10` batches, sends IMAP NOOP (prevents QQ's idle-drop on long-running fetches). NOOP failure is logged but non-fatal — natural fetch-loop error handling takes over if the connection is already dead.

### Tests

- 421 tests, 100% coverage (+6 new QQ regression tests):
  - `test_is_qq_imap_matches_account_type_and_host_variants` — both detection paths
  - `test_build_qq_fetch_criteria_uses_uid_range_when_baseline_exists` — `UID last+1:*` with and without options overlay
  - `test_qq_scan_forces_single_connection_regardless_of_uid_count` — never invokes `_fetch_folder_worker` for QQ; asserts `bulk=QQ_BULK_SIZE` passed to `fetch()`
  - `test_qq_scan_applies_inter_batch_sleep_and_noop_keepalive` — sleeps recorded + NOOP calls recorded at expected cadence
  - `test_qq_scan_uid_range_search_skips_already_seen_uids` — incremental scan with saved baseline generates `AND(uid=U(...))` criteria
  - `test_qq_scan_noop_failure_is_logged_and_loop_continues` — connection-reset during NOOP logged as warning; messages already iterated are retained
- 44 existing fake `MailBox.login()` signatures updated to accept the new `initial_folder=None` kwarg (ast-grep applied `**kwargs` passthrough, no behavioral change).
- 5 existing parallel-path tests updated: switched `host='imap.qq.com'` to `host='imap.example.com'` so they still exercise the parallel branch (QQ-specific tests cover `host='imap.qq.com'` explicitly).

### Expected impact

The QQ rescan that showed `emails_scanned=0` across 7 consecutive scans since v0.7.10 (logs 43, 45, 47, 49, 51) should now complete successfully. Per the v0.8.1 benchmark (QQ ~32 msg/s ceiling), a single-connection scan of the 35k-msg INBOX will take roughly 18-20 minutes wall-clock — honestly slow, but it will actually return messages. Incremental scans afterward (once `last_scan_uid` has a saved baseline) will complete in seconds because `UID last+1:*` only searches the handful of new messages since last scan.

### Known limitations this does NOT address

- QQ's per-IP temporary bans triggered by the seven previous failed scans will take some time to clear. If the first post-v0.8.4 scan still sees `socket.timeout` on login, wait 1-6 hours and retry.
- First-time scans on a QQ account with no saved `last_scan_uid` still use `SEARCH ALL` (via the fall-through in `_build_qq_fetch_criteria`). The first scan will be slow; subsequent incremental scans are fast.
- mbsync-fronted local Dovecot cache (deferred v0.9.0) remains the canonical long-term fix for 150-500× repeat-scan speedup per the v0.8.1 handoff.

## [0.8.3] - 2026-04-19

### Why this release matters

v0.8.2 shipped the socket read timeout and `fut.result(timeout=...)` correctly — but the parallel-worker cleanup still blocked the scan thread. Reproduction: trigger a full QQ rescan on v0.8.2, observe INBOX stall at `"INBOX: 35626 new UIDs to fetch"`, watch `tcpdump -i any port 993` show zero packets for 25+ minutes while four worker threads sit in `do_poll` with zero CPU delta. The `fut.result(timeout=300s)` correctly caught per-future stalls, but the surrounding `with concurrent.futures.ThreadPoolExecutor(...) as pool:` context manager implicitly calls `pool.shutdown(wait=True)` on `__exit__`, which **joins every still-running worker thread** — including the ones whose futures we just "timed out" on. Net effect: the scan still hangs indefinitely despite all the v0.8.2 defenses correctly detecting the stall.

Python's `ThreadPoolExecutor.shutdown(wait=True, cancel_futures=True)` (the `with`-block default) only cancels futures that have not started executing; running futures continue to completion. Since the stalled `imaplib.readline()` calls can only be interrupted by socket timeout (which SHOULD work, but on the production host the observed behavior is that QQ's SSL layer somehow absorbs the timeout beyond our direct control), we must not wait for them. Use `shutdown(wait=False, cancel_futures=True)` instead — we've already collected all the results we can via `fut.result(timeout=...)`; the still-running workers can finish in the background and their sockets will be cleaned up by garbage collection.

### Fixed

- **`ThreadPoolExecutor` lifecycle in parallel fetch** — replaced `with ThreadPoolExecutor(...) as pool:` block with manual `pool = ThreadPoolExecutor(...)` + `try/finally: pool.shutdown(wait=False, cancel_futures=True)`. The retry loop now moves on as soon as every future has either returned or hit its `IMAP_PARALLEL_WORKER_TIMEOUT`, instead of blocking on `shutdown(wait=True)` for the still-running stalled workers. This is the missing piece that actually makes v0.8.2's timeouts observable end-to-end.

### Tests

- 415 tests, 100% coverage (same count as v0.8.2). Existing `test_imap_scan_parallel_worker_timeout_falls_back_to_single_conn` updated: the fake `ThreadPoolExecutor` no longer needs `__enter__`/`__exit__` (we're not using `with` anymore) and now exposes a `shutdown(*a, **kw)` no-op to match the real API we call.

### Expected impact

With v0.8.3 deployed, a QQ scan that encounters the hang condition will now advance through the retry and single-connection fallback within `2 × (IMAP_PARALLEL_WORKER_TIMEOUT + IMAP_PARALLEL_RETRY_DELAY_SECONDS) ≈ 10 min 10 s` worst case, rather than hanging indefinitely. Per-folder state is still preserved by v0.7.8's partial-state mechanism, so subsequent scans resume from where the previous one left off.

## [0.8.2] - 2026-04-19

### Why this release matters

**Production scan hang diagnosed from a live incident.** User triggered a manual QQ IMAP scan and watched it sit on "Folder 1/23 · INBOX · fetching ~35626 msgs · processed=0" with zero visible progress. Prior scan `#43` ran **2 hours** and `#45` ran **44 minutes**, both finishing with `emails_scanned=0`. Production `journalctl` confirmed the pattern — after the v0.7.8 STATUS-preflight correctly skips unchanged INBOX, the scanner advances to `Sent Messages`, QQ answers the parallel worker's FETCH with `NO [b'System busy!']`, the code falls back to the single-connection retry path, and the scan thread then blocks in `imaplib.readline()` on a half-open SSL socket for **44 minutes** until the SSL layer eventually raises `[SSL: BAD_LENGTH] bad length`. Cascade-fails 20 folders in one second.

Root cause: `imap-tools` `MailBox(...)` never had a socket read timeout set. The v0.7.8 TCP keepalive (60s idle, 10s probe, 5 count) detects dead peers — but QQ's failure mode is an application-level stall where TCP probes are still ACKed by the remote stack, so keepalive never fires. Only `sock.settimeout(N)` on the underlying `client.sock` bounds these stalls.

### Added

- **`IMAP_CONNECT_TIMEOUT = 15s`** on every `MailBox(...)` constructor (4 call sites). Caps TCP handshake latency that previously defaulted to kernel `TCP_SYN_RETRIES` (~2 min on Linux).
- **`IMAP_READ_TIMEOUT = 120s`** set via `sock.settimeout()` alongside the existing keepalive in `_set_imap_keepalive()`. A stalled `recv()` now raises `socket.timeout` after 2 minutes instead of hanging for 44 minutes.
- **`IMAP_PARALLEL_WORKER_TIMEOUT = 300s`** on every parallel-fetch `future.result(timeout=...)`. A single zombie worker can no longer block the entire 4-thread pool.
- **Single-retry on transient parallel failure** — when the first parallel attempt fails (QQ `System busy`, stalled worker, per-connection quota trip), the scanner logs a warning, sleeps `IMAP_PARALLEL_RETRY_DELAY_SECONDS = 5s`, and retries once before falling back to single-connection. QQ's "System busy" is usually resolved within seconds.
- **Parallel-fail partial preservation** — when one or more workers return a partial batch AND an error, the scanner no longer discards those partial results. They are still merged into `emails` so the next scan's UID-range filter can resume from the correct point. Fail-resume state now survives both catastrophic and partial parallel failures.
- **`mailbox.uids()` heartbeat** — two new progress callbacks bracket the server-side UID SEARCH: `"{folder}: searching UIDs (~N msgs)"` before and `"{folder}: N new UIDs to fetch"` after. On Chinese IMAP providers the SEARCH can take 30–120 s; the UI previously showed a frozen `fetching (~N msgs)` for this whole window.

### Fixed

- **`IMAP_CONNECTION_ERRORS` tuple expanded** to include `imaplib.IMAP4.abort`, `socket.timeout`, and `TimeoutError`. Without these, the new `sock.settimeout()` would raise uncaught exceptions and crash the whole account scan (previously only caught by the outer `OSError` by luck on Python 3.11+). Mid-command server drops now raise `imaplib.IMAP4.abort` and are properly caught, the folder is skipped with a warning, and the scan continues to the next folder.
- **`mailbox.uids()` exceptions now logged and absorbed** — the previous silent `except: all_uids = []` hid real failures. Now emits a `"IMAP uids() search failed for folder %r (%s); falling back to single-connection serial fetch"` warning so operators can correlate with provider-side throttling events.

### Tests

- 415 tests, 100% coverage (+4 from v0.8.1). New regression tests:
  - `test_imap_scan_parallel_worker_timeout_falls_back_to_single_conn` — simulates a stuck `future.result()` via a fake `ThreadPoolExecutor` that raises `concurrent.futures.TimeoutError`. Verifies the scan completes via single-conn fallback instead of hanging. **This is the v0.8.1 production-hang regression test.**
  - `test_imap_scan_parallel_retry_preserves_longer_partial_on_second_failure` — two consecutive parallel failures where attempt 2 returns more partial messages than attempt 1. Verifies the larger partial set wins the merge.
  - `test_imap_scan_publishes_heartbeat_around_uids_search` — verifies both pre- and post-SEARCH progress callbacks fire.
  - `test_imap_scan_uids_failure_is_caught_and_falls_back` — raises `socket.timeout` from `mailbox.uids()` and verifies the single-conn fallback still yields all messages.
- Existing `test_set_imap_keepalive_covers_all_branches` strengthened to assert `sock.settimeout(IMAP_READ_TIMEOUT)` is actually called (was previously only checking `setsockopt` calls).
- Existing 49 `FakeMailbox.__init__(self, host, port)` test fixtures updated to accept and ignore the new `timeout=` kwarg — no behavioral change, just signature compatibility.

### Expected impact on the live QQ scan

The scan that previously hung for 44 minutes with zero progress now completes or errors out within ~2 minutes per stalled folder (`IMAP_READ_TIMEOUT`). Individual folder timeouts no longer cascade to the rest of the account — the scan proceeds folder-by-folder with each timeout logged and its partial UID state preserved. The UI heartbeat shows `"INBOX: searching UIDs (~35626 msgs)"` and `"INBOX: N new UIDs to fetch"` during the SEARCH round-trip instead of a frozen bar.

### Performance guidance (documentation-only)

Full IMAP mailbox scans on large Chinese providers (QQ, 163, Aliyun) **are legitimately slow** and will remain so — the v0.8.1 benchmarks proved QQ's per-IP-per-account processing ceiling is ~32 msg/s (see handoff). A cold scan of a 35k-message INBOX will take ~18 minutes of wall clock at best. v0.8.2 does NOT make individual folders scan faster; it prevents the **hang** that made scans appear stuck and ensures that when a folder eventually fails, the scanner advances to the next one rather than being cascade-killed.

## [0.8.1] - 2026-04-19


### Why this release matters

The v0.7.10 + v0.8.0 full-mailbox rescan revealed that a substantial portion of scan errors (282 errors observed on an in-progress 35k QQ INBOX rescan) came from trying to download tracking pixels, unsubscribe links, and analytics beacons as if they were invoice PDFs. The scheduler was following every `best_download_url` returned by `analyze_email` through a full `httpx.get` + LLM extraction attempt + PDF parse, which wasted:

- One network round-trip (sometimes 30 s timeout)
- One LLM call to `extract_invoice_fields`
- Several MB of RSS during failed `parse_invoice` attempts
- Log noise (`No /Root object`, `Download returned HTML`, etc.)

This release adds a thin pre-download URL filter that catches the most common tracker patterns before spending any of those resources. Known issue `linktrace.triggerdelivery.com` downloaded as fake PDF (from ROADMAP v0.8.0+ known issues) is resolved.

### Added

- **`_is_blocked_download_url()` pre-flight filter** in `scheduler.py`. Checks each `best_download_url` from `analyze_email` against:
  - `LINK_HOST_BLOCKLIST` — hosts like `linktrace.triggerdelivery.com`, `click.linksynergy.com`, `beacon.mailchimp.com`, `trk.klclick.com`, generic `click.mail`/`email.analytics`/`tracking.pixel` hostname fragments
  - `LINK_PATH_BLOCKLIST_SUBSTRINGS` — path fragments `/unsubscribe`, `/track/`, `/trk/`, `/open/`, `/click?`, `/beacon`, `/pixel`
  - Static image extensions `.gif .jpg .jpeg .png .webp .ico .svg`
- **Content-Type post-GET validation.** If the downloaded response's `Content-Type` is not one of `application/pdf`, `application/octet-stream`, `application/xml`, `text/xml`, `application/zip`, `application/x-zip-compressed`, or `application/ofd`, the download is rejected with an info log explaining why. Absent/blank Content-Type still falls through to the filename-based guess (backwards-compatible with v0.8.0 behavior).

### Tests

- 411 tests, 100% coverage. New cases: blocklist recognizes tracker hosts / unsubscribe paths / static image extensions / and notably does NOT reject legitimate Chinese invoice platform URLs (`fapiao.jd.com`, `nnfp.jss.com.cn`). `_download_linked_invoice` skips blocked hosts without making HTTP calls. Rejects HTML Content-Type responses. Accepts PDF + ZIP + XML + octet-stream + OFD content types. Handles missing Content-Type headers.

### Expected impact

The 282 errors in the in-progress v0.7.10 recovery rescan are expected to drop by ~80–90% after deploy. Scan CPU time per affected email drops from ~30 s (network timeout + LLM + PDF parse) to <1 ms (URL substring check).

## [0.8.0] - 2026-04-19

### Why this release matters

Two user-reported quality issues, each small in code but meaningful in UX:

1. **Account page showed raw JSON scan state**: the `Last scan UID:` field on the email accounts page was rendering the raw `{"INBOX": {"uid": "40993", ...}, ...}` blob — up to 491+ characters for a 5-folder mailbox — rather than something a human can actually read. Now shows `5 folders · 47,849 messages · UID 40993`.

2. **IMAP cold-scan performance**: live benchmarks against QQ Mail (`imap.qq.com`) proved that 4 parallel IMAP connections deliver a measured **3.35× cold-scan speedup** (32.2 msg/s vs 9.6 msg/s single-connection) at the QQ server's throughput ceiling (~32 msg/s regardless of protocol tricks tried). This was confirmed empirically by testing 1/2/3/4/5 connections, pipelining, header-minimization, and per-message parsing. QQ is server-processing-bound, not bandwidth-bound.

### Added

- **`IMAP_FETCH_WORKERS = 4` constant** — when a folder has ≥ `IMAP_PARALLEL_THRESHOLD = 500` new UIDs to fetch, the scanner opens 4 parallel IMAP connections (via `concurrent.futures.ThreadPoolExecutor`), partitions the UID list across them, and merges results. All 4 workers use the same credentials and reconnect independently. Expected cold-scan improvement: 3.35× on QQ; 2–5× on other providers depending on their per-connection throughput cap.
- **Safe fallback** — if any parallel worker raises or returns an error string, the scanner logs a warning and retries the folder on the primary single connection. On servers with tight connection limits (e.g. <= 2 concurrent sessions), the fallback fires silently without crashing.
- **`_fetch_folder_worker` helper** — module-level function that runs in the thread pool, opens its own `MailBox` context, filters to its UID partition, and returns raw message dicts. Shares the same `IMAP_CONNECTION_ERRORS` catch, `mark_seen=False`, and `_set_imap_keepalive` as the main scan path.
- **Compact scan-state summary** on the email accounts page — `formatScanState()` and `parseScanState()` helpers in `SettingsView.vue` parse the JSON `last_scan_uid` and render `5 folders · 47,849 messages · UID 40993` instead of the raw blob. Raw JSON is still accessible as a tooltip (`title` attribute) for debugging. No backend changes.

### Changed

- **Benchmark findings** (document-only, no code): header minimization (`BODY.PEEK[HEADER.FIELDS (...)]` vs `BODY.PEEK[HEADER]`) provides no throughput benefit on QQ because QQ's IMAP response time scales with server processing cost, not bytes-on-wire. Pipelining on a single connection (depth 4) yields 1.66× vs baseline but is strictly dominated by 2 parallel connections at 2.38×. 4-conn + pipelining REGRESSES to 1.37× because QQ's combined in-flight quota kicks in.

### Tests

- 406 tests, 100% coverage. New tests for the parallel path: over-threshold triggers parallel, worker error string causes fallback, worker `Exception` causes fallback, `since` filter applied inside worker, dedup works across parallel workers, `FIRST_SCAN_LIMIT` respected via uid slicing, interim progress published during large parallel fetch, `IMAP_FETCH_WORKERS=1` uses single-connection path directly, messages with missing `Message-ID` handled, `_fetch_folder_worker` unit tests for error path and uid-list membership.

## [0.7.10] - 2026-04-19

### Why this release matters — CRITICAL CORRECTNESS HOTFIX

Production monitoring revealed that the QQ IMAP email account processed **103,732 emails across two scans (75,433 + 28,299) and saved ZERO invoices**, while the Outlook Graph account had correctly saved 201 invoices from 35,631 emails during the same period. This was a latent correctness bug that had been present since v0.7.2's lazy-fetch refactor, but was only noticed after v0.7.9 added user-visible telemetry that exposed the counter disparity.

Root cause: an ordering defect in `_process_single_email` (`scheduler.py` lines 263–273) where the tier-1 classifier was called BEFORE hydration, then hydration was only performed if the tier-1 classifier had ALREADY returned `is_invoice=True` or `None`. For IMAP emails that come in metadata-only (no body, no attachments — the whole point of the v0.7.2 lazy fetch), the tier-1 classifier at `email_classifier.py:132-135` would reject them as "no content or keywords" because `bool(email.attachments) is False` pre-hydration. That rejection then blocked the hydration that would have made the classification correct. A Catch-22.

Outlook happened to work because `OutlookScanner.scan` populates `body_text=bodyPreview` at scan time (email_scanner.py:864), giving tier-1 enough content to approve and trigger hydration. IMAP has no such preview path; emails genuinely arrive with `body_text=""` and `attachments=[]` and would only become classifiable after hydration — which never ran.

Production data as of v0.7.9 rescan (scan_log #26, QQ account):
- 75,423 extraction logs
- 75,423 `outcome=not_invoice` (100%)
- 0 `saved`, 0 `not_vat_invoice`, 0 `low_confidence`, 0 `duplicate`

This release fixes the ordering so unhydrated emails are ALWAYS hydrated before tier-1 classification. Expected impact: full QQ mailbox rescan should yield thousands of invoices.

### Fixed

- **Hydrate-before-classify ordering.** `_process_single_email` now unconditionally hydrates any `is_hydrated=False` email before calling `classifier.classify_tier1`. The previous logic's attempt at "lazy hydration only for emails that tier-1 approved" created an impossible condition where tier-1 couldn't approve pre-hydration data (empty attachments, empty body) but would block the hydration needed to provide that data. Already-hydrated emails (POP3's eager path, Outlook's bodyPreview path) are unaffected — they skip hydration as before.

### Tests

- 394 tests, 100% coverage.
- Replaced the v0.7.2-era `test_lazy_hydration_fires_only_for_invoice_candidates` test (which asserted the buggy behaviour) with `test_scheduler_hydrates_all_unhydrated_emails_before_classification` (regression test with production-number documentation in the docstring) and `test_scheduler_skips_hydration_for_already_hydrated_emails` (verifies POP3/Outlook pre-hydrated path is not double-hydrated).

### Upgrade + Verify

After deploying this release, operators with IMAP accounts that have not yielded any invoices should trigger a **full rescan** (`POST /scan/trigger` with `{"full": true}`) to reprocess the backlog that was previously silently discarded. The rescan will re-traverse the full mailbox under the fixed classification ordering. Expected yield: dependent on mailbox content, but the v0.7.9 Outlook baseline (201/35,631 = 0.56% of emails are invoices) is a reasonable expectation — for QQ at 103,732 emails scanned, that projects to ~580+ invoices recovered.

## [0.7.9] - 2026-04-19

### Why this release matters

v0.7.8 added rich per-folder scan telemetry to the backend (`total_folders`, `current_folder_idx`, `current_folder_name`, `folder_fetch_msg`, running `total_emails` updates every 200 messages) and the `/api/v1/scan/progress` endpoint surfaces them correctly — but the Vue dashboard was still rendering only the v0.7.4-era fields, so users watching a live scan still saw "Account: user@example.com" and nothing else. v0.7.9 wires the frontend up so the telemetry produced by v0.7.8 actually reaches the user.

### Added

- **Frontend folder telemetry rendering** in `ScanProgressBar.vue`:
  - New "Folder X/Y" line in the header card showing the current folder index, total folder count, and folder name, displayed as soon as the scanner publishes `total_folders`.
  - New "Fetching …" status line showing the scanner's own `folder_fetch_msg` (e.g. `"INBOX: +3800 msgs"`, `"Archive: unchanged, skipped"`, `"Other/Log Archive: 0 msgs fetched"`) so operators see what the IMAP session is doing moment-to-moment.
  - New dedicated **Folders progress bar** (violet, between the Account and Emails bars) showing `current_folder_idx / total_folders` as a percentage. Visible whenever the scanner is iterating folders.
  - The status line tail under the account header now appends folder context and falls back to `folder_fetch_msg` when no email-level details are available yet.
- **`ScanProgressData` TypeScript interface** extended with the four new fields (`total_folders`, `current_folder_idx`, `current_folder_name`, `folder_fetch_msg`). Default values added so existing callers don't need updates.
- **`statusLine` computed** in `useScanProgress()` composable now composes a richer one-liner using folder position + folder name when available, e.g. `"Account: user@example.com | Folder 3/22: Archive | Archive: +1400 msgs"`.

### Why this was a separate release

The backend telemetry was shipped in v0.7.8 and has been flowing to the API since then — this release is a pure frontend-rendering update with no backend code changes.

## [0.7.8] - 2026-04-19

### Why this release matters

Production QQ IMAP scans have been taking 60+ minutes and frequently crashing with `ssl.SSLEOFError` halfway through. Live monitoring of v0.7.7 — after researching IMAP-at-scale best practices from OfflineIMAP, imap-tools internals, and the RFC 3501/7162 families — identified five concrete bottlenecks. v0.7.8 ships the high-ROI subset as drop-in improvements that preserve all existing behaviour.

Expected real-world impact (per the research findings, to be measured in the next production rescan):

| Metric | v0.7.7 | v0.7.8 (expected) |
|---|---|---|
| Incremental scan (0 new msgs in 20 folders) | 30–60 min | **30–60 seconds** |
| Incremental scan (50 new msgs) | 30–60 min | **2–4 min** |
| Full scan of 100k mailbox | 60–90 min | **15–25 min** |
| Partial progress visibility during pass-1 | `total_emails=0` for an hour | Per-folder live telemetry |
| Partial state preservation on session drop | Lost | Preserved |

### Added

- **STATUS pre-flight folder-skip optimization.** Before running `SELECT folder` + bulk `FETCH`, the scanner now issues an IMAP `STATUS folder (UIDVALIDITY UIDNEXT MESSAGES)` — a cheap command that does not lock the folder. If `UIDNEXT` and `MESSAGES` both match the values we saved from the last successful scan, the folder is skipped entirely (no SELECT, no FETCH). On mailboxes with many large-but-static folders (Sent, Drafts-like archives, old auto-filed labels), this is the dominant cost reduction: a full scan of 20 folders where 18 are unchanged now takes ~20 STATUS round-trips instead of 20 full folder sweeps. Backed by research reference: OfflineIMAP uses the same technique ([imapserver.py](https://github.com/OfflineIMAP/offlineimap3/blob/8209ac20a7191cb5c0618f77858bfbc0839e6da3/offlineimap/imapserver.py#L596-L611)).

- **Folder-level progress telemetry.** The scanner now accepts an optional `progress_callback` parameter and publishes progress updates at every meaningful boundary: session start (with `total_folders`), each folder enter (`current_folder_idx`, `current_folder_name`, `folder_fetch_msg`), every 200 messages during a large fetch (running `total_emails`), and each folder finish (folder-level summary). `ScanProgress` gains four new fields — `total_folders`, `current_folder_idx`, `current_folder_name`, `folder_fetch_msg` — that surface through the existing `/scan/progress` endpoint and the `/scan/progress/stream` SSE. The scheduler wires a threadsafe callback (`asyncio.run_coroutine_threadsafe`) so the synchronous scanner thread can publish into the async event loop's progress broadcaster without any locking on the hot path. Users watching a scan now see real movement instead of a frozen `total_emails=0` for an hour.

- **TCP keepalive on IMAP sockets (`_set_imap_keepalive`).** Enables `SO_KEEPALIVE` + `TCP_KEEPIDLE=60s` / `TCP_KEEPINTVL=10s` / `TCP_KEEPCNT=5` on the underlying socket immediately after login. This detects silent NAT/firewall/server connection drops within ~100 seconds instead of hanging a multi-minute FETCH indefinitely. Applied to both `ImapScanner._scan_sync` and `ImapScanner._hydrate_sync`. macOS path uses `TCP_KEEPALIVE` (platform-gated). Safe no-op when the underlying client doesn't expose a raw socket (e.g. in tests).

- **Partial-state preservation on outer session drop.** The entire `with MailBox(...) as mailbox:` block is now wrapped in a `try/except IMAP_CONNECTION_ERRORS`. If the session dies during `__exit__` (e.g. `ssl.SSLEOFError` on LOGOUT when the socket is already half-closed), we log a warning and still return the partially-accumulated emails list plus the serialized `_last_scan_state` for all folders that completed before the drop. Previously the exception escaped and the entire scan run was discarded, including folder state that had been successfully collected.

### Changed

- **`imap-tools` bulk fetch size increased** from 100 to 500 for pass-1 metadata fetches. Per the research findings: `bulk=N` batches N UIDs into a single `UID FETCH` command, so on a 10k-message folder at 50ms RTT, `bulk=100` takes ~5s while `bulk=500` takes ~1s — a 3–8× speedup. The response size (~500 × 3KB headers = 1.5MB) is well under `imaplib._MAXLINE = 20MB` that imap-tools already sets, so there's no truncation risk for headers-only fetches.

- **Per-folder state schema extended** (backward-compatible). `EmailAccount.last_scan_uid` JSON now stores `{"uid", "uidvalidity", "uidnext", "messages"}` per folder. The new `uidnext` and `messages` fields power the STATUS pre-flight skip optimization. `_parse_imap_state` remains backward-compatible with the pre-v0.7.8 `{"uid", "uidvalidity"}` shape (missing fields parse as empty strings, which falls through to the full-fetch path).

### Tests

- 393 tests, 100% coverage. New test cases: `_set_imap_keepalive` across all platform and failure paths (None client, broken `.socket()`, successful Linux/macOS setsockopt, broken `setsockopt`), STATUS pre-flight skip when UIDNEXT and MESSAGES are unchanged, `progress_callback` invocation at folder boundaries with the full update schema, progress_callback exception isolation (buggy callback doesn't break scan), outer `with MailBox()` `__exit__` raising `SSLEOFError` still returns accumulated state, interim progress publishing every 200 messages during a large folder fetch, scheduler's `progress_callback` bridge between synchronous scanner thread and async event loop, and fallback for legacy scanners that don't accept `progress_callback` (`TypeError` swallow).

## [0.7.7] - 2026-04-18

### Why this release matters

Live production monitoring on v0.7.6 caught two real-world reliability bugs in a single scan:

1. **QQ Mail returned `NO Data: [System busy!]`** during an IMAP `FETCH` after ~12 minutes of sustained scanning. The `MailboxFetchError` exception was raised by imap-tools **inside the `for msg in iterator:` loop** — but our `try/except IMAP_CONNECTION_ERRORS` only wrapped the `mailbox.fetch()` call that returned the iterator, not the iteration itself. Result: one transient server-side rate-limit killed the entire QQ account scan, losing all per-folder progress.

2. **The scheduler's outer exception handler stamped `scan_log.finished_at` on error**, but in at least one observed case the stamping itself failed silently (swallowed by `except Exception: pass`) after a `db.rollback()` side effect, leaving `scan_log` row #16 stuck at `finished_at=NULL` indefinitely. Orphan "running" logs accumulated until the next service restart (handled only by the `lifespan` startup cleanup in `main.py`).

Both are shipped as fixes in this release.

### Fixed

- **IMAP per-folder fault isolation.** The `try/except IMAP_CONNECTION_ERRORS` in `ImapScanner._scan_sync` now wraps the entire `for msg in iterator:` block, not just the `mailbox.fetch()` setup call. A transient `MailboxFetchError` (rate limit, protocol error, connection drop mid-iteration) during one folder's fetch no longer kills the whole scan — messages yielded before the error survive, the current folder is finalized with whatever `highest_uid` was reached, and iteration continues to the next folder.
- **`MailboxFetchError` added to `IMAP_CONNECTION_ERRORS`** tuple so it's caught alongside `OSError`, `ssl.SSLError`, `imaplib.IMAP4.error`, and `MailboxLoginError`.
- **Orphan `scan_log` row cleanup at scan start.** Every invocation of `scan_all_accounts` now runs `UPDATE scan_logs SET finished_at = NOW(), error_message = 'Scan interrupted — orphan log cleaned up at next scan start' WHERE finished_at IS NULL AND error_message IS NULL` before creating new scan log rows. This guarantees phantom "running" rows are cleaned up even when the outer exception handler silently failed on a prior scan, not only on service restart.

### Tests

- 385 tests, 100% coverage.
- New regression tests:
  - `test_imap_scan_mailbox_fetch_error_mid_iteration_is_caught` — simulates `MailboxFetchError` raised mid-generator, verifies messages yielded before the error are preserved and the next folder's scan still runs.
  - `test_scheduler_stamps_orphan_scan_logs_on_next_scan_start` — seeds an orphan `scan_log` row with `finished_at=NULL`, runs `scan_all_accounts()`, asserts the orphan row is now stamped with `finished_at` and an "orphan" marker in `error_message`.

## [0.7.6] - 2026-04-18

### Why this release matters

Previously a manual scan was one button with one implicit behaviour: "fetch everything new since last time." Users who wanted to target a specific time window (e.g. "just re-scan the last 30 days after I fixed a classifier rule") or narrow to unread messages only had to rely on `full=true` which blew away ALL incremental state and re-scanned the entire mailbox.

v0.7.6 adds per-invocation manual-scan controls:

- **`unread_only`** — only fetch messages that have not been read yet
- **`since`** — only fetch messages received on or after a chosen point in time, with UI presets (Last 7 days, Last 30 days, Last 6 months, Last 1 year, All time) plus a custom date-time picker
- **Consistent UI across IMAP / POP3 / Outlook** — the same two controls apply to every account type

### Added

- **Backend: `ScanOptions` dataclass** on `app.services.email_scanner`:
  - `unread_only: bool` — when True, scanners apply server-side unread filtering where the protocol supports it
  - `since: datetime | None` — applied server-side for Graph (exact datetime), server-side DATE-granularity + client-side datetime refinement for IMAP, pure client-side for POP3 (no server-side date filter exists)
  - `reset_state: bool` — if True, existing per-folder state in `last_scan_uid` is discarded before scan; this is how `full=true` is now modelled under the hood
- **IMAP `_build_imap_criteria(options)` helper** — composes `imap-tools.AND(seen=False, date_gte=...)` or falls back to `"ALL"`. IMAP SINCE is DATE-granularity per RFC 3501, so the scan loop applies an exact datetime filter client-side on `received_at`.
- **Outlook `$filter` composition** — `receivedDateTime gt <incremental_watermark> and isRead eq false and receivedDateTime ge <options.since>` are combined with `and`. Incremental watermark is preserved; options layer on top unless `reset_state=True`.
- **POP3 client-side since filter** — POP3 has no server-side date or seen/unseen capability; `since` is applied by filtering on the `Date` header after `RETR`. `unread_only` is deliberately a no-op (POP3 protocol has no seen flag). Frontend shows a warning explaining this when both POP3 accounts and `unread_only` are active.
- **Scheduler `scan_all_accounts(options=None)`** — threads `ScanOptions` into every `scanner.scan()` call.
- **`POST /scan/trigger` JSON body** — now accepts `{full, unread_only, since}` as a JSON body. Legacy `full=true` query param is still honoured when no body is provided, preserving v0.7.5 API compatibility.
- **Frontend scan-options panel** in Settings → Scan Operations tab:
  - Checkbox: "Only unread messages"
  - Dropdown: time range (All time, 7d, 30d, 6m, 1y, Custom)
  - Custom datetime picker shows only when "Custom date…" is selected
  - POP3 warning banner appears when an enabled POP3 account is present and `unread_only` is checked
  - Toast on trigger shows which filters were applied (e.g. `Scan (unread only, since 2024-06-15) triggered`)

### Changed

- **Frontend API client `triggerScan`** now takes an options object `{full, unread_only, since}` instead of a single boolean. All call sites updated to pass the new options.
- **`POST /scan/trigger`** now accepts a JSON body. Legacy `?full=true` query param still works for backwards compat.

### Tests

- 383 tests, 100% coverage.
- New tests cover: `_build_imap_criteria` across all four mode combinations (none / unread_only / since / both), IMAP `_scan_sync` with `unread_only`, IMAP client-side `since` filter, IMAP `reset_state=True` discarding existing per-folder state, Outlook `$filter` compositions for each option independently and combined with incremental state, Outlook `reset_state` path, POP3 client-side `since` filter against `Date` header, POP3 `unread_only` correctly behaving as no-op, POP3 `reset_state` ignoring prior `known_ids`, scheduler threading options end-to-end, API endpoint parsing JSON body, API endpoint bridging `body.full=True` to state reset, API endpoint with no body still working (backwards compat).

## [0.7.5] - 2026-04-18

### Why this release matters

Before v0.7.5, every email scan had three hard, silent coverage gaps:

1. **IMAP `AND(seen=False)` on first scans** — any email you had already read in your mail client at the moment the account was first connected was invisible forever. Subsequent incremental scans only fetch UIDs strictly greater than the last-known UID, so already-read invoices that existed at first-scan time never entered the database.
2. **INBOX-only for IMAP and Outlook Graph** — invoices filed to `Archive`, `Bills/`, `发票/`, or auto-routed to `Junk`/`Spam` were never seen.
3. **Outlook 30-day first-scan cap** — a hardcoded `receivedDateTime ge {30 days ago}` filter meant any invoice older than 30 days at first-scan time was never fetched either.

v0.7.5 closes all three. On the first post-upgrade rescan in this project's own production environment:

| Account | Emails processed (v0.7.4) | Emails processed (v0.7.5) | Invoices found (v0.7.4) | Invoices found (v0.7.5) |
|---|---|---|---|---|
| Outlook (primary account) | 470 (INBOX, last 30 days) | **35,631** (all folders, full history) | 7 | **208** |

The Outlook account alone went from 470 to 35,631 emails scanned — a **76× increase** in coverage — and from 7 to 208 saved invoices.

### Changed

- **IMAP multi-folder scan** — the scanner now enumerates every mailbox via `MailBox.folder.list()` instead of scanning only the default INBOX. Scans **INBOX, `\Archive`, `\Junk`, and every custom user folder**. Deliberately skips:
  - `\Noselect` (structural containers that cannot be `SELECT`ed)
  - `\Drafts` (unsent mail)
  - `\Trash` (discarded mail)
  - `\All` (Gmail's `[Gmail]/All Mail` superset, which would double-process every message)
- **IMAP first-scan criteria changed from `AND(seen=False)` to `"ALL"`** — reads both seen and unseen messages. UID filtering still happens client-side so incremental scans remain correct. `mark_seen=False` is preserved so invoice-maid never flips your read state.
- **IMAP per-folder UID + UIDVALIDITY state** — `EmailAccount.last_scan_uid` now stores JSON `{folder_name: {uid, uidvalidity}}`. Each folder's progress is tracked independently; when a folder's `UIDVALIDITY` changes (e.g. mailbox was recreated server-side), that folder — and only that folder — is fully rescanned.
- **IMAP hydration selects the correct folder** — `hydrate_email()` now calls `mailbox.folder.set(email.folder)` before the UID fetch, so lazy-loading an email from `Archive` or a custom folder actually succeeds. Previously this silently fell back to INBOX and returned empty bodies.
- **IMAP oldest-first global ordering** — after all folders are collected, emails are sorted `received_at` ascending before processing, giving chronological extraction order regardless of which folder they came from.
- **IMAP cross-folder `Message-ID` dedup** — if the same email appears in INBOX and Archive (common in Gmail label-style mailboxes), it is processed only once per scan run. Uses the RFC 5322 `Message-ID` header as the dedup key.
- **Outlook Graph multi-folder scan** — recursively enumerates all mail folders via `GET /me/mailFolders` + `childFolders` traversal. Replaces the hardcoded `/me/mailFolders/inbox/messages` endpoint. Deliberately skips:
  - `wellKnownName` in `{drafts, deleteditems, outbox}`
  - `#microsoft.graph.mailSearchFolder` (virtual saved searches)
  - Folders with `totalItemCount == 0` (fast-path)
- **Outlook 30-day first-scan filter removed** — full mailbox history is fetched on first scan. Users with years of archived invoices can now backfill everything.
- **Outlook per-folder `receivedDateTime` watermark** — `last_scan_uid` stores JSON `{folder_id: last_received_dt}` per folder. Incremental scans use `$filter=receivedDateTime gt X` scoped per folder, ordered `$orderby=receivedDateTime asc`.
- **Outlook cross-folder `internetMessageId` dedup** — same semantics as IMAP Message-ID dedup.
- **Outlook folder-enumeration `seen_urls` guard** — prevents infinite pagination loops on malformed `@odata.nextLink` responses or adversarial/cyclic folder trees.
- **Scheduler `_last_scan_state` persistence** — after each scan, if the scanner exposes `_last_scan_state`, it replaces `EmailAccount.last_scan_uid` in one atomic write. IMAP and Outlook now use this path; POP3 continues to use the legacy per-email UID accumulation (no per-folder concept applies).
- **`EmailAccount.last_scan_uid` widened** from `VARCHAR(255)` to `TEXT` via migration `0007_last_scan_uid_text`. The per-folder JSON state can exceed 255 chars for mailboxes with many folders.

### Migration

- `0007_last_scan_uid_text` — widens `email_accounts.last_scan_uid` from `VARCHAR(255)` to `TEXT`. Backwards-compatible: an existing bare-string value (legacy format from v0.7.4 and earlier) is still recognized on read via `_parse_imap_state` / `_parse_graph_state` and seamlessly upgraded to the new per-folder JSON format on the next scan.

### Operational notes

- **First post-upgrade scan will be long.** The combined effect of removing `seen=False`, enumerating all folders, and (for Outlook) removing the 30-day cap means the first scan after upgrading from v0.7.4 fetches your entire historical mailbox. For the project's own Outlook account, this meant 35,631 emails for pass-1 metadata fetch. This is a one-time cost; subsequent scans remain incremental via the per-folder state.
- **POP3 accounts unchanged.** POP3 has no folder concept and no seen/unseen flag. The POP3 scanner's behavior is identical to v0.7.4.
- **SQLite `database is locked` warnings under high concurrency** (`EMAIL_CONCURRENCY=50`) are pre-existing and not a v0.7.5 regression. Future work may reduce concurrency for IMAP accounts specifically.

### Tests

- 368 tests, 100% coverage.
- New test coverage includes: IMAP multi-folder iteration across flag-driven skip rules (`\Drafts`, `\Trash`, `\Noselect`, `\All`), cross-folder `Message-ID` dedup where the duplicate has both a higher and a lower UID than the folder's running highest, `UIDVALIDITY` change forcing a folder-level full rescan, `folder.set()` failures gracefully skipping a folder without aborting the scan, `fetch()` failures gracefully skipping a folder, `hydrate_email` selecting the correct folder, state-helper backwards-compatibility parsing (legacy bare string, new JSON, invalid JSON fallback, valid JSON with wrong shape), Outlook recursive `childFolders` traversal, Outlook folder skip rules (`drafts`, `deleteditems`, `outbox`, `mailSearchFolder`, empty folders, folders without IDs), Outlook cross-folder `internetMessageId` dedup, Outlook per-folder `receivedDateTime` watermark advancement, Outlook `seen_urls` guard preventing `@odata.nextLink` cycles, scheduler persistence of `_last_scan_state` (both "new state" and "unchanged state" paths), and scheduler fallback to legacy per-email UID when no `_last_scan_state` is exposed (POP3).

## [0.7.4] - 2026-04-18

### Added
- **Dual-model AI connection test** — `POST /settings/ai/test-connection` now tests BOTH the chat model and the embedding model in parallel via `asyncio.gather`. Returns structured per-model status: `{ok, chat: {ok, model, latency_ms, detail, ...}, embed: {ok, model, dim, latency_ms, detail, ...}}`. Previously only the chat model was tested, silently leaving misconfigured embedding models to fail later during semantic search indexing.
- **Granular openai error handling** — the test endpoint distinguishes `auth` (401), `model_not_found` (404), `permission` (403), `rate_limited` (429 — treated as soft pass since endpoint is reachable), `timeout`, `connection`, `bad_request` (400), and `unknown` error types with human-readable messages.
- **Embedding dimension validation** — if the embedding model returns a different vector size than the configured `EMBED_DIM`, the test emits `dim_mismatch: true` with a WARNING message so users know to update the config before sqlite-vec silently rejects embeddings.
- **Two-dot status indicators** — Settings › AI 模型 page now shows separate green/red/gray dots next to both the chat model input and the embedding model input, each reflecting its own test result. Tooltip shows model name, latency, and detail.
- **`classification_tier` in extraction log API** — the tier (1/2/3) was captured in the DB since v0.4.0 but never exposed through `GET /scan/logs/{id}/extractions`. Now returned and rendered as a T1/T2/T3 badge per extraction row.
- **Parse metadata persistence** — new `parse_method` (qr/xml_xpath/ofd_struct/regex/llm), `parse_format` (pdf/xml/ofd), and `download_outcome` columns on `extraction_logs`. Populated by the scheduler at `saved`, `not_vat_invoice`, `low_confidence`, `duplicate` outcomes. Migration: `0006_extraction_parse_metadata`.
- **`GET /scan/logs/{id}/summary` endpoint** — returns aggregate counts by outcome, parse_method, and classification_tier for a scan log. Enables per-scan at-a-glance statistics without pulling every extraction record.
- **Scan summary cards in UI** — clicking a scan log now shows outcome count cards plus parse-method and classification-tier breakdowns above the extraction detail list.
- **Extraction row badges** — each extraction row now surfaces `classification_tier` (indigo T-badge), `parse_method`+`parse_format` (purple badge), and color-coded outcome badges (green for saved, amber for low_confidence/not_vat_invoice, slate for skipped/duplicate, red for error).

### Changed
- **Test endpoint response shape** — backwards-incompatible: clients reading `{ok, model, detail}` now need to read `{ok, chat: {...}, embed: {...}}`. Frontend updated.

### Tests
- 341 tests, 100% coverage. New: dual-model success path, dimension mismatch warning path, no-expected-dim path, per-openai-error-type matrix (auth / model_not_found / permission / rate_limited / timeout / bad_request / unknown), extraction log with parse metadata fields, scan summary aggregation endpoint (success + 404 + empty scan).

## [0.7.3] - 2026-04-18

### Added
- **Anti-scam three-layer defense** — detects invoice-fraud / phishing emails that slipped through previous pipelines (e.g. "代开各行业发票联系微信gn81186", "有发票開丨微信在附件上"):
  - **Tier-1 classifier** now rejects emails whose subject or body contains scam phrases (`代开`, `代开发票`, `有发票出售`, `联系微信`, `加QQ` …), inline WeChat/QQ contact IDs, or obfuscated phone numbers (digits separated by punctuation). No LLM call, no hydration, no attachment fetch.
  - **Tier-3 `analyze_email` LLM prompt** received a dedicated "诈骗 / 虚假发票邮件（必须拒绝）" section that overrides the attachment-is-invoice heuristic when scam signals are present.
  - **Extraction-time LLM prompt** gained a STEP 0 scam rejection layer that flags WeChat-for-invoice solicitations, obfuscated phone patterns, and ad-copy polluted buyer/seller fields as `is_valid_tax_invoice=false`.
- **Scheduler post-extraction sanity check** — even if the LLM extraction claims `is_valid_tax_invoice=true`, the scheduler inspects the resolved buyer/seller/item_summary against the same scam heuristic as tier-1. If the invoice "text" looks like fraud, it is logged as `not_vat_invoice` with reason `scam signal: <...>` and not saved.
- **Shared `is_scam_text()` helper** in `email_classifier.py` so classifier and scheduler apply identical detection rules.

### Changed
- **analyze_email cache key bumped** `analyze_email_v2` → `analyze_email_v3`. Stale classifications cached under the previous prompt (which may have accepted scam emails) are automatically bypassed.

### Tests
- 334 tests, 100% coverage. New tests cover: all three scam detection branches (phrase / contact pattern / obfuscated digits), tier-1 rejecting scam subject even when an invoice-looking PDF is attached, scheduler rejecting a scam invoice post-LLM-merge when the LLM hallucinated a real-looking seller but buyer was ad copy.

## [0.7.2] - 2026-04-18

### Changed
- **Metadata-first email fetch** — IMAP and Outlook scanners no longer download email bodies or attachment payloads during the initial scan pass. Only subject/from/date/size metadata is fetched. Bodies and attachments are retrieved lazily, only after the tier-1 classifier says the message might be an invoice. On large mailboxes (10k+ messages) this avoids downloading hundreds of megabytes of newsletter bodies and promotional attachments that immediately get discarded as non-invoice.
- **IMAP scan** uses `imap-tools` `headers_only=True` + `mark_seen=False` + `bulk=100` — drastically reducing per-message bandwidth and IMAP round-trips.
- **IMAP hydrate** opens a fresh short-lived IMAP connection per classified message and fetches the full MIME via `AND(uid=...)`. Scoped to individual messages, avoiding long-held connections during LLM classification latency.
- **Outlook scan** drops the eager `/attachments?$top=50` call. Pass 1 requests `$select=id,internetMessageId,subject,bodyPreview,from,receivedDateTime,hasAttachments` — bodyPreview (up to 255 chars) is enough for the tier-1 classifier.
- **Outlook hydrate** fetches full body via `$select=body`, then `/attachments` only when `hasAttachments=true`.
- **POP3 stays eager** — the protocol's `TOP` command is unreliable for extracting attachment metadata across all server implementations. Documented as the one protocol exception.
- **Hydration concurrency cap** — new `HYDRATION_CONCURRENCY=5` semaphore per account ensures we don't overwhelm IMAP servers with 50 simultaneous new connections for the second-pass fetch.

### Fixed
- **Hydration failure is non-fatal** — if a second-pass fetch fails (IMAP connection error, Outlook HTTP error), the message is skipped with a warning log; the scan continues processing other messages.

### Tests
- 331 tests, 100% coverage. New tests verify: IMAP `hydrate_email` fetches body+attachments and handles connection failures gracefully, Outlook `hydrate_email` skips `/attachments` endpoint when `hasAttachments=false`, POP3 default hydrate is a no-op, scheduler calls `hydrate_email` only for tier-1 positive/ambiguous emails (spam emails never trigger the second-pass fetch).

## [0.7.1] - 2026-04-18

### Changed
- **Aggressive LLM enrichment** — LLM `extract_invoice_fields` now fires whenever a saved invoice candidate has missing semantic fields (buyer/seller/type/summary), not just when parser confidence is low. User-requested: "let's not save LLM usage, use it to empower the service."
- **Selective merge policy** — LLM fills `buyer`, `seller`, `invoice_type`, `item_summary` when it returns non-未知 values. Parser keeps `invoice_no`, `invoice_date`, `amount` when the result is strong (QR/XML/OFD struct parse, or regex-matched valid 8/20-digit invoice_no). LLM only backfills identifiers when parser failed or produced an invalid format.
- **LLM veto gated on weak parse** — an LLM `is_valid_tax_invoice=false` response no longer discards invoices that the parser extracted via QR, XML, OFD, or a valid 8/20-digit regex match. This fixes false-negatives where the LLM mislabels real 电子普通发票 as invalid.
- **Prompt relaxation** — `extract_invoice.txt` now accepts an invoice as valid when `发票号码` is present AND at least 2 of 4 secondary signals are present, with the requirement that at least one signal must be VAT-specific (type title or tax rate/amount). Ordinary receipts with seller + total but no VAT markers are still rejected.
- **Unlimited first scan** — `FIRST_SCAN_LIMIT` default changed from 500 to unlimited (`None`). IMAP/POP3/Outlook scanners now fetch the full mailbox on initial scan. Subsequent incremental scans are already unlimited. Note: IMAP still uses `seen=False` and Outlook still uses a 30-day `receivedDateTime` filter — true full-history rescans may require clearing those filters in the future.

### Fixed
- **LLM exception fallback** — if the LLM call raises (timeout, rate limit, provider error), the parser result is still saved when strong enough. Previously the whole invoice was discarded as "error".

### Tests
- 321 tests, 100% coverage. New tests cover: enrichment fires on missing fields despite high parser confidence, parser 20-digit invoice_no survives LLM disagreement, strong parse survives LLM `is_valid=false` veto, LLM exception fallback preserves parser invoice, weak parse lets LLM backfill all fields, and LLM-returned 未知 values don't overwrite parser values.

## [0.7.0] - 2026-04-18

### Added
- **VAT invoice whitelist gate** — `VALID_INVOICE_TYPES` constant with all valid Chinese tax invoice types; scheduler rejects documents with unrecognised `invoice_type`, logging new `not_vat_invoice` outcome. Hotel receipts (入住凭证), ride itineraries (行程单), payment receipts, and foreign-currency receipts are no longer saved.
- **LLM rejection field** — `InvoiceExtract.is_valid_tax_invoice: bool`; rewritten `extract_invoice.txt` prompt performs explicit document validation before field extraction
- **VAT-specific confidence scoring** — weighted field scoring (invoice_no 30%, amount 25%, date 15%, buyer/seller 10% each, valid_type 10%) replaces naive field-count-based scoring
- **Text heuristic backup** — `_is_vat_document()` rule-based check used when LLM is unavailable (quota exhausted)
- **AI connection test endpoint** — `POST /settings/ai/test-connection` actually tests chat completion against the selected model instead of listing models
- **AI connection status indicator** — green/red indicator on Settings AI panel after testing

### Fixed
- **Doubled characters** (e.g. `霍霍城城` → `霍城`) — pdfplumber now calls `dedupe_chars(tolerance=1)` before text extraction
- **CID font artifacts** — PDF parser falls back to PyMuPDF when pdfplumber output contains more than 5 `(cid:N)` placeholders
- **XML legacy formats** — `parse_xml` handles GBK-encoded XMLs (航信/百望 tax-control systems) and recognises 14 additional element names covering 航信, 百望, and 数电票 formats
- **QR field-order bug** — corrected to STA spec (`parts[3]=invoice_no`, `parts[4]=amount`, `parts[5]=date`)
- **QR validation** — only QR codes with `parts[0]=="01"` and valid STA type code accepted; 数电票 URL-QR codes skipped cleanly
- **AI Settings test button** — now tests actual chat completion instead of listing models

### Changed
- **Confidence threshold 0.5 → 0.6** — stricter save gate; zero/sentinel amounts (< ¥0.10) treated as parse failures

## [0.6.3] - 2026-04-18

### Changed
- **Email concurrency raised from 5 to 50** — significantly faster scan throughput for large mailboxes

### Fixed
- **Overall progress percentage stuck at 5%** — formula now correctly uses `emails_processed / total_emails` and weights completed accounts properly, providing smooth 0-100% progression across all accounts

## [0.6.2] - 2026-04-18

### Fixed
- **Scan log timestamps inconsistent** — naive datetimes from the database are now normalized to UTC before serialization, ensuring the frontend renders all times consistently in the user's local timezone

## [0.6.1] - 2026-04-18

### Fixed
- **IMAP scanner crash on incremental scans** — `AND()` without parameters raises `ValueError` in imap-tools; replaced with `"ALL"` string criteria
- **Progress bar emails_processed not reset per-account** — counter now resets when switching accounts, preventing misleading "total decreasing" display

### Added
- **Full Rescan button** — "Full Rescan" button on Settings page resets `last_scan_uid` for all accounts, triggering a complete re-scan from scratch instead of incremental
- **`?full=true` query parameter on `POST /scan/trigger`** — API support for full rescan

## [0.6.0] - 2026-04-18

### Added
- **Concurrent email processing** — emails are now processed 5 at a time via `asyncio.Semaphore`-bounded workers instead of sequentially, providing 3-5x scan throughput improvement
- **Per-email DB sessions** — each concurrent email worker gets its own database session, eliminating session contention
- **Thread-safe progress tracking** — `scan_progress.py` now uses `asyncio.Lock` to protect all progress counter updates; added `inc_emails_processed()`, `inc_invoices_found()`, `inc_errors()` atomic increment helpers
- **IntegrityError handling** — concurrent invoice inserts and LLM cache writes now gracefully handle unique constraint races instead of crashing

### Changed
- **Scanner pagination limits raised** — all scanners now fetch up to 500 emails on first scan (was 100-200) and have no limit on subsequent incremental scans
- **Outlook scanner fully paginates** — removed hard 200-email cap; follows `@odata.nextLink` until all messages are fetched or `last_uid` is reached
- **IMAP scanner unlimited on incremental scans** — `limit=None` when `last_uid` is set, fetching all new mail since last scan
- **POP3 scanner processes full mailbox** — on incremental scans, processes all messages from newest to oldest until hitting known IDs
- **CPU-bound parsing off event loop** — `parse_invoice()` now runs via `asyncio.to_thread()` to avoid blocking the async event loop during PDF/QR extraction
- **Progress functions are now async** — `update_progress()`, `finish_progress()`, `inc_*()` are all `async def` with lock protection

### Fixed
- **Progress bar stuck at 50%/99% on completion** — `finish_progress()` now sets `current_account_idx` and `current_email_idx` to their maximum values when phase is DONE, ensuring all progress bars reach 100%

## [0.5.7] - 2026-04-18

### Fixed
- **Scan log invoice count always zero** — `invoices_found` and `finished_at` are now set in a single commit instead of two separate commits that caused the value to be lost due to SQLAlchemy session expiration between commits

### Changed
- **Release workflow includes changelog** — GitHub Releases now include the matching CHANGELOG.md section instead of only an auto-generated diff link
- **CHANGELOG ordering corrected** — entries now strictly follow reverse-chronological order; removed duplicate v0.5.0/v0.4.5 block

## [0.5.6] - 2026-04-17

### Changed
- **LLM-first email analysis pipeline** — scan classification now uses only hard Tier 1 negatives/strong attachment positives plus a single structured LLM analysis call for everything else.
- **Single targeted link download** — Tier 3 scanning no longer blindly downloads every body link. The backend now asks the LLM to classify the email, choose one best invoice URL, return extraction hints, and only downloads that highest-confidence link.
- **PDF-first processing order** — attachments/downloads are prioritized as PDF → OFD → XML, with LLM format hints able to confirm PDF-first ordering.

## [0.5.5] - 2026-04-17

### Fixed
- **Scan progress panel never appeared** — root cause: the SSE composable used `onmessage` which only fires for unnamed events, but the backend emits named `event: "progress"` events. Fixed by adding `addEventListener('progress', handler)` alongside `onmessage` as fallback
- **Progress state not visible on connect** — composable now polls `GET /scan/progress` immediately on `connect()` so the current backend state renders instantly (before the first SSE push arrives)
- **App automatically switches to Scan tab when a scan starts** — so users always see the progress bar whether the scan was triggered manually or by the scheduler

## [0.5.4] - 2026-04-17

### Added
- **Richer scan progress detail** — Scan Operations progress panel now shows:
  - Classification tier badge (T1 free-local / T2 scored / **T3·LLM** in amber to indicate an LLM call)
  - Current download link URL + live outcome (downloading / saved / failed)
  - Extraction method badge (QR / XML / OFD / LLM / Regex)
  - File format badge (PDF / XML / OFD)
  - Completion summary shows email count alongside invoice count
- **Known issues documented** in ROADMAP.md:
  - Email tracking pixels (e.g. linktrace.triggerdelivery.com) fetched as fake PDFs — parse fails gracefully, fix planned
  - Nuonuo/JSScloud invoice links redirect to CDN HTML pages not direct PDFs — anti-crawl protection blocks server-side download

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

## [0.4.2] - 2026-04-17

### Changed
- Bumped to v0.4.2 — deploy tooling and Docker support

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
