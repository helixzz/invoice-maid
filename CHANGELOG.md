# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.2.0] - 2026-04-17

### Added
- Login rate limiting at 10 requests per minute per IP with `Retry-After` support on 429 responses
- Rich health reporting for database status, scheduler state, sqlite-vec availability, invoice totals, and last scan time

## [0.1.0] - 2026-04-17

### Added
- Email inbox scanning: IMAP, POP3, QQ Mail (app password), Microsoft Outlook (OAuth device code flow)
- AI-powered email classification using OpenAI-compatible LLM
- Invoice parsing for PDF, XML, and OFD formats
- QR code extraction for Chinese VAT invoice fields
- Structured field extraction: buyer, seller, amount, date, type, item description
- LLM-based field extraction with instructor for structured output
- Full-text search via SQLite FTS5
- Optional semantic search via sqlite-vec embeddings
- Web UI: invoice search with date filtering, PDF preview, single and batch ZIP download
- Single-user JWT authentication
- Scheduled periodic email scanning via APScheduler
- Systemd service and nginx reverse proxy templates
- LLM response caching by content hash
- Canonical invoice file naming: {buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf
