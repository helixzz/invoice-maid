# Invoice Maid

AI-powered invoice extraction service. Invoice Maid automatically scans your email inboxes, identifies invoices using LLMs, downloads and parses them, and provides a clean web interface for searching and managing your invoice data.

## Features

- **Automated Email Scanning**: Supports IMAP, POP3, QQ Mail (via auth code), and Microsoft Outlook (via OAuth2 device code flow).
- **AI Classification & Extraction**: Uses OpenAI-compatible LLMs to classify emails and extract structured data from invoices.
- **Multi-format Support**: Handles PDF, XML, and OFD (数电票) invoice formats.
- **QR Code Decoding**: Decodes QR codes from Chinese VAT invoices for accurate field mapping.
- **Structured Data**: Extracts buyer, seller, total amount, date, invoice type, and AI-summarized item descriptions.
- **Advanced Search**: Full-text search via SQLite FTS5 and optional semantic search using vector embeddings.
- **Web UI**: Modern dashboard for invoice searching, date filtering, PDF previews, and batch ZIP downloads.
- **Scheduled Processing**: Configurable periodic scanning intervals.
- **Secure Access**: Single-user admin authentication with JWT.

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy (SQLite), APScheduler, OpenAI-compatible LLM (via Instructor).
- **Frontend**: Vue 3, Vite.
- **Search**: SQLite FTS5, sqlite-vec for embeddings.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend development)
- **System Packages**:
  - `libzbar0`: Required for QR code decoding.
  - `fonts-noto-cjk`: Optional, recommended for proper Chinese character rendering in previews.

## Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/helixzz/invoice-maid.git
   cd invoice-maid/backend
   ```

2. **Set up Python environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. **Configure the application**:
   ```bash
   cp .env.example .env
   # Edit .env with your settings (see Configuration Reference)
   ```

4. **Generate admin password hash**:
   ```bash
   python -c "from passlib.hash import bcrypt; print(bcrypt.hash('your-password'))"
   ```
   Paste the resulting hash into `ADMIN_PASSWORD_HASH` in your `.env`.

5. **Run the server**:
   ```bash
   uvicorn app.main:app --reload
   ```

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
| `SQLITE_VEC_ENABLED` | Enable/disable semantic search | `true` | No |

## Email Account Setup

### IMAP / POP3
Standard configuration using your email provider's server address and credentials.

### QQ Mail
1. Login to QQ Mail web interface.
2. Go to **Settings > Account**.
3. Enable **POP3/IMAP Service**.
4. Generate an **Authorization Code** (16 characters) to use as your password.

### Microsoft Outlook
Uses OAuth2 Device Code Flow. On the first scan, check the application logs for a device code and a URL (e.g., `https://microsoft.com/devicelogin`). Open the link in a browser, enter the code, and authorize the application.

## Deployment

### Production Requirements
- **Workers**: You MUST run with `--workers 1` because APScheduler runs in-process. Multiple workers will cause duplicate job executions.

### Systemd
Copy `deploy/invoice-maid.service` to `/etc/systemd/system/`, update the placeholders (`{{USER}}`, `{{INSTALL_DIR}}`, `{{VENV}}`), then:
```bash
systemctl daemon-reload
systemctl enable invoice-maid
systemctl start invoice-maid
```

### Nginx
Use `deploy/invoice-maid.nginx.conf` as a template for your site configuration. Replace `{{DOMAIN}}` with your actual domain and ensure SSL certificates are correctly referenced.

## Development

### Backend
```bash
cd backend
pip install -e ".[dev]"
pytest --cov=app --cov-report=term-missing
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## License

MIT
