# Roadmap

## v0.1.0 — Released

First release. Full email-to-invoice pipeline with PDF/XML/OFD parsing, LLM-powered classification and extraction, FTS5 + semantic search, Vue 3 dashboard, and CI/release automation.

---

## v0.2.0 — Released

**Theme:** Reliability, Data Quality, Branding, and Operational Hardening

| # | Feature | Priority | Status |
|---|---------|----------|--------|
| 1 | **Typed exception handlers** — replace bare `except Exception` in email scanner with specific exceptions | P0 | Done |
| 2 | **Per-email extraction audit log** — track why each email was saved, skipped, or failed | P0 | Done |
| 3 | **APScheduler startup guard** — detect multi-worker misconfiguration and warn | P0 | Done |
| 4 | **Semantic search pagination** — fix hardcoded page=1 in semantic search endpoint | P1 | Done |
| 5 | **Project icon in Web GUI** — favicon, login page, nav bar branding | P0 | Done |
| 6 | **AI model settings via Web UI** — manage LLM base URL, API key, model selection from Settings page; retrieve model list from provider | P0 | Done |
| 7 | **Manual invoice correction** — edit extracted fields inline with audit trail | P0 | Done |
| 8 | **Confidence display** — show extraction confidence and method as colored badges | P1 | Done |
| 9 | **CSV export** — export filtered invoice list as CSV | P1 | Done |
| 10 | **Rich health endpoint** — report DB, scheduler, sqlite-vec status and invoice stats | P1 | Done |
| 11 | **Rate limiting** — brute-force protection on login endpoint | P0 | Done |
| 12 | **Outbound webhooks** — invoice.created with HMAC-SHA256 signature | P1 | Done |
| 13 | **Saved views / smart filters** — persist named search states | P1 | Done |
| 14 | **Spend analytics** — monthly spend, top sellers, counts by type/method | P1 | Done |
| 15 | **Similar invoice discovery** — KNN or FTS5 fallback | P2 | Done |
| 16 | **Documentation updates** — README + CHANGELOG for all shipped changes | P0 | Done |

### Full v0.2.0 (additional items)

| Feature | Priority |
|---------|----------|
| Outbound webhooks (`invoice.created` with HMAC signature) | P1 |
| Saved views / smart filters | P1 |
| Spend analytics dashboard (monthly spend, top sellers) | P1 |
| Composite duplicate detection | P1 |
| Standardized log levels across parsers | P1 |
| Config type safety cleanup | P1 |
| Structured JSON logging | P1 |
| Docker Compose setup | P1 |
| "More like this" semantic discovery | P2 |
| Excel export (via openpyxl) | P2 |

### Execution Plan

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.3.0 — Released

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.4.0 — Released

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.1 — Released

**Theme:** Aggressive LLM enrichment + selective merge + prompt relaxation

Regex-extracted invoices were saving with buyer/seller/type as `未知` because the LLM extraction stage only fired when parser confidence was below 0.6. v0.7.1 makes LLM enrichment mandatory whenever semantic fields are missing (even if parser confidence is high), with selective merge: LLM wins for buyer/seller/invoice_type/item_summary, parser wins for invoice_no/invoice_date/amount when the parser result is strong (QR, XML, OFD, or regex-matched 8/20-digit invoice_no). Prompt relaxed to accept invoices with `发票号码` + any 2 of 4 secondary signals. `FIRST_SCAN_LIMIT` default changed to unlimited.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.2 — Released

**Theme:** Metadata-first lazy email fetch

IMAP and Outlook scanners previously downloaded full MIME + every attachment payload for every message before classification. A 10k-email mailbox pulled gigabytes that were ~90% discarded as non-invoice. v0.7.2 adds two-phase scanning: pass 1 fetches subject/from/body-preview only; pass 2 (`hydrate_email`) fetches full body and attachments only after tier-1 classifier says invoice-related. Opens a fresh short-lived IMAP connection per classified message to avoid holding connections through LLM latency.

**Production impact:** QQ mailbox scan 51min → 16min (3.1× faster).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.3 — Released

**Theme:** Anti-scam three-layer defense

Invoice-fraud / phishing emails ("代开各行业发票联系微信gn81186", "有发票開丨微信在附件上") were slipping through the pipeline because tier-1 saw `发票` in the subject, tier-3 LLM couldn't distinguish scam from genuine, and the extraction-time LLM was happy to hallucinate buyer/seller from ad copy. v0.7.3 adds (a) tier-1 classifier phrase/contact/obfuscated-digit detection via a new `is_scam_text()` helper, (b) a dedicated "诈骗 / 虚假发票邮件" rejection section in the `analyze_email` prompt, and (c) a STEP 0 scam rejection layer in `extract_invoice.txt`. Scheduler adds a post-extraction sanity check that re-inspects resolved buyer/seller against the same heuristic.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.4 — Released

**Theme:** Dual-model AI test + scan operations transparency

The "Test Connection" endpoint only tested the chat model, leaving embedding-model misconfiguration to fail silently during semantic search. Scan operations page rendered extraction details with no classification tier, parse method, or aggregate summary. v0.7.4 adds: parallel chat + embed test via `asyncio.gather`, granular openai error types (auth/404/403/429/timeout/connection/400), embedding dimension mismatch warnings, new `GET /scan/logs/{id}/summary` endpoint, persisted `classification_tier` / `parse_method` / `parse_format` / `download_outcome` columns on `ExtractionLog` (migration `0006`), and frontend T1/T2/T3 badges plus summary cards above the extraction list.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.5 — Released

**Theme:** Multi-folder email scan (IMAP + Outlook)

Three silent scan-coverage gaps closed at once:

1. IMAP first scans used `AND(seen=False)` — already-read invoices at first-scan time were invisible forever.
2. IMAP and Outlook were hardcoded to INBOX only — Archive / Junk / custom folders never scanned.
3. Outlook first scan had a 30-day `receivedDateTime` cap — invoices older than 30 days never fetched.

v0.7.5 enumerates all mail folders for both IMAP (via `MailBox.folder.list()` with flag-based skip of `\Drafts` / `\Trash` / `\Noselect` / `\All`) and Outlook Graph (recursive `/me/mailFolders` + `childFolders` with skip of `drafts` / `deleteditems` / `outbox` / `mailSearchFolder`). Per-folder UID + UIDVALIDITY state for IMAP, per-folder `receivedDateTime` watermark for Outlook. Cross-folder dedup by `Message-ID` / `internetMessageId`. `EmailAccount.last_scan_uid` widened `VARCHAR(255)` → `TEXT` (migration `0007`).

**Production impact:** Outlook account: 470 emails → 35,631 emails scanned (76× coverage), 7 → 208 invoices saved.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.6 — Released

**Theme:** Per-invocation manual-scan options (unread_only + since)

Previously a manual scan had one implicit behavior — "fetch everything new since last time" — and `full=true` was the only override available, blowing away all incremental state. v0.7.6 adds two controls applied consistently across IMAP / POP3 / Outlook:

- **`unread_only`** — server-side `AND(seen=False)` for IMAP, `$filter=isRead eq false` for Graph, no-op for POP3 (with UI warning banner)
- **`since`** — server-side `AND(date_gte=date)` + client-side datetime refinement for IMAP (SINCE is RFC 3501 DATE-granularity), `$filter=receivedDateTime ge X` for Graph, client-side `Date` header filter for POP3

UI adds a two-control panel in Settings → Scan Operations: "Only unread messages" checkbox and a time-range dropdown (All time / 7d / 30d / 6m / 1y / Custom). `POST /scan/trigger` now accepts a JSON body `{full, unread_only, since}`; legacy `?full=true` query param is preserved for backward compat.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.7 — Released

**Theme:** Scan fault isolation + orphan log cleanup

Live production monitoring on v0.7.6 caught two reliability bugs: a transient `MailboxFetchError` from QQ Mail ("System busy!") raised mid-iteration killed the entire account scan because the `try/except` only wrapped the `fetch()` setup call, not the iteration loop. Separately, when the scheduler's outer exception handler failed to stamp `scan_log.finished_at` after a failed scan, the orphan row stayed `NULL` forever and could only be cleaned by service restart. v0.7.7 widens the IMAP try/except to include the for-loop, adds `MailboxFetchError` to `IMAP_CONNECTION_ERRORS`, and guarantees orphan scan_log cleanup at the start of every `scan_all_accounts` invocation (not just at service startup).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.7.8 — Released

**Theme:** IMAP scan performance — STATUS pre-flight skip, folder-level progress, TCP keepalive, partial-state preservation

After library research (imap-tools, OfflineIMAP, RFC 3501/7162) and live production monitoring confirmed that large-mailbox scans were bottlenecked on 4 independent problems, v0.7.8 ships the high-ROI subset as drop-in improvements:

1. **STATUS pre-flight** (largest win on incremental scans): before selecting a folder, check its UIDNEXT and MESSAGES via `STATUS`. If unchanged since last scan, skip the folder entirely — often reducing a 20-folder scan to ~20 cheap STATUS round-trips instead of 20 full folder sweeps. Expected: 30–60 sec incremental scans where v0.7.7 took 30–60 min.
2. **Bulk size 100 → 500** for pass-1 `UID FETCH`, a 3–8× speedup per imap-tools research.
3. **Folder-level progress telemetry** exposed via `ScanProgress.total_folders` / `current_folder_idx` / `current_folder_name` / `folder_fetch_msg` and running `total_emails` updates every 200 messages, so operators see real progress instead of a frozen bar.
4. **TCP keepalive** on IMAP sockets to detect silent NAT/firewall drops in ~100 s instead of hanging FETCHes for minutes.
5. **Partial-state preservation** when the outer `with MailBox()` drops mid-scan (e.g. `ssl.SSLEOFError` during LOGOUT) — accumulated emails + completed-folder state now survive the drop.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v1.0.0 — Released

**Stable multi-user release.** Caps the v0.9.x arc with a single user-visible UX improvement — per-email scan summary aggregation — and docs polish.

**What changed**:
- Scan-log expanded panel now aggregates `extraction_logs` by `email_uid`, showing one card per email instead of one row per attachment. A 2-email / 6-row Sam's Club invoice scan (two emails × three attachments each) now summarizes as "2 封邮件 · 保存 1 张新发票 · 去重 1 封" instead of the confusing "6 duplicate rows".
- Per-email card shows the highest-priority outcome as the primary badge (saved > duplicate > skipped_seen > low_confidence > parse_failed > error > not_invoice) with attachment-level chips below for low-level audit.
- `duplicate` badge has an explicit tooltip: "Same invoice_no already exists — correctly deduped, no action needed" — directly addressing the user confusion from the 2026-04-20 investigation.
- Parse-method and classification-tier chips from v0.9.1 retained as secondary forensic view.

**What did NOT change**:
- No backend changes
- No schema changes
- No API contract changes
- No migration
- No classifier behavior change
- No scanner behavior change

Zero-touch upgrade from v0.9.1. 619 tests, 100% coverage (unchanged).

**Deferred to v1.1.0**: Microsoft Graph Delta Query scanner rewrite — per Oracle review, needs state versioning, legacy bridge, operator kill-switch. Too high blast radius for first 1.0 cut.

See [CHANGELOG.md](CHANGELOG.md) for the full cumulative journey from v0.8 → v1.0.

---

## v0.9.1 — Released

**Email scanner + classifier hardening.** Investigation into a 2026-04-20 "missed invoice" report concluded the system worked correctly (Sam's Club sent the same e-invoice number twice 247 seconds apart; correctly deduped) but surfaced 8 real defects. This release bundles the low/medium-risk fixes per Oracle review; Microsoft Graph Delta Query is deferred to v1.1.0.

**Shipped**:
- Migration 0014 seeds 7 default trusted senders (qcloudmail.com / fapiao.jd.com / shuidi.com / noreply@invoice.alipay.com / inv.nuonuo.com / piaoyi.baiwang.com / eeds.chinatax.gov.cn) — additive-only, defensive against fresh-install schema-lifecycle, no-op downgrade
- 7 new Chinese keywords: 购物凭证 / 消费凭证 / 购物发票 / 订单完成 / 交易成功 / 支付凭证 / 發票
- Domain-aware trusted-sender match (exact-email + @domain + legacy substring) — closes `notbilling@company.com` spoofing vulnerability against a configured `billing@company.com`
- Outlook `includeHiddenFolders=true` — was missing Clutter / some Archive configurations
- Outlook watermark `gt` → `ge` — was silently excluding boundary emails
- Tier-1 body scan window 200 → 2000 chars
- LLM classify cache TTL 30d → 7d — limits false-negative propagation
- Frontend `formatScanState()` account-type-aware — Outlook now shows "8 folders · last synced [timestamp]" instead of "0 messages · UID unknown"

Zero schema/API/filesystem changes. 619 tests, 100% coverage (+14).

**Deferred to v1.0.0**: Fix 8 (scan-summary UX) + docs polish.  
**Deferred to v1.1.0**: Fix 9 (Delta Query rewrite) — per Oracle, needs state-versioning, bridge migration, operator kill-switch.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0 — Released

**Multi-user is done.** Nine alphas (alpha.1 → alpha.9) incrementally transformed Invoice Maid from single-operator-only to a safely multi-tenant self-hosted product. Every release shipped to production sequentially. v0.9.0 final is a docs-and-version-bump cap — all behavior already landed in earlier alphas.

**Summary of the journey:**

- Phase 1 (alpha.1-3): DB-backed users + session revocation + login-form polish
- Phase 2 (alpha.4): nullable `user_id` columns on 7 tenant tables, backfilled to admin
- Phase 3 (alpha.5): `NOT NULL` + `CASCADE` FK + composite `UNIQUE(user_id, invoice_no)`
- Phase 4a (alpha.6): tenant isolation on every read path (FTS5 + ORM, 22 dedicated isolation tests)
- Phase 4b.1 (alpha.7 + post1): per-user file storage layout; alembic `env.py` auto-loads `.env` (post1 production recovery)
- Phase 5a (alpha.8): self-service registration (`ALLOW_REGISTRATION` gate) + change-password with cross-device revocation
- Phase 5b (alpha.9): admin panel (backend endpoints + frontend AdminView) + startup orphan-directory scan

**What works now:** a second user can safely exist on the instance. They have isolated invoices, email accounts, scan history, storage, and settings. The admin can see them, toggle their status, and delete them. Stolen tokens after password change stop working.

**Deferred to v1.0+:** per-user AI/classifier settings, per-user webhooks, repository-pattern refactor. All currently remain instance-wide by explicit design.

605 tests, 100% coverage.

See [CHANGELOG.md](CHANGELOG.md) for the full cumulative changelog.

---

## v0.9.0-alpha.9 — Released

**Phase 5b of multi-user transition:** admin panel.

Backend: `AdminUser` dep raises 403 (not 404) for non-admin callers — deliberately the opposite of `assert_owned`'s 404 because the admin endpoint's existence is public in the OpenAPI schema; we refuse the action, not hide the URL. Three new endpoints: `GET /api/v1/admin/users` (list all, with invoice_count), `PUT /api/v1/admin/users/{id}` (toggle active/admin, rename email, with anti-lockout guardrails), `DELETE /api/v1/admin/users/{id}` (cascade-wipes invoices/files/scan logs in FK-safe order, invokes `FileManager.delete_user_files` for disk cleanup). Startup orphan-directory scan in lifespan logs WARN for any `users/{id}/` left on disk by interrupted deletions.

Frontend: `AdminView.vue` single-page user-management table with toggle/promote/delete row actions, confirm modal for destructive operations, self-row delete button disabled, `(you)` tag on current admin. Admin nav link (amber-styled) only visible to admins via `authStore.isAdmin`. `/admin` route guarded by `requiresAdmin` meta so non-admins get redirected to `/invoices`.

Anti-lockout guardrails: admin cannot deactivate self, cannot demote last admin, cannot delete self. Delete handler is resilient to DB-level FK config drift — explicit per-table cleanup in FK-safe order.

17 new backend tests in `test_admin.py` + 3 orphan-scan tests in `test_main.py`. Every endpoint × (admin, non-admin, unauthenticated) combination covered, every guardrail tested, cascade + file-delete contracts verified.

Zero-touch upgrade — no schema changes, no migration. Production stays single-user; admin panel becomes available when ALLOW_REGISTRATION lets a second user in.

605 tests, 100% coverage (+21).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.8 — Released

**Phase 5a of multi-user transition:** authentication UX (user identity in UI + self-service registration + change-password flow).

Backend: `POST /api/v1/auth/register` (gated by `ALLOW_REGISTRATION` env var, default false; rate-limited 5/min; validates email format, password length, and confirmation match; returns 403 when disabled, 409 on duplicate). `PUT /api/v1/auth/me/password` (verifies current password, hashes new, revokes every other session for the same user so stolen tokens on other devices stop working; the caller's current session stays valid). `hash_password()` bcrypt helper in `auth_service.py`. `ALLOW_REGISTRATION` setting in `config.py`.

Frontend: `UserInfo` type + auth store with `user` state, `isAdmin` getter, `register/fetchMe/changePassword` actions. App-init `fetchMe()` call so AppLayout shows the logged-in user's email immediately. `RegisterView.vue` with client-side validation and a `Registration is disabled` message for 403 responses. LoginView has a "Sign up" link. AppLayout user menu: profile dropdown with avatar/email/admin-badge + Change-password modal (with success banner explaining other-device sign-out). Router guard redirects already-authenticated users away from `/login` and `/register`.

13 new backend tests covering every branch of register + change-password, including the tenant-isolation invariant that changing one user's password does NOT revoke another user's sessions.

Zero-touch upgrade — no schema changes, no migration, no config changes. `ALLOW_REGISTRATION=false` default keeps production single-user and closed.

584 tests, 100% coverage (+13).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.7.post1 — Released

**Hotfix**: alembic's ``env.py`` did not auto-load ``backend/.env``, so migration 0013's ``_derive_storage_path`` hit its URL-derived fallback on production deployments where ``STORAGE_PATH`` lives in ``.env`` rather than as a systemd-level env var. The DB ``file_path`` column was rewritten to ``users/{user_id}/...`` correctly, but the files stayed at the flat layout — downloads would have 404'd for every invoice.

Recovery: all 239 flat files were moved to their DB-declared user subdirectory via a one-off script before end-of-deploy. Production never saw user-visible impact.

Fix: ``env.py`` now auto-loads ``backend/.env`` on every invocation (via python-dotenv, already a transitive dep). ``load_dotenv(override=False)`` respects systemd-level vars. ``ALEMBIC_SKIP_DOTENV=1`` as a test-only escape hatch. Migration 0013 also logs the resolved ``STORAGE_PATH`` at the start of upgrade/downgrade so operators can spot misresolution from a single log line.

See [CHANGELOG.md](CHANGELOG.md) for full incident report.

---

## v0.9.0-alpha.7 — Released

**Phase 4b.1 of multi-user transition:** per-user file storage.

Invoice files now live under `STORAGE_PATH/users/{user_id}/invoices/` instead of a flat directory. The canonical filename is deterministic from invoice metadata (`buyer_seller_invoiceno_date_amount.pdf`); under the flat layout, two users who legitimately own invoices with identical metadata would generate the same filename and silently overwrite each other's files. Per-user subdirectories make the collision structurally impossible.

`FileManager.save_invoice()` gains a keyword-only `user_id: int` parameter. Every call site (scheduler, manual upload, smoke-data seeder) passes it through. `get_full_path`, `stream_zip`, and `delete_invoice_file` take the already-prefixed relative path — no `user_id` parameter because the security boundary is the tenant-scoped `db.get(Invoice, id)` upstream, not the filesystem layer.

Alembic migration 0013 is a data migration that moves every invoice file from flat `STORAGE_PATH/{filename}` to `STORAGE_PATH/users/{user_id}/invoices/{filename}`, updates `invoices.file_path` accordingly, and logs every action (INFO for moves, WARNING for missing-on-disk rows). Fully idempotent: already-migrated rows are skipped; missing-on-disk rows update DB only; partial-run recoveries leave the new-path file intact. Honors `DRY_RUN=1` for pre-flight validation against production DB copies.

New `FileManager.delete_user_files(user_id)` removes the per-user directory recursively — used by the upcoming admin user-delete endpoint and the planned startup orphan-directory scan.

Dry-run against a production database copy: 239 files moved in under 0.5 seconds, 0 collisions, 0 missing-on-disk rows, 18 orphan root files correctly left alone. Downgrade roundtrip clean.

571 tests, 100% coverage (+15).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.6 — Released

**Phase 4a of multi-user transition:** tenant isolation on every read path.

Every API endpoint now filters by `user_id`. Every `db.get(...)` for a tenant-scoped row is wrapped in a new `assert_owned(resource, user)` helper that returns 404 — not 403 — for rows belonging to other users. The 404 response body is now the generic `{"detail": "Not found"}` instead of resource-specific strings like `"Invoice not found"`, so a second user can't distinguish between "no such ID" and "exists but not yours."

`SearchService` takes `user_id` on every entry point. The FTS5 MATCH branch still queries the shared FTS index for candidate rowids — FTS5 has no user concept — then hydrates through a tenant-scoped `WHERE invoices.user_id = ? AND invoices.id IN (...)` SELECT. The ORM filter, not the FTS index, is the security boundary. Documented inline so a future optimization can't accidentally bypass it.

Every endpoint family updated: invoices (list/get/update/delete/download/similar/export/semantic/batch-delete/batch-download), accounts (list/update/delete/test-connection/oauth), scan (logs/extractions/summary/trigger), stats, saved views. Service-layer duplicate checks (`manual_upload.py`, `tasks/scheduler.py`) also scope by `user_id` — two users may legitimately receive invoices with the same `invoice_no`.

22-test tenant-isolation suite covering every endpoint family, asserting 404/empty/silent-noop responses to cross-tenant access attempts and that admin's resources are untouched after second-user attempts.

Dry-run against a production database copy: inserted a synthetic user 2, ran tenant-scoped SELECTs across all seven tenant tables, confirmed user 2 sees zero rows while user 1 retains all 239 invoices / 3 accounts / 85 scan logs / 273,580 extraction logs / 1 correction log.

Zero-touch upgrade — no schema changes, no migration, no config changes. Existing single-user deployment unaffected.

556 tests, 100% coverage (+22).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.5 — Released

**Phase 3 of multi-user transition:** `NOT NULL` + FK constraints on every tenant `user_id` column, plus composite `UNIQUE(user_id, invoice_no)` on `invoices`.

Alembic migration 0012 tightens seven tenant tables (`invoices`, `email_accounts`, `scan_logs`, `extraction_logs`, `correction_logs`, `saved_views`, `webhook_logs`). Every `user_id` column goes `NOT NULL` with `ondelete=CASCADE` to `users.id`. The global `UNIQUE(invoice_no)` on `invoices` is replaced by composite `UNIQUE(user_id, invoice_no)` so two users can legally own an invoice with the same number. A non-unique `ix_invoices_invoice_no` is kept so partial-key queries still hit an index.

Invoices are processed last in the migration because their FTS5 sync triggers (`invoices_ai` / `invoices_ad` / `invoices_au`) don't survive a `batch_alter_table` rewrite. The migration drops them up front, performs the rewrite, recreates them against the rebuilt table, and repopulates the FTS5 content with `INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')`.

On a fresh install where `alembic upgrade head` runs before the app's first boot, migration 0012 seeds `users[1]` itself from `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` so the `NOT NULL` tightening succeeds. The application's first-boot bootstrap hook becomes a no-op. Missing env vars cause the migration to refuse rather than insert a placeholder row.

Downgrade carries a preflight check: if any two invoices share an `invoice_no` (possible only once a second user has been added), the downgrade raises rather than violating the pre-0012 global unique.

Every application insert site (`Invoice`, `ScanLog`, `ExtractionLog`, `CorrectionLog`, `SavedView`, `WebhookLog`, `EmailAccount`) now populates `user_id` from `_current_user.id` in request contexts or from `account.user_id` in the scheduler. Test fixtures in `conftest.py` updated to match.

Dry-run against a production database copy completed in under one second (239 invoices, 273,580 extraction logs). Every row count preserved, zero NULL `user_id`s, composite unique index present, all three FTS5 triggers recreated, FTS index repopulated, live-insert smoke test confirmed trigger firing, subsequent downgrade restored the prior schema cleanly.

534 tests, 100% coverage (+8).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.4 — Released

**Phase 2 of multi-user transition:** `user_id` columns on every tenant table.

Alembic migration 0011 adds a nullable `user_id INTEGER` column to seven tenant tables — `invoices`, `email_accounts`, `scan_logs`, `extraction_logs`, `correction_logs`, `saved_views`, `webhook_logs` — backfills existing rows to the bootstrap admin (`users[1]` from Phase 1), and creates per-table indexes sized to the query shapes the app actually issues. Additive only — no `NOT NULL`, no foreign-key constraint, no composite-unique reshape. Those tightenings are deferred to Phase 3 migration 0012 once the application code has been refactored to populate `user_id` on every write (Phase 4).

Migration is defensive about the project's schema-lifecycle wart: `saved_views` and `webhook_logs` are not created by any alembic migration — they come from `Base.metadata.create_all` at first app start. Migration 0011 inspects the bind and skips any tenant table that isn't yet present, so a fresh install where alembic runs before the first app boot still succeeds; `create_all` later materialises the missing tables with the matching ORM column and index.

Dry-run against a production database copy verified: pre-upgrade and post-upgrade row counts match across all seven tenant tables, every row carries `user_id = 1`, all seven new indexes present, downgrade cleanly removes the column without touching other data.

526 tests, 100% coverage (+6).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.3 — Released

**Hotfix:** Login rejected self-hosted-style email addresses.

The v0.9.0-alpha.1 `LoginRequest` used pydantic's `EmailStr` which requires a dotted domain. The bootstrap default `ADMIN_EMAIL=admin@local` failed that validation, so the admin user created by v0.9.0-alpha.1 couldn't log in through the alpha.2 form. Schema relaxed to plain `str` with length bounds; DB lookup still exact-match. Format validation moves to the future `/auth/register` endpoint where it belongs.

520 tests, 100% coverage (regression test added).

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.2 — Released

**Hotfix:** Login page email field.

v0.9.0-alpha.1 shipped the backend email-based login flow but left the frontend form password-only, so every login attempt hit `422 email is required`. This alpha ships the matching frontend change: email input + adjusted Pinia action + axios client payload. Operators who logged out after upgrading to v0.9.0-alpha.1 get back in with `admin@local` (the default `ADMIN_EMAIL`) + their existing password.

No backend migrations. Frontend `dist/` rebuilt. Zero-touch deploy.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.9.0-alpha.1 — Released

**Theme:** Phase 1 of the multi-user transition — DB-backed users, session revocation, email-based login.

Ships the foundational auth layer without yet tenant-scoping existing data. A single-operator v0.8.10 deployment upgrades cleanly: the bootstrap admin auto-creates from `ADMIN_EMAIL` (default `admin@local`) + existing `ADMIN_PASSWORD_HASH` on first boot, every API endpoint continues to work with the same scope and semantics, and only the login flow changes (email + password instead of password-only).

New: `users` and `user_sessions` tables via Alembic `0010`. New endpoints `/auth/logout`, `/auth/logout-all`, `/auth/me`, `/auth/sessions`. `CurrentUser` dep now returns a `User` ORM object instead of the string `"admin"`. JWTs require a matching unrevoked `user_sessions` row to grant access — this is the contract that makes logout meaningful.

Alpha designation signals: Phases 2-5 (user_id columns, repository pattern, admin UI, per-user settings) are still pending. The release is production-safe for single-operator deployments — it's just Phase 1 of a longer journey.

519 tests, 100% coverage.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.8.10 — Released

**Theme:** Operational hardening ahead of the v0.9.0 multi-user transition.

Three bounded-growth fixes for long-running self-hosted deployments: a 64 MiB cap on the on-disk WAL file via `PRAGMA journal_size_limit`, TTL-based eviction for `llm_cache` (30-day expiry for classification / email-analysis cache entries; 365-day for invoice-extraction entries), and a 90-day retention window for `extraction_logs` (which grow at roughly 1000:1 relative to saved invoices). Two new APScheduler jobs run the cleanup work hourly (LLM cache) and daily (extraction logs).

Adds Alembic migration `0009_llm_cache_expires_at` which backfills `expires_at` on existing cache rows so v0.8.9 deployments upgrade without any stale-cache gotchas. The `AIService` read/write paths both use `expires_at` now, with a cross-file-contract test guarding the TTL values against drift.

Shipped independently so v0.9.0 can build on a stable operational baseline.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.8.9 — Released

**Theme:** Prompt-level fix for LLM amount-miss on transport-ticket bare-currency fares.

Follow-up to v0.8.8. Image-based railway e-ticket PDFs whose fare renders as a bare `￥N.NN` fragment with no adjacent `票价` / `价税合计` label could still save with `amount=0.00` because the v0.8.8 prompt anchored amount extraction to those labels and the LLM would fall back to the `0.01` sentinel on this layout.

Diagnosis: prompt clauses interacted badly. Path B's "valid even without 价税合计" plus the "amount → 0.01 when absent" fallback gave the LLM a conservative escape hatch. Fix: three targeted additions to `extract_invoice.txt` — (1) Path B's relaxation is explicitly scoped to validity only, not amount; (2) a bare `￥N.NN` or `¥N.NN` on transport e-tickets IS the amount even without a label; (3) `退票费 / 改签费 / 手续费 / 服务费 / 退款 / 已退 / 优惠`-tagged amounts are excluded, and a blank `退票费:` marker is ignored (doesn't convert the ticket into a refund-ticket).

No schema change. `InvoiceExtract.amount` already accepts the right values; the LLM just needed explicit permission to choose them. 3 new prompt-contract tests lock the new clauses in so a future cleanup PR can't silently re-break them. 489 tests, 100% coverage.

See [CHANGELOG.md](CHANGELOG.md#089---2026-04-20).

---

## v0.8.8 — Released

**Theme:** Railway + airline e-ticket support (per 2024年第8号/9号公告) and SQLite concurrent-writer fix.

Two independent classes of failure on the v0.8.7 manual-upload path for image-based railway e-tickets (铁路电子客票):

1. **LLM prompt was pre-2024.** It treated 行程单 / 出行记录 as ride-itinerary receipts. Correct pre-2024, wrong since 国家税务总局 2024年第8号公告 (effective 2024-11-01) reclassified 铁路电子客票 as legal 全面数字化的电子发票, and 2024年第9号公告 (effective 2024-12-01) did the same for 航空电子行程单.
2. **Image-based PDFs yield sparse text.** 12306-issued railway tickets are often rasterized, so pdfplumber/PyMuPDF extract only the 20-digit invoice_no — not the 票价 or buyer/seller. Existing confidence gate (`amount_is_sentinel`) would still reject even with a fixed prompt.
3. **SQLite writer-lock contention.** v0.8.7's 3-worker pool had each worker hold the lock through a 10–30s LLM round-trip, exceeding the default 5s busy_timeout.

v0.8.8 ships coordinated fixes: LLM prompt adds **PATH B** validity rule for transport e-tickets (matches on 20-digit invoice_no + transport markers, bypasses the 价税合计 requirement for image PDFs); `VALID_INVOICE_TYPES` adds `电子发票（铁路电子客票）` + `电子发票（航空运输电子客票行程单）`; manual-upload confidence gate relaxes for detected transport e-tickets (saves with `amount=0` for operator correction). SQLite engine gets `timeout=30.0` in connect_args, and `_create_upload_scan_log` commits immediately so the writer lock is released before the LLM call.

486 tests, 100% coverage (+10 from v0.8.7). Image-based 铁路电子客票 PDFs that previously failed with `not_vat_invoice` now save cleanly with proper `invoice_type`; concurrent uploads no longer fail due to lock contention.

See [CHANGELOG.md](CHANGELOG.md#088---2026-04-20).

---

## v0.8.7 — Released

**Theme:** Multi-file invoice upload — drag in up to 25 PDF / XML / OFD files at once, processed 3 in parallel with per-file progress and retry.

A natural follow-on to v0.8.6's manual-upload feature once real users test it with a historical backlog: picking each file through the browser dialog is untenable past ~3 invoices. v0.8.7 replaces the single-file view with a queue-based UI: drop a folder of invoices, each becomes its own row showing its filename + size + status icon + progress bar, the frontend spawns 3 concurrent workers that greedy-poll the queue, and per-row error panels map the backend's structured outcomes (`duplicate`, `low_confidence`, `not_vat_invoice`, etc.) to specific messaging — including a "view existing invoice" link for 409 duplicates and a retry button for transient failures.

Zero backend change. The v0.8.6 single-file endpoint (`POST /api/v1/invoices/upload`) and its 476-test suite stay exactly as shipped — multi-file is a pure frontend orchestration that loops `api.uploadInvoice()` N times with a concurrency cap. The slowapi `30/min/IP` rate limit on the endpoint naturally paces large batches; when it's tripped the per-file error panel surfaces a `429 Retry` button instead of aborting the whole batch.

See [CHANGELOG.md](CHANGELOG.md#087---2026-04-20).

---

## v0.8.6 — Released

**Theme:** Manual invoice upload — alongside the scheduler, users can now drop PDF / XML / OFD invoice files directly into the same extraction pipeline.

A natural companion to the bulk-export feature shipped in v0.8.5: once you've made the output end flexible, the next question is "what about invoices that never arrive by email?" — scanned paper receipts, historical backlog from before the mailbox was configured, invoices received over WeChat / DingTalk / WhatsApp. v0.8.6 answers that.

New endpoint `POST /api/v1/invoices/upload` accepts a single multipart upload (PDF / XML / OFD, up to 25 MB) and feeds it to the exact same `invoice_parser.parse()` → `AIService.extract_invoice_fields()` → `FileManager.save_invoice()` → `Invoice` row → `ExtractionLog` chain the scheduler uses. Extraction quality is identical because it IS the same code. A new Vue view at `/upload` provides drag-and-drop, client-side validation, progress bar, and structured error handling (409 duplicate with link to existing invoice, 422 with specific outcome reason, 413/415 rejections).

The feature also closes several security surfaces the email-only scanner didn't need: magic-byte MIME sniffing, XXE-hardened XML parser (`lxml.etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)`), OFD zip-bomb detection (`OFD_MAX_UNCOMPRESSED_BYTES = 100 MB`), UUID-only temp filenames, three-layer body-size enforcement (nginx 25 MB → `ContentSizeLimitMiddleware` 25 MB → route-level streaming counter 25 MB), and an updated `python-multipart >= 0.0.22` pin covering three high-severity CVEs from 2024–2026.

Tests: 476 passing, 100% coverage (+42 vs v0.8.5). README grew a new "How it works" section with a hand-authored SVG workflow diagram showing how the two sources (email scanner + manual upload) converge on the shared five-stage pipeline.

See [CHANGELOG.md](CHANGELOG.md#086---2026-04-20).

---

## v0.8.5 — Released

**Theme:** Bulk-export ZIPs now bundle an `invoices_summary.csv` metadata table; README fully refreshed for the v0.7-v0.8 feature accumulation; CHANGELOG PII sanitization and `.gitignore` hygiene.

Previously the "Download selected" batch-export produced a ZIP of PDFs but nothing that mapped each PDF's filename back to the structured fields — reconciling a 50-invoice batch against accounting records meant opening each PDF one by one. v0.8.5 embeds `invoices_summary.csv` at the root of the ZIP with a row per downloaded invoice covering the exact same column layout as the `/invoices/export` endpoint. Chinese seller names render correctly in Excel / WPS / Numbers thanks to a UTF-8 BOM.

Implementation is clean: a new `app.services.invoice_csv` module owns the CSV layout (`CSV_COLUMNS`, `build_csv_content`, `build_csv_bytes`) and is used by both the standalone export endpoint and the batch-download endpoint. `FileManager.stream_zip` grew an optional `extra_members: list[tuple[str, bytes]]` kwarg so callers can embed in-memory files alongside disk-backed PDFs without temp-directory shenanigans.

README was meaningfully updated for the first time since v0.2.0 — every feature shipped between v0.7.1 and v0.8.4 (multi-folder scan, STATUS preflight, parallel IMAP, dedicated QQ code path, scan telemetry, fault isolation, URL tracker pre-filter, timeout defenses, etc.) is now documented in the Features section.

Security audit pass: no API keys, bcrypt hashes with live passwords, Fernet blobs, private keys, or `IDEA.md`/`*.db`/`.env` content in any tracked file or historical commit. Three CHANGELOG prose references to personal email addresses (quoted inside log-line examples in the v0.7.5 and v0.7.9 entries) replaced with placeholders; four `.gitignore` gaps closed (`.playwright-mcp/`, `backend/data/`, `backend/scripts/`, root-level ad-hoc screenshots).

See [CHANGELOG.md](CHANGELOG.md#085---2026-04-20) for the full patch.

---

## v0.8.4 — Released

**Theme:** QQ Mail actually works now — evidence-based IMAP tuning (initial_folder=None, single-connection, bulk=50, UID-range SEARCH, NOOP keepalive)

Every QQ scan since v0.7.10 showed `emails_scanned=0` even after v0.8.2/v0.8.3's timeout bounding: the scans finished in ~40 min (v0.8.3) or hung 44 min (v0.8.2) without ever successfully fetching a single message. Triangulated diagnosis from a local probe against imap.qq.com + explore agent (code audit) + librarian agent (imap-tools docs, QQ help pages, Chinese dev forums) converged on:

1. **imap-tools' `MailBox.login()` implicitly calls `SELECT INBOX`** which on a 35k-msg mailbox takes 15-17s on QQ. Our 15s connect timeout caught the login itself before it completed — every worker and the main session died before the first FETCH was attempted. Fix: pass `initial_folder=None` to every `.login()` call (4 sites). This single change unblocks all non-QQ providers too.
2. **QQ enforces a per-account connection limit of ~1-2 concurrent sessions.** Our 4 parallel workers reliably triggered `NO [b'System busy!']` and downstream SSL: BAD_LENGTH corruption. Fix: QQ-specific `QQ_FETCH_WORKERS=1` forces the single-connection path.
3. **`bulk=500` FETCH on 35k-msg INBOX times out server-side 30-60s before any data returns.** Fix: `QQ_BULK_SIZE=50` + `QQ_INTER_BATCH_SLEEP_SECONDS=1s` + `NOOP` keepalive every 10 batches.
4. **`SEARCH ALL` on large QQ folders may be truncated or time out.** Fix: when a saved highest-UID baseline exists, `_build_qq_fetch_criteria` emits `AND(uid=U(last+1, '*'))` so QQ's server only searches new messages.

v0.8.4 adds a new `_is_qq_imap(account, host=None)` detection helper (matches both `account.type=='qq'` and host `imap.qq.com` / `imap.exmail.qq.com`) and gates all QQ-specific behavior on it. Generic IMAP providers remain on the fast 4-worker parallel path. Per-account socket read timeout is now overridable: QQ uses 180s (its SELECT/SEARCH are legitimately slow), generic stays 120s.

Expected result: QQ scans go from `emails_scanned=0` to actually retrieving messages at QQ's per-IP throughput ceiling (~32 msg/s per v0.8.1 benchmarks). Cold scan of 35k-msg INBOX: ~18-20 min wall clock. Incremental scans afterward: seconds.

See [CHANGELOG.md](CHANGELOG.md#084---2026-04-20).

---

## v0.8.3 — Released

**Theme:** Make v0.8.2's timeouts actually observable — fix ThreadPoolExecutor `with`-block cleanup

Post-deploy validation of v0.8.2 revealed the socket read timeout and `fut.result(timeout=...)` were both firing correctly, but the enclosing `with ThreadPoolExecutor(...) as pool:` block's implicit `pool.shutdown(wait=True)` was joining every still-running (stalled) worker thread on `__exit__`. Net effect: the scan still hung indefinitely even though every v0.8.2 defense was technically working. Observed on production by triggering a QQ full rescan, watching `tcpdump -i any port 993` show 0 packets for 25+ minutes while `/proc/PID/task/*/wchan` showed four workers frozen in `do_poll` with zero CPU delta.

Fix: replace `with pool:` with manual `pool = ThreadPoolExecutor(...)` + `try/finally: pool.shutdown(wait=False, cancel_futures=True)`. The retry loop now proceeds as soon as every future has returned or hit its `IMAP_PARALLEL_WORKER_TIMEOUT`, rather than blocking on the stalled workers. Worst-case wall-clock for the retry-then-fallback path is now `~10 min 10 s` (`2 × 300s` + one `5s` sleep) instead of unbounded.

See [CHANGELOG.md](CHANGELOG.md#083---2026-04-19).

---

## v0.8.2 — Released

**Theme:** IMAP scan hang bulletproofing — socket read timeout, parallel worker timeout, fail-resume partial preservation

Diagnosed from a live production incident: QQ IMAP full-mailbox scans were appearing to freeze on "Folder 1/23 · INBOX · fetching ~35626 msgs · processed=0" for 44+ minutes at a time (two prior scans ran 2 hours and 44 minutes respectively, both finishing with `emails_scanned=0`). Root cause: `imap-tools` `MailBox(...)` has no socket read timeout, so when QQ returns `NO [b'System busy!']` to a parallel worker and the code falls back to the single-connection retry path, the scan thread blocks in `imaplib.readline()` on a half-open SSL socket until the SSL layer eventually raises `[SSL: BAD_LENGTH]` 44 minutes later. The v0.7.8 TCP keepalive didn't catch this because QQ's failure mode is application-level stall with ACKed keepalive probes.

v0.8.2 adds a three-layer timeout defense: `IMAP_CONNECT_TIMEOUT=15s` on the TCP handshake, `IMAP_READ_TIMEOUT=120s` via `sock.settimeout()` on the underlying client socket (alongside the existing keepalive), and `IMAP_PARALLEL_WORKER_TIMEOUT=300s` on every `future.result()` call. Also adds a single-retry path for transient "System busy" responses before falling back to single-conn, preserves partial messages from failed parallel attempts (fail-resume through retries), and bracket-wraps `mailbox.uids()` with progress callbacks so the UI doesn't appear frozen during the 30–120s server-side SEARCH on Chinese providers.

`IMAP_CONNECTION_ERRORS` tuple expanded with `imaplib.IMAP4.abort`, `socket.timeout`, and `TimeoutError` so the new timeouts don't trigger uncaught exceptions that cascade-kill the account scan.

**Performance note (documentation-only):** this release does NOT make individual folders scan faster on QQ/163 — the v0.8.1 benchmarks already proved QQ's per-IP processing ceiling is ~32 msg/s. A cold 35k-msg INBOX will still take ~18 minutes of wall clock. v0.8.2 prevents the **hang** that made scans appear stuck, and ensures that when a folder eventually fails, the scanner advances to the next folder rather than being cascade-killed on SSL corruption.

See [CHANGELOG.md](CHANGELOG.md#082---2026-04-19) for the full patch.

---

## v0.8.1 — Released

**Theme:** URL pre-download filter for tracking pixels & unsubscribe links

Live v0.7.10 + v0.8.0 QQ rescan showed ~282 errors per scan coming from downloads of tracking pixels, unsubscribe links, and analytics beacons that `analyze_email` had selected as `best_download_url`. Each one consumed a full HTTP round-trip, LLM extraction attempt, and failed PDF parse. v0.8.1 adds a pre-flight URL filter (`_is_blocked_download_url`) that rejects known tracker hosts, path fragments, and image extensions before any network call, plus Content-Type validation after GET to catch HTML responses from CDN error pages. Closes the first ROADMAP known-issue (`linktrace.triggerdelivery.com downloaded as fake PDF`).

See [CHANGELOG.md](CHANGELOG.md) for the full blocklist.

---

## v0.8.0 — Released

**Theme:** IMAP 3.35× parallel cold-scan + account page UX

Benchmarks confirmed QQ Mail's IMAP throughput ceiling is ~32 msg/s per IP regardless of header size or pipelining — but parallel connections (4 workers) beat the ceiling by distributing across QQ's per-connection quota. v0.8.0 adds `IMAP_FETCH_WORKERS=4` (configurable) with safe per-folder fallback to single-connection on worker errors. Also fixes the account page raw JSON blob display with a compact `5 folders · 47,849 messages · UID 40993` summary.

See [CHANGELOG.md](CHANGELOG.md) for full benchmark details.

---

## v0.7.10 — Released

**Theme:** Critical correctness hotfix — hydrate-before-classify

v0.7.9's folder-level telemetry made a latent correctness bug visible: QQ IMAP account had processed 103,732 emails across two scans and saved 0 invoices (vs. Outlook's 201/35,631). Root cause: `_process_single_email` was calling the tier-1 classifier BEFORE hydrating unhydrated emails, then only hydrating if tier-1 had already returned `is_invoice=True`. For IMAP emails that arrive metadata-only (no body, no attachments), tier-1 correctly rejected them as "no content or keywords" — but that rejection blocked the hydration that would have made classification correct. An impossible-to-pass Catch-22 introduced in v0.7.2's lazy-fetch refactor. The fix unconditionally hydrates any `is_hydrated=False` email before classifying.

See [CHANGELOG.md](CHANGELOG.md) for full details including production telemetry.

---

## v0.7.9 — Released

**Theme:** Frontend surfaces v0.7.8's folder telemetry

v0.7.8 wired up rich folder-level scan telemetry (`total_folders`, `current_folder_idx`, `current_folder_name`, `folder_fetch_msg`) all the way to the `/api/v1/scan/progress` API — but the Vue component rendering the progress bar was still showing only the v0.7.4-era fields, so live scans still looked like a frozen bar to end users. v0.7.9 adds the frontend pieces: new "Folder X/Y" line, "Fetching …" status from the scanner's own prose message, a dedicated Folders progress bar, and extended `ScanProgressData` TypeScript interface. No backend changes.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.8.0+ — Deferred (from v0.7.8 research)

Ranked by expected speedup, deferred because each requires larger refactors that didn't fit v0.7.8's ship-window:

| # | Optimization | Expected ROI | Complexity |
|---|---|---|---|
| 1 | `UID SEARCH UID N+1:*` server-side filter (replaces client-side UID filtering) | 20–100× on incremental scans | S |
| 2 | Generator streaming scanner → scheduler pipeline (starts classifying as emails arrive; ends the "accumulate everything in memory" phase) | 500 MB → ~50 MB RSS on 100k mailboxes; also unblocks LLM classification during fetch | M |
| 3 | Reconnect-with-backoff wrapper (exponential backoff on QQ "System busy!", 90/180/300s; auto-reconnect after session drop) | reliability, prevents full-scan abort | M |
| 4 | QQ-specific defensive config: `max_workers=1`, `bulk=200`, inter-folder sleep, reconnect-every-N-folders | unlocks practically-stable QQ scans | M |
| 5 | Parallel per-folder fetch (2–3 connections per account) for Gmail/Outlook | 2–4× on large multi-folder accounts | M |
| 6 | Bloom filter for cross-folder Message-ID dedup | O(1) lookup on Gmail All-Mail-style duplicates | S |
| 7 | Selective header fetch (`BODY.PEEK[HEADER.FIELDS (...)]`) | 1.5–2× transfer reduction | M |
| 8 | CONDSTORE / MODSEQ delta fetch (RFC 7162) on servers that advertise it | ∞ on revisits where CONDSTORE is supported (Gmail, Outlook, Dovecot; not QQ) | L |

Reference: see internal research findings in the v0.7.8 PR description and the full synthesis in the librarian exploration that drove the release plan.

---

### Known Issues

| Issue | Notes |
|-------|-------|
| ~~**Email tracking pixel URLs downloaded as fake PDFs**~~ | **Resolved in v0.8.1** via `_is_blocked_download_url` pre-flight filter + Content-Type validation. |
| **Nuonuo e-invoice download links return QR-code HTML pages** | Chinese e-invoice platforms (`nnfp.jss.com.cn`, `fp.nuonuo.com`) serve invoice download links that redirect to interactive QR-code web pages, not direct PDF/XML downloads. The CDN/anti-crawl protection on these platforms blocks server-side download. Fix: document that scanning for this platform family requires the PDF to be attached to the email, not just linked. Alternatively, explore a browser-based fetch path or nuonuo open API. |
| **SQLite `database is locked` under EMAIL_CONCURRENCY=50** | Pre-existing, surfaces more visibly during v0.7.5 full rescans because all folders are processed. Tune IMAP-specific concurrency or switch to WAL busy-timeout. |

### Planned improvements

- **Automatic light/dark mode switch** (user-requested 2026-04-19) — respect `prefers-color-scheme` media query by default, with a manual toggle in the top bar to override to "Light / Dark / Auto". Persist preference in `localStorage`. Helps users viewing the dashboard at night. Stack-appropriate: Tailwind has `dark:` variants built in, so the lift is a `useColorMode` composable + adding `dark:` classes across `InvoiceList`, `InvoiceDetail`, `SettingsView`, and `ScanProgressBar`. Estimated effort: M (maybe 4-6 hours of CSS variant work).
- PWA / installable app
- Faceted filter sidebar
- Vendor name normalization
- Scheduled digest email notifications
- In-app backup / restore
- Drag-and-drop manual invoice upload
- Keyboard shortcuts
- Re-extract button (re-run LLM on stored file)
- Multi-user support
- Docker Compose setup
- Structured JSON logging
- Excel export (via openpyxl)
- Inline-image QR decoding (deferred from v0.7.2)
- Per-account scan concurrency tuning

---

## v0.5.0+ — Future (historical pre-0.7.x planning, superseded)

### Known Issues

| Issue | Notes |
|-------|-------|
| **Email tracking pixel URLs downloaded as fake PDFs** | The body-link scanner follows all URLs in invoice-related emails, including email tracking pixels (e.g. `linktrace.triggerdelivery.com`). These return HTML/images, not PDFs. Parser fails with "No /Root object" and moves on. Fix: v0.6.0 URL pre-filter should check Content-Type header before attempting PDF parse. |
| **Nuonuo e-invoice download links return QR-code HTML pages** | Chinese e-invoice platforms (`nnfp.jss.com.cn`, `fp.nuonuo.com`) serve invoice download links that redirect to interactive QR-code web pages, not direct PDF/XML downloads. The CDN/anti-crawl protection on these platforms blocks server-side download. Fix: document that Outlook scanning for this platform family requires the PDF to be attached to the email, not just linked. Alternatively, explore a browser-based fetch path or nuonuo open API. |

### Planned improvements

### Planned improvements

#### LLM-first email analysis — combined classify + link selection (shipped in v0.5.6)

**Architecture shift:** The 3-tier heuristic classifier is replaced with a 2-step pipeline:

1. **Tier 1 only** (hard negatives, free): bulk mail headers OR no content/keywords → skip immediately
2. **Single LLM call for everything else** → returns `EmailAnalysis` with:
   - `is_invoice_related` + `invoice_confidence`
   - `best_download_url` (ONE URL from the explicit links list, or null)
   - `url_confidence` (gate at ≥0.6 before downloading)
   - `url_is_safelink` (resolve Outlook SafeLinks before fetch)
   - `extraction_hints` (platform, format, visible invoice fields for downstream parser)
   - `skip_reason` (why not invoice, when false)

**Why LLM is actually faster here:** Previous pipeline followed ALL body links (3-10s × N links each). New pipeline: one 1-3s LLM call → at most ONE targeted download. Real-world speedup is 10-100x for invoice-dense mailboxes.

**SafeLink support:** Outlook wraps all URLs in `*.safelinks.protection.outlook.com`. The LLM is explicitly instructed to select SafeLinks when no direct URL is available, and the scanner resolves them before downloading.

**Known limitations still tracked below:**

Current state: the scan progress SSE shows account/email/attachment counters but misses meaningful state information:

| Signal | Currently shown | Proposed |
|--------|----------------|----------|
| Classification decision | ❌ | "Classified as invoice (Tier 1 — keyword match)" |
| Download URL + outcome | ❌ | "Downloading link 3/5 … saved / parse_error / skipped" |
| Extraction method | ❌ | "Parsing PDF via QR code extraction (confidence 97%)" |
| Invoice save confirmation | ❌ | "Saved: 诺诺科技_发票_2024.pdf (¥1,234.56)" |
| Classification tier used | ❌ | Tier 1/2/3 indicator next to email subject |

Five new fields to add to `ScanProgress` dataclass:
- `current_attachment_url` — URL currently being downloaded
- `current_download_outcome` — downloading / saved / parse_error / skipped
- `current_parse_method` — qr / xml_xpath / ofd_struct / llm / regex
- `current_parse_format` — pdf / xml / ofd
- `last_classification_tier` — 1 / 2 / 3

Exact insertion points in `scheduler.py` already mapped:
- Line 249: emit tier after classification
- Line 275-277: emit URL + outcome after `_download_linked_invoice()`
- Line 304-316: emit extraction_method + source_format after `parse_invoice()`
- Line 385-414: emit save confirmation with filename + amount after `db.flush()`

Frontend: `ScanProgressBar` extended to show these details inline.



Currently, triggering a manual scan shows only a brief "Scanning..." spinner, then nothing until the scan finishes (or fails). There is no way to know:
- Whether the scan is actually running
- How many emails have been processed vs remaining
- Which account is being scanned right now
- Whether a slow scan is making progress or stuck

v0.3.0 should implement **real-time scan progress** visible in the UI:

**Backend: In-process progress bus**
- Module-level `ScanProgress` singleton updated at each loop boundary in `scan_all_accounts()`
- 20 identified signal points: account start/finish, email classification, attachment parsing, download links, webhook delivery
- SSE endpoint `GET /scan/progress/stream` pushes state changes to connected browsers
- Polling fallback `GET /scan/progress` for environments where SSE is blocked
- Concurrent scan guard (asyncio lock) prevents duplicate runs from scheduler + manual trigger

**Frontend: Nested progress display**
- `ScanProgressBar` component with 3 nested bars: overall → per-account → per-email
- Real-time status line: "Parsing invoice_2024.pdf" / "Email: 发票通知 (45/120)" / "Account 2/3: work@outlook.com"
- Counters: emails processed, invoices found, errors
- Done/error banners with summary
- `useScanProgress()` composable connecting via SSE with polling fallback

**Infrastructure:**
- `X-Accel-Buffering: no` in nginx config for SSE endpoint
- JWT auth via query param for `EventSource` (native `EventSource` can't set headers)
- Heartbeat pings every 30s to keep connections alive through proxies

**Estimated effort:** L (3–5 days)

### Tiered email classification pipeline (P0 for v0.3.0)

The current approach sends every email through the LLM API for classification — expensive and slow when scanning thousands of emails. v0.3.0 should implement a 3-tier classification pipeline that eliminates >90% of LLM calls:

**Tier 1 — Free local signals (instant, zero cost):**
- Attachment filenames: `.pdf`, `.xml`, `.ofd` with invoice-like names → instant positive
- Known sender addresses: configurable allowlist (tax bureau domains, e-invoice platforms)
- Subject keyword match: `发票`, `invoice`, `开票`, `报销`, `税` → strong positive signal
- No attachments + no links + no keywords → instant negative skip

**Tier 2 — Cheap metadata enrichment (still no LLM):**
- Attachment MIME types: `application/pdf`, `text/xml`
- Body URL pattern matching: known invoice download domains
- Email header analysis: `X-Mailer`, `List-Unsubscribe` (newsletters → skip)

**Tier 3 — LLM fallback (only for ambiguous emails):**
- Feed enriched context: subject + sender + attachment filenames + body links + body text (first 2KB)
- Only called when Tier 1 and Tier 2 are inconclusive

Expected impact: **90%+ of emails resolved locally**, LLM called for <10%, massive cost and latency reduction on large mailboxes.

### Other planned items

- Dark mode
- PWA / installable app
- Faceted filter sidebar
- Vendor name normalization
- Scheduled digest email notifications
- In-app backup / restore
- Drag-and-drop manual invoice upload
- Keyboard shortcuts
- Re-extract button (re-run LLM on stored file)
- Multi-user support
- Docker Compose setup
- Structured JSON logging
- Excel export (via openpyxl)

---

> **Documentation rule:** CHANGELOG.md, README.md, and ROADMAP.md are updated with every release. No exceptions.
