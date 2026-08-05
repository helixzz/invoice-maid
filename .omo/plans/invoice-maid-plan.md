
**Generated:** 2026-04-16  
**Remote:** git@github.com:helixzz/invoice-maid.git  
**Status:** Ready for ultrawork execution  

---

## Confirmed Decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Outlook OAuth | Device code flow — user authenticates once, token cached to `token_cache.json` (gitignored) |
| 2 | QQ Mail | Standard IMAP port 993 + 16-char app password |
| 3 | Frontend dist/ | **Committed to git** — `npm run build` runs before first commit; no Node.js needed on server |
| 4 | Admin auth | JWT (python-jose) + bcrypt-verified password from env var |
| 5 | sqlite-vec | Optional — graceful fallback to FTS5-only if extension unavailable |

---

## Tech Stack (Pinned Versions)

```toml
# pyproject.toml [project.dependencies]
fastapi>=0.115,<1.0
uvicorn[standard]>=0.29
sqlalchemy>=2.0.30,<3.0
aiosqlite>=0.20
alembic>=1.13
apscheduler>=3.10,<4.0
openai>=1.30
instructor>=1.5,<2.0
pydantic>=2.7
pydantic-settings>=2.0
sqlite-vec==0.1.9
imap-tools>=1.7
pdfplumber>=0.11
pymupdf>=1.24
pyzbar>=0.1.9
easyofd>=0.5.6
python-jose[cryptography]>=3.3
passlib[bcrypt]>=1.7
python-multipart>=0.0.9
aiofiles>=23.0
httpx>=0.27
msal>=1.28
msgraph-sdk>=1.0

# [project.optional-dependencies] dev
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=5.0
anyio[trio]>=4.0
```

---

## Directory Structure (Target)

```
invoice-maid/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── deps.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── invoice.py
│   │   │   ├── email_account.py
│   │   │   └── scan_log.py
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── invoice.py
│   │   │   └── email_account.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── invoices.py
│   │   │   ├── email_accounts.py
│   │   │   ├── downloads.py
│   │   │   └── scan.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── auth_service.py
│   │   │   ├── ai_service.py
│   │   │   ├── email_scanner.py
│   │   │   ├── invoice_parser.py
│   │   │   ├── file_manager.py
│   │   │   └── search_service.py
│   │   ├── tasks/
│   │   │   ├── __init__.py
│   │   │   └── scheduler.py
│   │   └── prompts/
│   │       ├── classify_email.txt
│   │       └── extract_invoice.txt
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 0001_initial_schema.py
│   ├── alembic.ini
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── fixtures/
│   │   │   ├── sample_invoice.pdf   (synthetic minimal PDF)
│   │   │   ├── sample_invoice.xml
│   │   │   └── sample_invoice.ofd
│   │   ├── test_api/
│   │   │   ├── __init__.py
│   │   │   ├── test_auth.py
│   │   │   ├── test_invoices.py
│   │   │   ├── test_downloads.py
│   │   │   └── test_email_accounts.py
│   │   ├── test_services/
│   │   │   ├── __init__.py
│   │   │   ├── test_auth_service.py
│   │   │   ├── test_ai_service.py
│   │   │   ├── test_email_scanner.py
│   │   │   ├── test_invoice_parser.py
│   │   │   ├── test_file_manager.py
│   │   │   └── test_search_service.py
│   │   └── test_models/
│   │       ├── __init__.py
│   │       └── test_orm_models.py
│   ├── pyproject.toml
│   ├── .env.example
│   └── .coveragerc
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── main.ts
│   │   ├── router/
│   │   │   └── index.ts
│   │   ├── stores/
│   │   │   ├── auth.ts
│   │   │   └── invoices.ts
│   │   ├── api/
│   │   │   └── client.ts
│   │   ├── views/
│   │   │   ├── LoginView.vue
│   │   │   ├── InvoiceListView.vue
│   │   │   ├── InvoiceDetailView.vue
│   │   │   └── SettingsView.vue
│   │   └── components/
│   │       ├── InvoiceTable.vue
│   │       ├── SearchBar.vue
│   │       ├── DateRangePicker.vue
│   │       ├── PdfPreview.vue
│   │       └── BatchDownloadButton.vue
│   ├── dist/                        ← committed to git
│   ├── public/
│   │   └── favicon.ico
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── tailwind.config.js
├── deploy/
│   ├── invoice-maid.service
│   └── invoice-maid.nginx.conf
├── .gitignore
├── README.md
├── CHANGELOG.md
└── AGENTS.md
```

---

## Key Data Contracts

### `RawEmail` (email_scanner output)
```python
@dataclass
class RawEmail:
    uid: str
    subject: str
    body_text: str
    body_html: str
    from_addr: str
    received_at: datetime
    attachments: list[RawAttachment]   # filename, payload: bytes, mime_type
    body_links: list[str]              # URLs parsed from email body
```

### `ParsedInvoice` (invoice_parser output)
```python
@dataclass
class ParsedInvoice:
    invoice_no: str | None
    buyer: str | None
    seller: str | None
    amount: Decimal | None
    invoice_date: date | None
    invoice_type: str | None
    item_summary: str | None           # AI-generated one-line Chinese summary
    raw_text: str
    source_format: Literal["pdf", "xml", "ofd"]
    extraction_method: Literal["qr", "xml_xpath", "ofd_struct", "llm"]
    confidence: float
```

### `InvoiceExtract` (instructor/Pydantic LLM output)
```python
class InvoiceExtract(BaseModel):
    buyer: str = Field(description="购买方名称")
    seller: str = Field(description="销售方名称")
    invoice_no: str = Field(description="发票号码")
    invoice_date: date = Field(description="开票日期")
    amount: Decimal = Field(gt=0, description="价税合计")
    item_summary: str = Field(description="商品/服务一句话概括，中文")
    invoice_type: str = Field(description="发票类型")
    confidence: float = Field(ge=0, le=1)
```

### ORM Models (SQLAlchemy 2.0 `Mapped[]` style)

**Invoice**
| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| invoice_no | str unique index | |
| buyer | str | |
| seller | str | |
| amount | Numeric(12,2) | |
| invoice_date | Date index | |
| invoice_type | str | |
| item_summary | str nullable | |
| file_path | str | relative to STORAGE_PATH |
| raw_text | str | for FTS5 indexing |
| email_uid | str | source email UID |
| email_account_id | FK EmailAccount | |
| created_at | DateTime | |

**EmailAccount**
| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| name | str | display name |
| type | str | imap / pop3 / outlook / qq |
| host | str nullable | IMAP/POP3 host |
| port | int nullable | |
| username | str | |
| password_encrypted | str nullable | Fernet-encrypted; never returned via API |
| oauth_token_path | str nullable | path to MSAL token cache file |
| is_active | bool | |
| last_scan_uid | str nullable | idempotency: last processed UID |
| created_at | DateTime | |

**ScanLog**
| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| email_account_id | FK | |
| started_at | DateTime | |
| finished_at | DateTime nullable | |
| emails_scanned | int | |
| invoices_found | int | |
| error_message | str nullable | |

**LLMCache**
| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| content_hash | str unique | SHA-256(prompt_type + content) |
| prompt_type | str | "classify" or "extract" |
| response_json | str | raw JSON string |
| created_at | DateTime | |

**FTS5 virtual table** (raw SQL in migration, not in ORM):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts
USING fts5(
    invoice_no, buyer, seller, invoice_type, item_summary, raw_text,
    content='invoices', content_rowid='id'
);
```

**sqlite-vec virtual table** (raw SQL in startup, excluded from Alembic autogenerate):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS invoice_embeddings
USING vec0(embedding float[1536]);
```

---

## Environment Configuration (`.env.example`)

```bash
# Database
DATABASE_URL=sqlite+aiosqlite:///./data/invoices.db

# Storage
STORAGE_PATH=./data/invoices

# Auth — generate with: python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
ADMIN_PASSWORD_HASH=$2b$12$...
JWT_SECRET=change-me-32-char-random-string-here
JWT_EXPIRE_MINUTES=1440

# LLM (OpenAI-compatible)
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
LLM_EMBED_MODEL=text-embedding-3-small
EMBED_DIM=1536

# Scheduler
SCAN_INTERVAL_MINUTES=60

# Features
SQLITE_VEC_ENABLED=true

# Email accounts — configure via web UI after first launch
# EMAIL_ACCOUNTS env var not used; accounts stored in DB
```

---

## REST API Surface

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/auth/login` | No | Body: `{password}` → `{access_token}` |
| GET | `/api/v1/invoices` | JWT | Query: `q, date_from, date_to, page, size` |
| GET | `/api/v1/invoices/{id}` | JWT | Single invoice |
| DELETE | `/api/v1/invoices/{id}` | JWT | Delete invoice + file |
| POST | `/api/v1/invoices/search` | JWT | Body: `{query}` — semantic search |
| GET | `/api/v1/invoices/{id}/download` | JWT | FileResponse |
| POST | `/api/v1/invoices/batch-download` | JWT | Body: `{ids: []}` → StreamingResponse ZIP |
| GET | `/api/v1/accounts` | JWT | List email accounts |
| POST | `/api/v1/accounts` | JWT | Add account |
| PUT | `/api/v1/accounts/{id}` | JWT | Update account |
| DELETE | `/api/v1/accounts/{id}` | JWT | Remove account |
| POST | `/api/v1/scan/trigger` | JWT | Manual scan trigger |
| GET | `/api/v1/scan/logs` | JWT | Recent scan logs |
| GET | `/api/v1/health` | No | Health check |

---

## Implementation Waves

### Wave 0 — Foundation
**Blocking prerequisite. Run sequentially.**

| ID | Task | Category | Complexity | Description |
|----|------|----------|------------|-------------|
| W0-1 | Git init + remote + .gitignore | `quick` | Low | `git init`, `git remote add origin git@github.com:helixzz/invoice-maid.git`, create `.gitignore` |
| W0-2 | Directory skeleton | `quick` | Low | `mkdir -p` for all directories in the target structure; create all `__init__.py` placeholders |

**`.gitignore` must exclude:**
`IDEA.md`, `.env`, `*.db`, `*.sqlite`, `__pycache__/`, `*.egg-info/`, `.venv/`, `node_modules/`, `.env.local`, `*.pyc`, `.coverage`, `htmlcov/`, `*.log`, `token_cache.json`, `.pytest_cache/`, `*.egg`, `dist/` (root-level only; `frontend/dist/` is **committed**)

**Commit:** `chore: initialize project scaffold and gitignore`

---

### Wave 1 — Backend Config + DB Models + Frontend Scaffold
**All 3 run in parallel after W0.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W1-1 | pyproject.toml + .env.example + config.py | `deep` | [] | Medium | W0 |
| W1-2 | database.py + ORM models + Alembic | `deep` | [] | High | W0 |
| W1-3 | Vue 3 + Vite frontend scaffold + build dist/ | `visual-engineering` | [] | Low | W0 |

#### W1-1 Details
- `pyproject.toml`: all pinned dependencies, `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `addopts = "--cov=app --cov-report=term-missing --cov-branch"`
- `.env.example`: every config key with safe placeholder values
- `backend/app/config.py`: `pydantic-settings` `BaseSettings`, reads from `.env` file, all fields typed, `model_config = SettingsConfigDict(env_file=".env")`
- `.coveragerc`: `concurrency = greenlet, thread` (CRITICAL for async SQLAlchemy 100% coverage)

#### W1-2 Details
- `backend/app/database.py`:
  - `create_async_engine("sqlite+aiosqlite://...")` with `connect_args={"check_same_thread": False}`
  - WAL pragma via `@event.listens_for(engine.sync_engine, "connect")`
  - `async_sessionmaker(expire_on_commit=False)`
  - `get_db()` async generator dependency
  - `load_sqlite_vec()`: try-load sqlite-vec extension, log warning if unavailable, set `settings.sqlite_vec_available` flag
- All 4 ORM models with SQLAlchemy 2.0 `Mapped[]` syntax
- `alembic init -t async alembic`
- `alembic/env.py`: async pattern, `NullPool`, `include_object` hook to exclude `vec0` tables, DB URL from env, all model imports
- Migration `0001_initial_schema.py`: all ORM tables + FTS5 virtual table DDL + FTS5 triggers (insert/update/delete sync)

#### W1-3 Details
- `npm create vite@latest frontend -- --template vue-ts`
- Install: `pinia`, `vue-router@4`, `axios`, `tailwindcss`, `@vueuse/core`, `dayjs`
- Configure Vite dev proxy: `/api → http://localhost:8000`
- Run `npm run build` to produce initial `frontend/dist/` (committed)
- Basic `App.vue`, `router/index.ts`, `stores/auth.ts` stubs

**Commit:** `feat: add project configuration, database models, and frontend scaffold`

---

### Wave 2 — Core Backend Services
**All 4 run in parallel after W1-1 and W1-2.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W2-1 | Auth service | `deep` | [] | Medium | W1-1, W1-2 |
| W2-2 | AI service | `deep` | [] | High | W1-1, W1-2 |
| W2-3 | File manager | `deep` | [] | Medium | W1-1 |
| W2-4 | Search service | `deep` | [] | Medium | W1-1, W1-2 |

#### W2-1 Details (`services/auth_service.py` + `api/auth.py`)
- `verify_password(plain, hashed)` — `passlib.bcrypt`
- `create_access_token(data, expires_delta)` — `python-jose`
- `get_current_user(token)` — FastAPI `Depends`, raises HTTP 401 on invalid/expired JWT
- `api/auth.py`: `POST /api/v1/auth/login` — body `{password: str}` → `{access_token: str, token_type: "bearer"}`
- `deps.py`: `CurrentUser = Annotated[str, Depends(get_current_user)]`
- **TDD**: test correct password → 200 + token; wrong password → 401; expired token → 401; malformed token → 401

#### W2-2 Details (`services/ai_service.py`)
- `AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)`
- `instructor.from_openai(async_client)`
- `classify_email(subject: str, body: str) -> bool`: load `prompts/classify_email.txt`, call LLM, check cache first
- `extract_invoice_fields(text: str) -> InvoiceExtract`: load `prompts/extract_invoice.txt`, instructor structured extraction with `max_retries=3`
- `embed_text(text: str) -> list[float]`: `client.embeddings.create(model=settings.LLM_EMBED_MODEL)`
- `_get_cache(hash) -> str | None` / `_set_cache(hash, response)`: LLM call caching by `SHA-256(prompt_type + content)`
- **TDD**: all LLM calls mocked with `AsyncMock`; cache hit skips LLM call; `InstructorRetryException` logged gracefully; embedding returns correct-dimension vector

#### W2-3 Details (`services/file_manager.py`)
- `sanitize_filename_component(s: str) -> str`: strip/replace chars unsafe for filenames, limit length to 40 chars per component
- `canonical_filename(invoice: ParsedInvoice) -> str`: `{buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf` — all components sanitized
- `save_invoice(pdf_bytes: bytes, invoice: ParsedInvoice) -> str`: write to `settings.STORAGE_PATH / filename`, return relative path
- `stream_zip(file_paths: list[str]) -> AsyncIterator[bytes]`: streaming ZIP via `io.BytesIO`, yields chunks
- `delete_invoice_file(file_path: str)`: remove file, log if missing
- **TDD**: canonical filename deterministic for Chinese names; sanitize handles `/`, `\`, `*`, `?`, `:`, `"`, `|`, `<`, `>`, null bytes; ZIP contains all requested files; tmp_path fixture for file I/O

#### W2-4 Details (`services/search_service.py`)
- `search_fts(query: str, date_from, date_to, page, size) -> tuple[list[Invoice], int]`: FTS5 `MATCH` with date range WHERE, total count, pagination
- `search_semantic(query_embedding: list[float], limit: int) -> list[int]`: sqlite-vec KNN query → invoice IDs; if `settings.sqlite_vec_available=False`, return empty list (FTS5 handles it)
- `search(query, date_from, date_to, page, size, use_semantic) -> SearchResult`: merge FTS5 + semantic results, deduplicate, re-rank
- **TDD**: FTS5 returns results containing query terms; date filter excludes out-of-range; semantic returns empty list when vec disabled; pagination returns correct slice

**Commit:** `feat: implement auth, AI service, file manager, and search service`

---

### Wave 3 — Email Scanner + Invoice Parser
**Both run in parallel after W1-1 and W1-2.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W3-1 | Email scanner (4 implementations) | `deep` | [] | Very High | W1-1, W1-2 |
| W3-2 | Invoice parser (PDF/XML/OFD + QR) | `deep` | [] | Very High | W1-1, W2-2 |

#### W3-1 Details (`services/email_scanner.py`)

**Abstract base:**
```python
class BaseEmailScanner(ABC):
    @abstractmethod
    async def scan(self, account: EmailAccount) -> list[RawEmail]: ...
    @abstractmethod
    async def test_connection(self, account: EmailAccount) -> bool: ...
```

**Implementations:**

`ImapScanner` (covers IMAP + QQ Mail):
- `imap-tools` `MailBox` — synchronous, wrapped in `asyncio.get_event_loop().run_in_executor(executor)`
- Fetch unseen messages with UID > `account.last_scan_uid`
- Filter attachments to `.pdf`, `.ofd`, `.xml` only
- Extract URLs from body HTML/text (regex `https?://[^\s<>"]+`)
- Update `account.last_scan_uid` after scan
- QQ Mail: same `ImapScanner`, host=`imap.qq.com`, port=993, use app password

`Pop3Scanner`:
- `imap-tools` `MailBoxPop3` — same executor pattern
- POP3 has no UID — use `Message-ID` header as idempotency key
- Store seen Message-IDs in `last_scan_uid` as JSON list (max 1000 entries)

`OutlookScanner` (Microsoft Graph + MSAL):
- `msal.PublicClientApplication` device code flow
- Token cached to `account.oauth_token_path` (gitignored)
- `msgraph.GraphServiceClient` — `GET /me/mailFolders/inbox/messages?$filter=isRead eq false&$select=...`
- Silent token refresh via `acquire_token_silent()` before each scan
- Download attachments via Graph API `GET /me/messages/{id}/attachments/{attId}/$value`
- Mark processed messages as read after successful processing

`ScannerFactory`:
- `get_scanner(account_type: str) -> BaseEmailScanner`
- Returns correct implementation by type string

**TDD:** Mock `MailBox` via `unittest.mock.patch`; mock MSAL/Graph client; UID idempotency test (second scan returns empty); attachment filtering test; connection failure returns empty list, logs error

#### W3-2 Details (`services/invoice_parser.py`)

**Format detection:**
```python
def detect_format(filename: str, content: bytes) -> Literal["pdf", "xml", "ofd"]:
    # by extension first, then magic bytes
```

**PDF pipeline** (`parse_pdf`):
1. PyMuPDF render page 0 at 2× resolution → pyzbar QR decode
2. If QR found: parse `|`-delimited fields (code, number, amount, date, checksum) → `extraction_method="qr"`
3. Else: PyMuPDF `page.get_text("text")` — fast text extraction
4. If text empty: pdfplumber fallback for table extraction
5. If still no structured fields: pass text to `ai_service.extract_invoice_fields()` → `extraction_method="llm"`

**XML pipeline** (`parse_xml`):
- `lxml.etree.fromstring(content)`
- XPath for standard Chinese e-invoice XML fields (namespace-aware)
- `extraction_method="xml_xpath"`

**OFD pipeline** (`parse_ofd`):
- `base64.b64encode(content)` → `easyofd.OFD().read(b64)`
- `ofd.to_pdf()` → pipe through PDF pipeline
- `ofd.del_data()` always called (cleanup)
- `extraction_method="ofd_struct"` if fields found, else `"llm"`

**QR code format** (Chinese VAT invoice):
```
发票代码|发票号码|开票日期|不含税金额|校验码
```
Split by `|` or `,` depending on issuer — try both.

**`parse(filename, content) -> ParsedInvoice`**: top-level dispatcher

**TDD:**
- Synthetic minimal PDF fixture (1-page with known text)
- Synthetic XML fixture matching standard 增值税电子普通发票 schema
- Mock `ai_service.extract_invoice_fields` for determinism
- QR decode test with pre-rendered QR image embedded in PDF
- OFD test with `easyofd` mocked (avoid system font dependency in CI)
- Unknown format raises `ValueError` with informative message

**Commit:** `feat: implement email scanner and invoice parsing pipeline`

---

### Wave 4 — REST API Endpoints
**All 4 tasks after W2 + W3 complete. W4-1..W4-4 are parallel.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W4-1 | Invoice CRUD + search API | `deep` | [] | Medium | W2-1, W2-4 |
| W4-2 | Download API | `deep` | [] | Medium | W2-3 |
| W4-3 | Email accounts API | `quick` | [] | Low | W2-1 |
| W4-4 | main.py + scan trigger API | `quick` | [] | Low | W4-1, W4-2, W4-3 |

#### W4-1 Details (`api/invoices.py`)
- `GET /api/v1/invoices`: `query: str = ""`, `date_from: date | None`, `date_to: date | None`, `page: int = 1`, `size: int = 20` → `InvoiceListResponse(items, total, page, size)`
- `GET /api/v1/invoices/{id}` → `InvoiceResponse` (all fields, no file_path exposed)
- `DELETE /api/v1/invoices/{id}`: deletes DB record + file via `file_manager.delete_invoice_file()`
- `POST /api/v1/invoices/search`: body `{query: str}` → embed → semantic search → `InvoiceListResponse`
- All routes: `CurrentUser` dependency

#### W4-2 Details (`api/downloads.py`)
- `GET /api/v1/invoices/{id}/download`: resolve `file_path`, return `FileResponse(path, media_type="application/pdf", filename=...)`
- `POST /api/v1/invoices/batch-download`: body `{ids: list[int]}`, validate all IDs exist, `StreamingResponse(file_manager.stream_zip(paths), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=invoices.zip"})`

#### W4-3 Details (`api/email_accounts.py`)
- `GET /api/v1/accounts` → list (password fields excluded from response schema)
- `POST /api/v1/accounts`: encrypt password with Fernet before storing
- `PUT /api/v1/accounts/{id}`: partial update, password re-encrypted if provided
- `DELETE /api/v1/accounts/{id}`
- Response schema: `EmailAccountResponse` — no `password_encrypted` field

#### W4-4 Details (`app/main.py`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Run Alembic migrations programmatically
    # 2. Load sqlite-vec (graceful fallback)
    # 3. Create FTS5 virtual table if not exists
    # 4. Start APScheduler
    yield
    # 5. Shutdown scheduler
    # 6. Dispose DB engine

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, ...)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(invoice_router, prefix="/api/v1")
app.include_router(download_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")
app.include_router(scan_router, prefix="/api/v1")

# Mount built frontend assets
app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"))

# SPA catch-all (MUST be last)
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    return FileResponse(FRONTEND_DIST / "index.html")
```

`api/scan.py`:
- `POST /api/v1/scan/trigger`: manually enqueue scan job → `{"status": "triggered"}`
- `GET /api/v1/scan/logs`: paginated `ScanLog` list

**Commit:** `feat: implement REST API endpoints`

---

### Wave 5 — Scheduler Orchestration
**After W2-2, W2-3, W3-1, W3-2.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W5-1 | tasks/scheduler.py | `deep` | [] | Medium | W2-2, W2-3, W3-1, W3-2 |

#### W5-1 Details

```python
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

async def scan_all_accounts(db: AsyncSession, ai: AIService, ...):
    accounts = await get_active_email_accounts(db)
    for account in accounts:
        log = ScanLog(email_account_id=account.id, started_at=utcnow())
        try:
            scanner = ScannerFactory.get_scanner(account.type)
            emails = await scanner.scan(account)
            for email in emails:
                if not await ai.classify_email(email.subject, email.body_text):
                    continue
                for attachment in email.attachments:
                    parsed = await invoice_parser.parse(
                        attachment.filename, attachment.payload
                    )
                    if not parsed.invoice_no:
                        continue  # skip unparseable
                    # Idempotency: skip if invoice_no already in DB
                    if await invoice_exists(db, parsed.invoice_no):
                        continue
                    file_path = await file_manager.save_invoice(
                        attachment.payload, parsed
                    )
                    invoice = Invoice(
                        invoice_no=parsed.invoice_no,
                        # ... all fields
                        email_uid=email.uid,
                        email_account_id=account.id,
                    )
                    db.add(invoice)
                    await db.flush()
                    # Embed and store in sqlite-vec
                    if settings.sqlite_vec_available:
                        embedding = await ai.embed_text(parsed.raw_text)
                        await store_embedding(invoice.id, embedding)
                    # Update FTS5 index
                    await update_fts(db, invoice)
            await db.commit()
            account.last_scan_uid = scanner.last_uid
            log.invoices_found = invoices_added
        except Exception as e:
            log.error_message = str(e)
            logger.exception("Scan failed for account %s", account.id)
        finally:
            log.finished_at = utcnow()
            db.add(log)
            await db.commit()
```

Scheduler started in `main.py` lifespan:
```python
scheduler.add_job(
    scan_all_accounts,
    CronTrigger(minute=f"*/{settings.SCAN_INTERVAL_MINUTES}"),
    id="email_scan",
    replace_existing=True,
    misfire_grace_time=300,
)
scheduler.start()
```

**TDD:** Mock scanner returns 2 emails (1 invoice, 1 non-invoice); verify 1 Invoice created, 1 ScanLog written, LLM called once; second run skips already-processed invoice (idempotency); exception in scanner → error logged, other accounts continue

**Commit:** `feat: integrate APScheduler for periodic email scanning`

---

### Wave 6 — Frontend
**W6-1 first; W6-2, W6-3, W6-4 parallel after W6-1. Overlaps with Wave 4+5.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W6-1 | App shell: router, stores, axios, login | `visual-engineering` | [] | Medium | W1-3 |
| W6-2 | Invoice list view | `visual-engineering` | [] | High | W6-1 |
| W6-3 | Invoice detail/preview view | `visual-engineering` | [] | Medium | W6-1 |
| W6-4 | Settings view (email accounts) | `visual-engineering` | [] | Medium | W6-1 |

#### W6-1 Details
- `router/index.ts`: routes `/login`, `/` (redirect → `/invoices`), `/invoices`, `/invoices/:id`, `/settings` — all except `/login` require auth guard
- `stores/auth.ts`: Pinia store — `token: string | null`, `login(password)`, `logout()`, JWT stored in `localStorage`
- `api/client.ts`: axios instance with `baseURL="/api/v1"`, request interceptor adds `Authorization: Bearer {token}`, response interceptor redirects to `/login` on 401
- `views/LoginView.vue`: password field, submit handler, error display

#### W6-2 Details (`views/InvoiceListView.vue`)
- Search bar with 300ms debounce (`@vueuse/core useDebounce`)
- Date range picker (start/end, `dayjs` formatting)
- Results table columns: checkbox, invoice_no, buyer, seller, amount, date, type, item_summary, actions (preview, download)
- Multi-select: select all / deselect all
- "Download Selected (N)" button → `POST /api/v1/invoices/batch-download` → trigger browser download of ZIP
- Pagination: prev/next, page size selector (20/50/100)
- Loading skeleton state
- Empty state with helpful message

#### W6-3 Details (`views/InvoiceDetailView.vue`)
- All structured fields in labeled card grid
- PDF preview: `<iframe :src="'/api/v1/invoices/{id}/download'" />` — browser native PDF viewer
- Download button: links to download endpoint
- Breadcrumb: ← Back to list

#### W6-4 Details (`views/SettingsView.vue`)
- Email accounts table: name, type, username, status (active/inactive), last scan time
- Add/Edit modal form: type selector changes which fields appear (host/port for IMAP/POP3, OAuth note for Outlook)
- Password field: type=password, placeholder shows `••••••••` if already set, blank = keep existing
- "Test Connection" button → calls backend test endpoint → shows success/error
- "Scan Now" button → `POST /api/v1/scan/trigger` → shows toast notification
- Recent scan log table: account, start time, emails scanned, invoices found, status

**After all W6 tasks:** Run `npm run build` in `frontend/`, commit updated `dist/`

**Commit:** `feat: implement Vue 3 frontend with search, preview, and batch download`

---

### Wave 7 — Deployment Templates
**Depends on W0 only. Can run any time after W0.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|-------------|------------|--------------|
| W7-1 | deploy/invoice-maid.service | `quick` | [] | Low | W0 |
| W7-2 | deploy/invoice-maid.nginx.conf | `quick` | [] | Low | W0 |

#### W7-1 (`deploy/invoice-maid.service`)
```ini
[Unit]
Description=Invoice Maid — AI Invoice Extraction Service
After=network.target

[Service]
Type=simple
User={{USER}}
Group={{USER}}
WorkingDirectory={{INSTALL_DIR}}/backend
EnvironmentFile={{INSTALL_DIR}}/backend/.env
ExecStart={{VENV}}/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=invoice-maid

[Install]
WantedBy=multi-user.target
```
Parameterized: `{{INSTALL_DIR}}`, `{{USER}}`, `{{VENV}}`

#### W7-2 (`deploy/invoice-maid.nginx.conf`)
```nginx
server {
    listen 80;
    server_name {{DOMAIN}};
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name {{DOMAIN}};

    ssl_certificate     /etc/ssl/certs/{{DOMAIN}}.crt;
    ssl_certificate_key /etc/ssl/private/{{DOMAIN}}.key;

    client_max_body_size 50M;  # PDF uploads
    gzip on;
    gzip_types text/plain application/json application/javascript text/css;

    # API proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;  # LLM calls can be slow
    }

    # Static assets — immutable cache (Vite hashes filenames)
    location /assets/ {
        proxy_pass http://127.0.0.1:8000;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # SPA catch-all
    location / {
        proxy_pass http://127.0.0.1:8000;
        add_header Cache-Control "no-cache";
    }
}
```

**Commit:** `chore: add systemd and nginx deployment templates`

---

### Wave 8 — Tests (100% Coverage)
**Written alongside each wave (TDD); final coverage gate run here.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|------------|------------|--------------|
| W8-1 | tests/conftest.py + fixtures | `deep` | [] | High | All W1-W5 |
| W8-2 | tests/test_services/ | `deep` | [] | High | W8-1 |
| W8-3 | tests/test_api/ | `deep` | [] | Medium | W8-1 |
| W8-4 | tests/test_models/ | `quick` | [] | Low | W8-1 |
| W8-5 | Coverage gate + gap fixes | `quick` | [] | Low | W8-2, W8-3, W8-4 |

#### W8-1 Details (`tests/conftest.py`)

```python
# Session-scoped in-memory async engine
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Also create FTS5 table
        await conn.execute(text(FTS5_DDL))
    yield engine
    await engine.dispose()

# Per-test session with rollback isolation
@pytest_asyncio.fixture
async def db(engine): ...

# httpx.AsyncClient with overridden get_db
@pytest_asyncio.fixture
async def client(db): ...

# Mock AI service that returns deterministic InvoiceExtract
@pytest.fixture
def mock_ai_service(): ...

# Synthetic fixtures in tests/fixtures/
# sample_invoice.pdf: minimal 1-page PDF with known text fields
# sample_invoice.xml: standard 增值税电子普通发票 XML
# sample_invoice.ofd: minimal OFD (or use mock)
```

#### W8-2 Coverage Targets

| File | Key test cases |
|------|---------------|
| `test_auth_service.py` | correct pwd → token; wrong pwd → None; expired token → 401; malformed JWT → 401 |
| `test_ai_service.py` | classify_email returns True/False; extract returns InvoiceExtract; cache hit skips LLM; InstructorRetryException handled |
| `test_email_scanner.py` | IMAP scan returns emails; UID idempotency (2nd scan empty); attachment filtered to pdf/ofd/xml only; connection failure logged |
| `test_invoice_parser.py` | PDF with QR → qr extraction method; PDF text only → llm method; XML → xml_xpath; OFD → mocked easyofd; unknown format → ValueError |
| `test_file_manager.py` | canonical filename for Chinese names; sanitize removes illegal chars; ZIP contains all files; delete missing file logs warning |
| `test_search_service.py` | FTS5 returns results; date filter; semantic empty when vec disabled; pagination |

#### W8-3 Coverage Targets

| File | Key test cases |
|------|---------------|
| `test_auth.py` | POST /auth/login correct → 200 token; wrong → 401 |
| `test_invoices.py` | GET /invoices with query/dates/pagination; GET /invoices/:id; DELETE /invoices/:id; unauthenticated → 401 |
| `test_downloads.py` | Single download 200 FileResponse; batch ZIP StreamingResponse; missing ID → 404 |
| `test_email_accounts.py` | CRUD; password not in GET response |

#### W8-5 Coverage Gate

```bash
cd backend && pytest --cov=app --cov-report=term-missing --cov-fail-under=100
```

**`.coveragerc`** (non-negotiable):
```ini
[run]
source = app
branch = True
concurrency = greenlet, thread

[report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.:
    raise NotImplementedError
    \.\.\.
```

**Commit:** `test: achieve 100% unit test coverage`

---

### Wave 9 — Documentation
**Parallel with Wave 8.**

| ID | Task | Category | load_skills | Complexity | Dependencies |
|----|------|----------|------------|------------|--------------|
| W9-1 | README.md | `writing` | [] | Medium | All waves |
| W9-2 | CHANGELOG.md | `writing` | [] | Low | All waves |

#### W9-1 README.md Sections
1. Project description + screenshot placeholder
2. Features list (all implemented capabilities)
3. Architecture overview (text diagram)
4. Prerequisites (Python 3.11+, system packages: `libzbar0`, `fonts-noto-cjk`)
5. Installation
   - Clone repo
   - Create virtualenv, `pip install -e ".[dev]"`
   - Copy `.env.example` → `.env`, fill in values
   - Run migrations: `alembic upgrade head`
   - Start: `uvicorn app.main:app --reload`
6. Configuration reference (table: every `.env` key, description, example value, required/optional)
7. Running development (backend + frontend dev server)
8. Deployment (systemd + nginx walkthrough with parameter substitution)
9. Email account setup guide (IMAP, QQ Mail app password, Outlook device code flow)
10. Development: running tests, coverage report

#### W9-2 CHANGELOG.md
Keep-a-Changelog 1.0.0 format, v0.1.0 entry with full feature list.

**Commit:** `docs: add README.md and CHANGELOG.md`

---

### Wave 10 — Final Push

**Pre-push checklist:**
- [ ] `git status` — no `.env`, `IDEA.md`, `*.db`, `token_cache.json` tracked
- [ ] `pytest --cov=app --cov-fail-under=100` passes
- [ ] `frontend/dist/` is committed and up-to-date
- [ ] All 10 commits in `git log --oneline`
- [ ] `alembic upgrade head` runs clean on fresh DB

```bash
git push -u origin main
```

---

## Atomic Commit Strategy

| # | Commit Message | Wave | Contents |
|---|---------------|------|----------|
| 1 | `chore: initialize project scaffold and gitignore` | W0 | `.gitignore`, directory skeleton, `AGENTS.md` update |
| 2 | `feat: add project configuration, database models, and frontend scaffold` | W1 | `pyproject.toml`, `.env.example`, `config.py`, `.coveragerc`, `database.py`, all ORM models, `alembic/`, migration `0001`, Vue scaffold, built `dist/` |
| 3 | `feat: implement auth, AI service, file manager, and search service` | W2 | `auth_service.py`, `ai_service.py`, `file_manager.py`, `search_service.py`, prompt templates, `deps.py` |
| 4 | `feat: implement email scanner and invoice parsing pipeline` | W3 | `email_scanner.py`, `invoice_parser.py` |
| 5 | `feat: implement REST API endpoints` | W4 | All `api/` routers, `main.py` |
| 6 | `feat: integrate APScheduler for periodic email scanning` | W5 | `tasks/scheduler.py` |
| 7 | `feat: implement Vue 3 frontend with search, preview, and batch download` | W6 | All `frontend/src/`, rebuilt `frontend/dist/` |
| 8 | `chore: add systemd and nginx deployment templates` | W7 | `deploy/` |
| 9 | `test: achieve 100% unit test coverage` | W8 | All `tests/`, `conftest.py`, fixture files |
| 10 | `docs: add README.md and CHANGELOG.md` | W9+W10 | `README.md`, `CHANGELOG.md`, final push |

Each commit:
- Passes all existing tests
- Contains no secrets, credentials, or `IDEA.md`
- Is self-contained and reviewable
- Has a conventional commit message prefix

---

## Parallel Execution Map

```
W0-1, W0-2                                        [blocking]
        │
        ├─── W1-1 ─────────────────────────────┐
        ├─── W1-2 ─────────────────────────┐   │
        └─── W1-3 ──────────────────────┐  │   │
                                        │  │   │
                        (W1 complete)   │  │   │
                              │         │  │   │
              ┌───────────────┤         │  │   │
     W2-1 W2-2 W2-3 W2-4   W3-1 W3-2  W6-1 W7-1 W7-2
         └──────────────────────────────┘
                      │
              (W2+W3 complete)
              │               │
     W4-1 W4-2 W4-3 W4-4    W5-1    W6-2 W6-3 W6-4
              │               │         │
              └───────────────┴─────────┘
                              │
                  (all W4+W5+W6 complete)
                              │
                      W8-1 ───────────────
                      W8-2 W8-3 W8-4     │
                            │            │
                           W8-5         W9-1 W9-2
                            │            │
                            └────────────┘
                                   │
                                 W10-1 (git push)
```

---

## Critical Implementation Notes

1. **`expire_on_commit=False`** — MUST be set on `async_sessionmaker`. Without it, accessing ORM attributes after `commit()` triggers implicit async I/O that crashes.

2. **WAL mode pragma** — Add via SQLAlchemy sync event listener at `"connect"` event, not as a migration. Needed for concurrent reads during web requests while scheduler writes.

3. **sqlite-vec outside SQLAlchemy** — Maintain a separate sync `sqlite3` connection for vec operations. Exclude all `vec_*` tables from Alembic autogenerate via `include_object` hook.

4. **imap-tools is sync** — All `MailBox` calls must be wrapped in `loop.run_in_executor(executor, ...)`. Never call synchronously from an async APScheduler job.

5. **`concurrency = greenlet, thread` in `.coveragerc`** — Without this, async SQLAlchemy lines will never reach 100% coverage regardless of test completeness. Non-negotiable.

6. **Fernet for password encryption** — Email account passwords stored Fernet-encrypted in DB. Encryption key derived from `JWT_SECRET` (or a separate `ENCRYPTION_KEY` env var). Never store plaintext passwords.

7. **`easyofd.del_data()` always called** — In `finally` block. Forgetting causes temp directory accumulation. Use context manager wrapper.

8. **Frontend dist/ committed** — `frontend/dist/` is NOT in `.gitignore`. The root `.gitignore` has `dist/` but scoped to exclude `frontend/dist/`. Verify gitignore scoping rules carefully.

9. **APScheduler pin** — `apscheduler>=3.10,<4.0`. Version 4.x is a full API rewrite. Do not upgrade past 3.x.

10. **SPA catch-all ordering** — In `main.py`, API routers and `/assets` mount must be registered BEFORE the `/{full_path:path}` catch-all. Wrong order silently swallows API routes.
```
