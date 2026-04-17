# Roadmap

## v0.1.0 — Released

First release. Full email-to-invoice pipeline with PDF/XML/OFD parsing, LLM-powered classification and extraction, FTS5 + semantic search, Vue 3 dashboard, and CI/release automation.

See [CHANGELOG.md](CHANGELOG.md) for full details.

---

## v0.2.0 — In Progress

**Theme:** Reliability, Data Quality, Branding, and Operational Hardening

### Minimum Shippable

| # | Feature | Priority | Status |
|---|---------|----------|--------|
| 1 | **Typed exception handlers** — replace bare `except Exception` in email scanner with specific exceptions | P0 | Planned |
| 2 | **Per-email extraction audit log** — track why each email was saved, skipped, or failed | P0 | Planned |
| 3 | **APScheduler startup guard** — detect multi-worker misconfiguration and warn | P0 | Planned |
| 4 | **Semantic search pagination** — fix hardcoded page=1 in semantic search endpoint | P1 | Planned |
| 5 | **Project icon in Web GUI** — favicon, login page, nav bar branding | P0 | Planned |
| 6 | **AI model settings via Web UI** — manage LLM base URL, API key, model selection from Settings page; retrieve model list from provider | P0 | Planned |
| 7 | **Manual invoice correction** — edit extracted fields inline with audit trail | P0 | Planned |
| 8 | **Confidence display** — show extraction confidence and method as colored badges | P1 | Planned |
| 9 | **CSV export** — export filtered invoice list as CSV | P1 | Planned |
| 10 | **Rich health endpoint** — report DB, scheduler, sqlite-vec status and invoice stats | P1 | Planned |
| 11 | **Rate limiting** — brute-force protection on login endpoint | P0 | Planned |
| 12 | **Documentation updates** — README + CHANGELOG for all shipped changes | P0 | Planned |

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

```
Wave 1 (parallel, no dependencies):
  Track A — Reliability & Observability
  Track E — Security & Operations
  Track G — Branding & AI Model Settings

Wave 2 (parallel, after Wave 1):
  Track B — Data Quality (manual correction, confidence, duplicates)
  Track C — Export & Integrations (CSV, webhooks)
  Track D — Search & Discovery (saved views, analytics, similar)

Wave 3 (after all):
  Track F — Documentation & Changelog (MANDATORY before release tag)
```

---

## v0.3.0+ — Future

Ideas under consideration (not committed):

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

---

> **Documentation rule:** CHANGELOG.md and README.md are updated with every release. No exceptions.
