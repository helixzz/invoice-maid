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
- Parse **PDF**, **XML**, and **OFD (数电票)** invoice formats
- Search and review invoices from a modern web UI
- Download one invoice or export many as a ZIP

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
- [Deployment](#deployment)
- [Development](#development)
- [License](#license)

---

## Features

### Email ingestion
- **Automated Email Scanning**: Supports IMAP, POP3, QQ Mail (via auth code), and Microsoft Outlook (via OAuth2 device code flow).
- **Scheduled Processing**: Configurable periodic scanning intervals with APScheduler.

### Invoice intelligence
- **AI Classification & Extraction**: Uses OpenAI-compatible LLMs to classify emails and extract structured data from invoices.
- **Multi-format Support**: Handles PDF, XML, and OFD (数电票) invoice formats.
- **QR Code Decoding**: Decodes QR codes from Chinese VAT invoices for accurate field mapping.
- **Structured Data**: Extracts buyer, seller, total amount, date, invoice type, and AI-summarized item descriptions.

### Search & UI
- **Advanced Search**: Full-text search via SQLite FTS5 and optional semantic search using vector embeddings.
- **Web UI**: Modern dashboard for invoice searching, date filtering, PDF previews, and batch ZIP downloads.
- **Secure Access**: Single-user admin authentication with JWT.
- **Operational Guardrails**: Login rate limiting and a rich health endpoint for runtime visibility.

---

## Screenshots

### Login

A clean split-screen login page with product features on the left and a simple sign-in form on the right.

<p align="center">
  <img src="assets/screenshots/01-login.png" alt="Login page" width="900">
</p>

### Invoice Dashboard

After login, the main invoices page shows summary stats at the top, a search bar with date filters, and the invoice table with bulk actions. Select invoices, download them as a ZIP, or open individual details.

<p align="center">
  <img src="assets/screenshots/02-invoices.png" alt="Invoice dashboard" width="900">
</p>

### Invoice Detail

Click any invoice to see its full structured data: buyer, seller, amount, date, type, confidence score, and a PDF preview. Non-PDF formats show a download prompt instead.

<p align="center">
  <img src="assets/screenshots/03-invoice-detail.png" alt="Invoice detail" width="900">
</p>

### Email Account Settings

Configure email accounts for automatic scanning. Each account shows its protocol, scan interval, and last scan time. Add, edit, or delete accounts from the same page.

<p align="center">
  <img src="assets/screenshots/04-settings.png" alt="Email account settings" width="900">
</p>

### Scan Operations

Trigger a full scan manually, or review recent scan logs with per-account status, email counts, and invoice yield.

<p align="center">
  <img src="assets/screenshots/05-scan-operations.png" alt="Scan operations" width="900">
</p>

---

## Tech Stack

| Layer | Stack |
|------|-------|
| Backend | FastAPI, SQLAlchemy, APScheduler |
| Frontend | Vue 3, Vite, Tailwind CSS, Pinia |
| Database | SQLite, FTS5, sqlite-vec |
| AI | OpenAI-compatible API, Instructor |
| Parsing | pdfplumber, PyMuPDF, easyofd, lxml |

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development only)
- System packages:
  - `libzbar0` — required for QR code decoding
  - `fonts-noto-cjk` — optional, recommended for proper Chinese rendering

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
# Edit .env with your settings
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

Then open `http://localhost:8000`.

---

## Configuration Reference

| Key | Description | Example | Required |
|-----|-------------|---------|----------|
| `DATABASE_URL` | SQLAlchemy database connection string | `sqlite+aiosqlite:///./data/invoices.db` | Yes |
| `ADMIN_PASSWORD_HASH` | Bcrypt hash of the admin password | `$2b$12$...` | Yes |
| `JWT_SECRET` | Secret key for signing JWT tokens | `32-char-random-string` | Yes |
| `LLM_BASE_URL` | Base URL for OpenAI-compatible API | `https://api.openai.com/v1` | Yes |
| `LLM_API_KEY` | API key for the LLM service | `sk-...` | Yes |
| `STORAGE_PATH` | Directory to store downloaded invoices | `./data/invoices` | No |
| `JWT_EXPIRE_MINUTES` | Token expiration time in minutes | `1440` | No |
| `LLM_MODEL` | Model ID for classification and extraction | `gpt-4o-mini` | No |
| `LLM_EMBED_MODEL` | Model ID for generating embeddings | `text-embedding-3-small` | No |
| `EMBED_DIM` | Vector dimensions for embeddings | `1536` | No |
| `SCAN_INTERVAL_MINUTES` | Minutes between email scans | `60` | No |
| `SQLITE_VEC_ENABLED` | Enable or disable semantic search | `true` | No |

---

## Email Account Setup

### IMAP / POP3
Use your provider’s server address, port, username, and password/app password.

### QQ Mail
1. Log in to QQ Mail web interface
2. Open **Settings > Account**
3. Enable **POP3/IMAP Service**
4. Generate a **16-character Authorization Code** and use it as the password

### Microsoft Outlook
Invoice Maid uses **OAuth2 Device Code Flow**. On first scan, check the application logs for the device code and verification URL (for example `https://microsoft.com/devicelogin`), then complete authorization in a browser.

---

## Deployment

> **Warning**
> Run the backend with `--workers 1`. APScheduler runs in-process, and multiple workers will duplicate scheduled jobs.

### LAN tryout
To test Invoice Maid from another device on your local network, bind Uvicorn to all interfaces:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Then open `http://<your-lan-ip>:8000` from another machine on the same network.

Notes:
- Make sure your firewall allows inbound traffic on port `8000`.
- Keep `--workers 1` to avoid duplicate scheduler jobs.
- If you are testing without a reverse proxy, only do this on a trusted LAN.

### systemd
Copy `deploy/invoice-maid.service` to `/etc/systemd/system/`, update the placeholders (`{{USER}}`, `{{INSTALL_DIR}}`, `{{VENV}}`), then run:

```bash
systemctl daemon-reload
systemctl enable invoice-maid
systemctl start invoice-maid
```

### Nginx
Use `deploy/invoice-maid.nginx.conf` as your site template. Replace `{{DOMAIN}}` with your real domain and update SSL certificate paths.

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

---

## License

MIT
