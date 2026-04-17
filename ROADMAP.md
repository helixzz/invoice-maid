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

## v0.5.0+ — Future

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

> **Documentation rule:** CHANGELOG.md and README.md are updated with every release. No exceptions.
