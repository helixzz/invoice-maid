# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-17

## OVERVIEW

Invoice Maid — AI-powered invoice extraction service. FastAPI backend + Vue 3 frontend. Scans email inboxes via IMAP/POP3/Outlook/QQ, classifies emails with LLM, parses PDF/XML/OFD invoices, extracts structured fields, serves web UI for search and batch download. Single-user, self-hosted.

## STATUS

**v0.2.0 — Feature complete.** Reliability hardening, data quality, export, branding, AI settings, and operational improvements. 195 tests, 100% coverage.

## STRUCTURE

```
invoice-maid/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan + SPA serving
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── database.py          # Async SQLAlchemy + FTS5 + sqlite-vec
│   │   ├── deps.py              # Auth dependency (CurrentUser)
│   │   ├── models/              # SQLAlchemy 2.0 ORM (Invoice, EmailAccount, ScanLog, LLMCache, AppSettings, ExtractionLog, CorrectionLog, SavedView, WebhookLog)
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/                 # FastAPI routers (auth, invoices, downloads, accounts, scan, stats, views, ai_settings, test_helpers)
│   │   ├── services/            # Business logic (AI, email scanner, invoice parser, file manager, search, settings resolver)
│   │   ├── tasks/               # APScheduler scan orchestration
│   │   └── prompts/             # LLM prompt templates (classify, extract)
│   ├── alembic/                 # DB migrations
│   ├── tests/                   # 195 tests, 100% coverage
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
| AI settings | `backend/app/services/settings_resolver.py` | DB-backed config with .env fallback |
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
- **Pushing code without bumping the version** — every behavioral change (feature, fix, refactor) must increment `backend/pyproject.toml` version AND move the release tag to HEAD before pushing. Silent code drift from the tag is never acceptable.

## VERSION BUMP RULE (NON-NEGOTIABLE)

Every commit batch that changes observable behavior MUST:
1. Increment `backend/pyproject.toml` version (patch: x.x.N → x.x.N+1, or minor/major as appropriate)
2. Add a CHANGELOG.md entry under the new version
3. Tag HEAD with the new version after pushing: `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z`
4. Trigger the release workflow so GitHub Release stays in sync

Cadence guide:
- **Patch** (x.y.Z): bug fixes, doc updates, test fixes, minor improvements
- **Minor** (x.Y.0): new user-facing features, new API endpoints, new UI sections
- **Major** (X.0.0): breaking API/DB changes, architecture overhaul

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
