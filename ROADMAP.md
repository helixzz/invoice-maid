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

## v0.7.9+ — Deferred (from v0.7.8 research)

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
| **Email tracking pixel URLs downloaded as fake PDFs** | The body-link scanner follows all URLs in invoice-related emails, including email tracking pixels (e.g. `linktrace.triggerdelivery.com`). These return HTML/images, not PDFs. Parser fails with "No /Root object" and moves on. Fix: URL pre-filter should check Content-Type header before attempting PDF parse. |
| **Nuonuo e-invoice download links return QR-code HTML pages** | Chinese e-invoice platforms (`nnfp.jss.com.cn`, `fp.nuonuo.com`) serve invoice download links that redirect to interactive QR-code web pages, not direct PDF/XML downloads. The CDN/anti-crawl protection on these platforms blocks server-side download. Fix: document that scanning for this platform family requires the PDF to be attached to the email, not just linked. Alternatively, explore a browser-based fetch path or nuonuo open API. |
| **SQLite `database is locked` under EMAIL_CONCURRENCY=50** | Pre-existing, surfaces more visibly during v0.7.5 full rescans because all folders are processed. Tune IMAP-specific concurrency or switch to WAL busy-timeout. |

### Planned improvements

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
