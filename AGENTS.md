# PROJECT KNOWLEDGE BASE

**Generated:** 2026-11-28

## OVERVIEW

Invoice Maid (米尔 Miri) — Self-hosted AI invoice ledger and multimodal agent assistant. FastAPI backend + Vue 3 "Warm Stationery Ledger" frontend. Scans email inboxes via IMAP/POP3/Outlook (Graph Delta Query)/QQ and SaaS portals, classifies emails with tiered heuristics + OpenAI-compatible LLM (multi-model fallback chains, circuit breaking, independent embeddings, VLM PDF fallback), parses PDF/XML/OFD invoices with true multi-currency support, manages reimbursement workflows &amp; Excel/ZIP exports, and runs an IM-agnostic Agent Loop (`MaidAgentRunner`) with 14 tools, Set-of-Mark VLM QR receipt auto-claiming, persistent memory rules, and a dedicated Telegram Bot.

## STATUS

**v2.4.1 — Refreshed README &amp; v2.4 WebUI screenshots, fixed `update_invoice` notes field persistence, 14-tool Miri Agent with Set-of-Mark VLM QR claiming, and 100% CI parity.** 811+ backend tests (100% line &amp; branch coverage), 23 Vitest unit tests, 21 Playwright E2E tests.

## STRUCTURE

```
invoice-maid/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan + rich /health + SPA serving
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── database.py          # Async SQLAlchemy + FTS5 + sqlite-vec
│   │   ├── deps.py              # Auth & tenant-isolation dependencies (CurrentUser)
│   │   ├── models/              # SQLAlchemy 2.0 ORM (Invoice, EmailAccount, ScanLog, LLMCache, AppSettings, ExtractionLog, FailedEmail, CorrectionLog, SavedView, WebhookLog, User, UserSession, InvoiceProfile, MaidMemory, InvoiceClaimTask, MaidMessage)
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/                 # FastAPI routers (auth, admin, invoices, downloads, email_accounts, scan, stats, views, ai_settings, classifier_settings, notification_settings, profiles, maid, test_helpers)
│   │   ├── services/            # Business logic (ai_service, email_classifier, email_scanner, graph_http, failed_email_queue, invoice_parser, invoice_ingestion, manual_upload, invoice_csv, invoice_xlsx, telegram_notifier, telegram_agent_bot, qr_receipt_decoder, claim_form_agent, vision_browser_agent, maid_agent, file_manager, search_service, settings_resolver, scrapers/)
│   │   ├── tasks/               # APScheduler scan orchestration, DLQ retry & maid heartbeat jobs
│   │   └── prompts/             # LLM prompt templates (classify_email.txt, extract_invoice.txt)
│   ├── alembic/                 # DB migrations (0001–0023)
│   ├── tests/                   # 811+ tests, 100% line & branch coverage enforced
│   ├── pyproject.toml
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/          # Warm Stationery shared UI (AppLayout, MaidChatDrawer, badges, dialogs, toast, ScanProgressBar) + modular settings/ panels (AI, Classifier, Notifications, InvoiceProfiles)
│   │   ├── views/               # Login, Register, InvoiceList, InvoiceDetail, InvoiceUpload, MaidWorkspace, Settings, Admin
│   │   ├── stores/              # Pinia stores (auth, invoices)
│   │   └── api/                 # Typed axios API client
│   ├── e2e/                     # 6 Playwright E2E specs (21 tests)
│   └── dist/                    # Built frontend (committed)
├── docs/
│   └── VI-DESIGN-SYSTEM.md      # Miri (米尔) brand VI & Warm Stationery Ledger spec
├── assets/
│   ├── images/                  # Transparent 2026 Miri avatar & workflow SVG
│   └── screenshots/             # Current v2.4 WebUI screenshots for README
├── deploy/                      # systemd + nginx + idempotent install/upgrade scripts
├── README.md
├── CHANGELOG.md
└── ROADMAP.md
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Requirements / spec | `IDEA.md` | Chinese; private, never push |
| Brand & UI design system | `docs/VI-DESIGN-SYSTEM.md` | Miri (米尔) tokens, typography, layout & tone |
| API surface | `backend/app/api/` | All routers mounted under `/api/v1/` |
| Email scanning & Graph retry | `backend/app/services/email_scanner.py`, `graph_http.py` | IMAP/POP3/QQ/Outlook Delta + 429 Retry-After backoff |
| Invoice parsing & VLM render | `backend/app/services/invoice_parser.py` | PDF/XML/OFD + QR + currency detect + PyMuPDF PNG render |
| Shared ingestion pipeline | `backend/app/services/invoice_ingestion.py` | Unified enrich/VLM/currency/scam/VAT gate + memory rules |
| LLM routing & circuit breaker | `backend/app/services/ai_service.py` | Ordered fallback chain, circuit breaker, independent embed endpoint, VLM, tool-calling |
| Persistent retry queue (DLQ) | `backend/app/services/failed_email_queue.py` | Exponential backoff retry queue + watermark protection |
| Miri Agent Loop (14 tools) | `backend/app/services/maid_agent.py` | Tool-calling chat, memory rules, QR intake, heartbeat |
| QR decoding & Vision Browser Agent | `backend/app/services/qr_receipt_decoder.py`, `vision_browser_agent.py`, `claim_form_agent.py` | Thermal-line QR repair, WeChat code detection, Set-of-Mark VLM browser loop |
| Telegram notifications & Agent Bot | `backend/app/services/telegram_notifier.py`, `telegram_agent_bot.py` | Unified Miri channel, live progress, `sendPhoto`, Markdown→HTML |
| Export (CSV / Excel / Pack) | `backend/app/services/invoice_csv.py`, `invoice_xlsx.py` | Zero-dependency OOXML `.xlsx` + reimbursement ZIP pack |
| Settings UI panels | `frontend/src/components/settings/` | Modular AI / Classifier / Telegram / InvoiceProfiles panels |
| Config reference | `backend/.env.example` | All env vars documented |
| Deployment | `deploy/` | systemd + nginx + idempotent upgrade script |

## CONVENTIONS

- Python 3.11+, SQLAlchemy 2.0 `Mapped[]` style, async everywhere
- 100% unit test coverage required (`pytest --cov=app --cov-fail-under=100`)
- `.coveragerc` must have `concurrency = greenlet, thread`
- `expire_on_commit=False` on all session factories
- APScheduler `--workers 1` mandatory (in-process scheduler & Telegram polling)
- SPA catch-all route MUST be registered last in `main.py`
- Fernet encryption for stored passwords, API keys, and Telegram bot tokens (key from `JWT_SECRET`)
- LLM calls cached by SHA-256(`prompt_type + content`)
- Invoice files: `{buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf`

## ANTI-PATTERNS

- Pushing credentials, API keys, Bot tokens, `.env`, `IDEA.md`, or `*.db` to git
- Shipping features without updating `README.md` + `CHANGELOG.md` + `ROADMAP.md`
- Multiple uvicorn workers (duplicates scheduler jobs & causes Telegram 409 Conflict)
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
