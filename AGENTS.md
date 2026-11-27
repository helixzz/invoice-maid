# PROJECT KNOWLEDGE BASE

**Generated:** 2026-11-27

## OVERVIEW

Invoice Maid — AI-powered invoice extraction service. FastAPI backend + Vue 3 frontend. Scans email inboxes via IMAP/POP3/Outlook (Graph Delta Query)/QQ and SaaS portals, classifies emails with tiered heuristics + OpenAI-compatible LLM (supporting multi-model fallback chains, circuit breaking, and multimodal VLM fallback), parses PDF/XML/OFD invoices, extracts true multi-currency structured fields, supports reimbursement workflows & Telegram alerts, and serves a multi-user web UI for search, editing, and batch export.

## STATUS

**v1.4.0 — Hardened production architecture, multi-currency, VLM fallback, Telegram IM alerts, and reimbursement workflow.** 797+ backend tests with 100% line & branch coverage, 23 frontend unit tests, 6 Playwright E2E specs.

## STRUCTURE

```
invoice-maid/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan + rich /health + SPA serving
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── database.py          # Async SQLAlchemy + FTS5 + sqlite-vec
│   │   ├── deps.py              # Auth & tenant-isolation dependencies (CurrentUser)
│   │   ├── models/              # SQLAlchemy 2.0 ORM (Invoice, EmailAccount, ScanLog, LLMCache, AppSettings, ExtractionLog, FailedEmail, CorrectionLog, SavedView, WebhookLog, User, UserSession)
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/                 # FastAPI routers (auth, admin, invoices, downloads, email_accounts, scan, stats, views, ai_settings, classifier_settings, notification_settings, test_helpers)
│   │   ├── services/            # Business logic (ai_service, email_classifier, email_scanner, graph_http, failed_email_queue, invoice_parser, invoice_ingestion, manual_upload, invoice_csv, invoice_xlsx, telegram_notifier, file_manager, search_service, settings_resolver, scrapers/)
│   │   ├── tasks/               # APScheduler scan orchestration & maintenance jobs
│   │   └── prompts/             # LLM prompt templates (classify_email.txt, extract_invoice.txt)
│   ├── alembic/                 # DB migrations (0001–0022)
│   ├── tests/                   # 797+ tests, 100% coverage enforced
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/          # Shared UI + modular settings/ panels (AI, Classifier, Notifications)
│   │   ├── views/               # Login, Register, InvoiceList, InvoiceDetail, InvoiceUpload, Settings, Admin
│   │   ├── stores/              # Pinia stores (auth, invoices)
│   │   └── api/                 # Typed axios API client
│   └── dist/                    # Built frontend (committed)
├── deploy/                      # systemd + nginx + upgrade templates
├── README.md
├── CHANGELOG.md
└── ROADMAP.md
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Requirements / spec | `IDEA.md` | Chinese; private, never push |
| API surface | `backend/app/api/` | All routers mounted under `/api/v1/` |
| Email scanning & Graph retry | `backend/app/services/email_scanner.py`, `graph_http.py` | IMAP/POP3/QQ/Outlook Delta + 429 Retry-After backoff |
| Invoice parsing & VLM render | `backend/app/services/invoice_parser.py` | PDF/XML/OFD + QR + currency detect + PyMuPDF PNG render |
| Shared ingestion pipeline | `backend/app/services/invoice_ingestion.py` | Unified enrich/VLM/currency/scam/VAT gate for email + upload |
| LLM routing & circuit breaker | `backend/app/services/ai_service.py` | Ordered fallback chain, circuit breaker, independent embed endpoint, VLM |
| Persistent retry queue (DLQ) | `backend/app/services/failed_email_queue.py` | Exponential backoff retry queue + watermark protection |
| Telegram IM notifications | `backend/app/services/telegram_notifier.py` | Error alerts (15m cooldown) + scan summary |
| Export (CSV / Excel / Pack) | `backend/app/services/invoice_csv.py`, `invoice_xlsx.py` | Zero-dependency OOXML `.xlsx` + reimbursement ZIP pack |
| Settings UI panels | `frontend/src/components/settings/` | Modular AI / Classifier / Telegram notification panels |
| Config reference | `backend/.env.example` | All env vars documented |
| Deployment | `deploy/` | systemd + nginx + idempotent upgrade script |

## CONVENTIONS

- Python 3.11+, SQLAlchemy 2.0 `Mapped[]` style, async everywhere
- 100% unit test coverage required (`pytest --cov=app --cov-fail-under=100`)
- `.coveragerc` must have `concurrency = greenlet, thread`
- `expire_on_commit=False` on all session factories
- APScheduler `--workers 1` mandatory (in-process scheduler)
- SPA catch-all route MUST be registered last in `main.py`
- Fernet encryption for stored passwords, API keys, and Telegram bot tokens (key from `JWT_SECRET`)
- LLM calls cached by SHA-256(`prompt_type + content`)
- Invoice files: `{buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf`

## ANTI-PATTERNS

- Pushing credentials, API keys, Bot tokens, `.env`, `IDEA.md`, or `*.db` to git
- Shipping features without updating `README.md` + `CHANGELOG.md` + `ROADMAP.md`
- Multiple uvicorn workers (duplicates scheduler jobs)
- Forgetting `easyofd.del_data()` cleanup after OFD parsing
- Using sync `imap-tools` without `run_in_executor`
- **Pushing code without bumping the version** — every behavioral change (feature, fix, refactor) must increment `backend/pyproject.toml` version AND move/create the release tag on HEAD before pushing.

## VERSION BUMP RULE (NON-NEGOTIABLE)

Every commit batch that changes observable behavior MUST:
1. Increment `backend/pyproject.toml` version (patch: x.x.N → x.x.N+1, or minor/major as appropriate)
2. Add a `CHANGELOG.md` entry under the new version
3. Add a `ROADMAP.md` section under the new version (release summary + link to CHANGELOG)
4. Tag HEAD with the new version after pushing: `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z`
5. Trigger the release workflow so GitHub Release stays in sync

## COMMANDS

```bash
# Backend dev
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload

# Tests (100% required)
cd backend && pytest --cov=app --cov-report=term-missing --cov-fail-under=100

# Frontend dev & tests
cd frontend && npm run dev
cd frontend && npm run test:run

# Frontend build (dist/ committed)
cd frontend && npm run build
```
