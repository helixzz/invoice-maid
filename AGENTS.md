# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-17

## OVERVIEW

Invoice Maid — AI-powered invoice extraction service. FastAPI backend + Vue 3 frontend. Scans email inboxes via IMAP/POP3/Outlook/QQ, classifies emails with LLM, parses PDF/XML/OFD invoices, extracts structured fields, serves web UI for search and batch download. Single-user, self-hosted.

## STATUS

**v0.1.0 — Feature complete.** All core features implemented, 100% test coverage, ready for deployment.

## STRUCTURE

```
invoice-maid/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan + SPA serving
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── database.py          # Async SQLAlchemy + FTS5 + sqlite-vec
│   │   ├── deps.py              # Auth dependency (CurrentUser)
│   │   ├── models/              # SQLAlchemy 2.0 ORM (Invoice, EmailAccount, ScanLog, LLMCache)
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/                 # FastAPI routers (auth, invoices, downloads, accounts, scan)
│   │   ├── services/            # Business logic (AI, email scanner, invoice parser, file manager, search)
│   │   ├── tasks/               # APScheduler scan orchestration
│   │   └── prompts/             # LLM prompt templates (classify, extract)
│   ├── alembic/                 # DB migrations
│   ├── tests/                   # 92 tests, 100% coverage
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/                     # Vue 3 + Vite + Tailwind + Pinia
│   └── dist/                    # Built frontend (committed)
├── deploy/                      # systemd + nginx templates
├── README.md
└── CHANGELOG.md
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Requirements / spec | `IDEA.md` | Chinese; private, never push |
| API surface | `backend/app/api/` | 14 endpoints under /api/v1/ |
| Email scanning | `backend/app/services/email_scanner.py` | 4 scanner implementations |
| Invoice parsing | `backend/app/services/invoice_parser.py` | PDF/XML/OFD + QR decode |
| LLM integration | `backend/app/services/ai_service.py` | instructor + cache |
| Scheduler | `backend/app/tasks/scheduler.py` | APScheduler scan_all_accounts |
| Frontend views | `frontend/src/views/` | Login, InvoiceList, InvoiceDetail, Settings |
| Config reference | `backend/.env.example` | All env vars documented |
| Deployment | `deploy/` | systemd + nginx templates |

## CONVENTIONS

- Python 3.11+, SQLAlchemy 2.0 `Mapped[]` style, async everywhere
- 100% unit test coverage required (`pytest --cov-fail-under=100`)
- `.coveragerc` must have `concurrency = greenlet, thread`
- `expire_on_commit=False` on all session factories
- APScheduler `--workers 1` mandatory (in-process scheduler)
- SPA catch-all route MUST be registered last in main.py
- Fernet encryption for stored email passwords (key from JWT_SECRET)
- LLM calls cached by SHA-256(prompt_type + content)
- Invoice files: `{buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf`

## ANTI-PATTERNS

- Pushing credentials, API keys, `.env`, `IDEA.md`, or `*.db` to git
- Shipping features without updating README.md + CHANGELOG.md
- Multiple uvicorn workers (duplicates scheduler jobs)
- Forgetting `easyofd.del_data()` cleanup after OFD parsing
- Using sync imap-tools without `run_in_executor`

## COMMANDS

```bash
# Backend dev
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload

# Tests (100% required)
cd backend && pytest --cov=app --cov-report=term-missing --cov-fail-under=100

# Frontend dev
cd frontend && npm run dev

# Frontend build (dist/ committed)
cd frontend && npm run build
```
