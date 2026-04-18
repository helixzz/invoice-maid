# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
| Outlook (`user@example.com`) | 470 (INBOX, last 30 days) | **35,631** (all folders, full history) | 7 | **208** |

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
