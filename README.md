# Invoice Maid

<p align="center">
  <img src="assets/images/invoice-maid-icon.png" alt="Invoice Maid icon" width="120">
</p>

<p align="center">
  <img src="assets/images/invoice-maid.png" alt="Invoice Maid" width="900">
</p>

<p align="center">
  <strong>AI-powered invoice extraction for self-hosted workflows.</strong><br>
  Scan inboxes, detect invoices, parse PDF/XML/OFD files, and manage everything from one clean web UI.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.4.4-blue" alt="v0.4.4">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue-3-42B883?logo=vue.js&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/SQLite-FTS5%20%2B%20sqlite--vec-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/Coverage-100%25-brightgreen" alt="100% coverage">
</p>

---

## Why Invoice Maid?

Invoice Maid automates the boring part of invoice handling:

- Pull invoice emails from **IMAP**, **POP3**, **QQ Mail**, and **Microsoft Outlook**
- Use an **OpenAI-compatible LLM** to classify emails and extract invoice fields
- Parse **PDF**, **XML**, and **OFD (数电票)** invoice formats — including download links in email body
- Search, review, correct, and export invoices from a modern web UI
- Download one invoice, batch-export as ZIP, or export filtered lists as CSV

It is designed for **single-user**, **self-hosted** deployment with minimal operational overhead.

---

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [Email Account Setup](#email-account-setup)
- [Webhooks](#webhooks)
- [Deployment](#deployment)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

### Email ingestion
- **Automated Email Scanning** — IMAP, POP3, QQ Mail (via auth code), Microsoft Outlook (OAuth2 device code flow)
- **Scheduled Processing** — configurable scan intervals via APScheduler
- **Tiered Email Classification** — free heuristics first, cheap metadata second, LLM fallback only for ambiguous cases
- **Body Link Downloads** — follows download links in email body to retrieve invoices
- **Extraction Audit Log** — per-email tracking of why each message was saved, skipped, or failed

### Invoice intelligence
- **AI Classification & Extraction** — OpenAI-compatible LLMs classify emails and extract structured invoice data
- **Multi-format Parsing** — PDF, XML, and OFD (数电票) with QR code decoding
- **Manual Correction** — edit any extracted field inline with a full audit trail
- **Confidence Scoring** — extraction confidence and method displayed per invoice
- **Duplicate Detection** — composite dedup on invoice number + email UID + attachment filename

### Search, export & analytics
- **Full-text Search** — SQLite FTS5 with optional sqlite-vec semantic search
- **Saved Views** — persist named filter combinations for quick daily access
- **CSV Export** — export filtered invoice lists with one click
- **Spend Analytics** — monthly spend, top sellers, invoice counts by type and extraction method
- **Similar Invoices** — "more like this" discovery via embeddings or FTS5 fallback
- **Batch Actions** — select multiple invoices for ZIP download or bulk deletion

### Operations & security
- **AI Model Settings UI** — manage LLM provider, API key, model selection, and embedding config from the browser
- **Rate Limiting** — brute-force protection (10 req/min/IP) on login
- **Rich Health Endpoint** — reports DB, scheduler, sqlite-vec, invoice count, and last scan time
- **Outbound Webhooks** — `invoice.created` events with HMAC-SHA256 signed payloads
- **Project Branding** — favicon, login icon, and nav bar logo

---

## Screenshots

### Login

<p align="center">
  <img src="assets/screenshots/01-login.png" alt="Login page" width="900">
</p>

### Invoice Dashboard

Summary stats, search with date filters, invoice table with batch actions, saved views, and CSV export.

<p align="center">
  <img src="assets/screenshots/02-invoices.png" alt="Invoice dashboard" width="900">
</p>

### Invoice Detail

Full structured data with inline editing, confidence badge, extraction method, and PDF preview.

<p align="center">
  <img src="assets/screenshots/03-invoice-detail.png" alt="Invoice detail" width="900">
</p>

### Email Account Settings

Add, edit, test, and manage email accounts for automatic scanning.

<p align="center">
  <img src="assets/screenshots/04-settings.png" alt="Email account settings" width="900">
</p>

### Scan Operations

Manual scan trigger, scan history with per-email extraction audit logs.

<p align="center">
  <img src="assets/screenshots/05-scan-operations.png" alt="Scan operations" width="900">
</p>

### AI Model Configuration

Configure your LLM provider, API key, and model selection — all from the browser. Retrieve available models directly from your provider's API.

<p align="center">
  <img src="assets/screenshots/06-ai-settings.png" alt="AI model settings" width="900">
</p>

---

## Tech Stack

| Layer | Stack |
|------|-------|
| Backend | FastAPI, SQLAlchemy 2.0, APScheduler, slowapi |
| Frontend | Vue 3, Vite, Tailwind CSS, Pinia |
| Database | SQLite (WAL), FTS5, sqlite-vec |
| AI | OpenAI-compatible API, Instructor |
| Parsing | pdfplumber, PyMuPDF, easyofd, lxml, pyzbar |

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development only — pre-built dist/ included)
- System packages:
  - `libzbar0` — QR code decoding
  - `fonts-noto-cjk` — optional, recommended for Chinese rendering

---

## Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/helixzz/invoice-maid.git
cd invoice-maid/backend
```

### 2. Set up Python
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Configure the app
```bash
cp .env.example .env
# Edit .env — at minimum set the 5 required keys below
```

### 4. Generate the admin password hash
```bash
python -c "from passlib.hash import bcrypt; print(bcrypt.hash('your-password'))"
```

Paste the generated hash into `ADMIN_PASSWORD_HASH` in `.env`.

### 5. Run the server
```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000`, log in, then configure your email accounts and AI model from the Settings page.

### Docker Quick Start
```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your settings
docker compose up -d
# Visit http://localhost:8000
```

For backend hot-reload, copy `docker-compose.override.yml.example` to `docker-compose.override.yml` and use `docker compose up`. Frontend source changes still need `cd frontend && npm run build` or a separate `npm run dev` workflow.

---

## Configuration Reference

| Key | Description | Example | Required |
|-----|-------------|---------|----------|
| `DATABASE_URL` | SQLAlchemy async connection string | `sqlite+aiosqlite:///./data/invoices.db` | Yes |
| `ADMIN_PASSWORD_HASH` | Bcrypt hash of the admin password | `$2b$12$...` | Yes |
| `JWT_SECRET` | Secret for signing JWT tokens | `32-char-random-string` | Yes |
| `LLM_BASE_URL` | Base URL for OpenAI-compatible API | `https://api.openai.com/v1` | Yes |
| `LLM_API_KEY` | API key for the LLM service | `sk-...` | Yes |
| `STORAGE_PATH` | Invoice file storage directory | `./data/invoices` | No |
| `JWT_EXPIRE_MINUTES` | Token expiration (minutes) | `1440` | No |
| `LLM_MODEL` | Model for classification/extraction | `gpt-4o-mini` | No |
| `LLM_EMBED_MODEL` | Model for embeddings | `text-embedding-3-small` | No |
| `EMBED_DIM` | Embedding vector dimensions | `1536` | No |
| `SCAN_INTERVAL_MINUTES` | Minutes between scans | `60` | No |
| `SQLITE_VEC_ENABLED` | Enable semantic search | `true` | No |
| `WEBHOOK_URL` | Outbound webhook endpoint | `https://example.com/hook` | No |
| `WEBHOOK_SECRET` | HMAC-SHA256 signing key for webhooks | `your-secret` | No |
| `LOG_LEVEL` | App log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | `INFO` | No |
| `OUTLOOK_PERSONAL_CLIENT_ID` | Microsoft public client ID for personal Outlook/Live/Hotmail accounts | `04b07795-8ddb-461a-bbee-02f9e1bf7b46` | No |
| `OUTLOOK_AAD_CLIENT_ID` | Microsoft public client ID for work/school Azure AD accounts | `d3590ed6-52b3-4102-aeff-aad2292ab01c` | No |

AI model settings can also be managed from the **Settings > AI 模型** page in the web UI. Database-stored values override `.env` defaults.

---

## Email Account Setup

### IMAP / POP3
Use your provider's server address, port, username, and password/app password.

### QQ Mail
1. Log in to QQ Mail web interface → **Settings > Account**
2. Enable **POP3/IMAP Service**
3. Generate a **16-character Authorization Code** and use it as the password

### Microsoft Outlook
Invoice Maid uses **OAuth2 Device Code Flow** via the Settings page. For Outlook accounts, set **username** to the mailbox email address. Invoice Maid automatically detects personal Microsoft domains like `@outlook.com` and `@hotmail.com`, stores the account type, and uses the matching Microsoft authority/client ID for personal vs work/school accounts. Authentication is initiated explicitly and scans/test-connection only use cached tokens.

---

## Webhooks

When `WEBHOOK_URL` is configured, Invoice Maid sends a `POST` request for each new invoice:

```json
{
  "event": "invoice.created",
  "invoice_no": "12345678",
  "buyer": "Buyer Corp",
  "seller": "Seller Inc",
  "amount": "1234.56",
  "invoice_date": "2026-04-17",
  "invoice_type": "增值税电子普通发票",
  "confidence": 0.92
}
```

The `X-Signature-256` header contains an HMAC-SHA256 signature of the JSON body using `WEBHOOK_SECRET`, following the GitHub webhook signature format. Delivery failures are logged and never block the scan pipeline.

---

## Deployment

> **Warning**
> Run the backend with `--workers 1`. APScheduler runs in-process, and multiple workers will duplicate scheduled jobs.

### LAN tryout
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open `http://<your-lan-ip>:8000` from another device. Only do this on a trusted LAN without a reverse proxy.

### systemd
Copy `deploy/invoice-maid.service` to `/etc/systemd/system/`, update placeholders, then:

```bash
systemctl daemon-reload
systemctl enable --now invoice-maid
```

### Nginx
Use `deploy/invoice-maid.nginx.conf` as your site template. Replace `{{DOMAIN}}` and SSL paths.

---

## Development

### Backend
```bash
cd backend
pip install -e ".[dev]"
pytest --cov=app --cov-report=term-missing --cov-fail-under=100
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### E2E smoke tests
```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features and version history.

---

## License

[Apache License 2.0](LICENSE)
