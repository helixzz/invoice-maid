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
