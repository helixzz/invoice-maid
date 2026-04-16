# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-16T15:05:53Z

## OVERVIEW

Invoice Maid — AI-powered invoice extraction service. Scans email inboxes, identifies invoices via LLM, downloads/parses/structures invoice data, serves a web UI for search and batch download. Python backend + web frontend. Single-user. Pre-development stage.

## STATUS

**Planning phase.** Only `IDEA.md` exists (Chinese-language spec, excluded from git). No source code, no configs, no tests yet.

## ARCHITECTURE (from IDEA.md)

- **Backend**: Python service — local DB, scheduled email scanning, AI integration (OpenAI-compatible API)
- **Frontend**: Modern web UI — invoice search, preview, batch ZIP download
- **Email**: POP3, IMAP, Microsoft Outlook (OAuth), QQ Mail
- **Invoices**: Chinese VAT invoices (PDF, XML) — both as email attachments and download links in email body
- **AI**: LLM for email classification, invoice field extraction, project name summarization, semantic search

## STRUCTURED FIELDS (invoice)

| Field | Notes |
|-------|-------|
| Buyer name | 购买方名称 |
| Seller name | 销售方名称 |
| Total amount | 价税合计 (numeric) |
| Item description | AI-summarized to one line |
| Invoice type | e.g. 电子普通发票, 数电专票 |
| Invoice number | 发票号码 |
| Invoice date | Parsed to date format for filtering |

## CONVENTIONS (HARD RULES from IDEA.md)

- **Language**: Python, modern/clean stack
- **Git**: All code pushed must pass 100% unit test coverage
- **Sensitive data**: MUST be stripped or excluded before any git push
- **IDEA.md**: NEVER push to remote — it's a private draft between requester and AI
- **Docs**: README.md and CHANGELOG.md MUST be updated when features ship
- **Deployment**: MUST provide systemd service template + nginx config template
- **File naming**: Invoices saved as `{buyer}_{seller}_{invoice_no}_{date}_{amount}.pdf`

## ANTI-PATTERNS

- Pushing credentials, API keys, or sensitive config to git
- Shipping features without updating README.md + CHANGELOG.md
- Pushing code without 100% test coverage
- Including IDEA.md in git commits

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Requirements / spec | `IDEA.md` | Chinese; private, never push |
| Project knowledge | `AGENTS.md` | This file |

## COMMANDS

```bash
# None yet — project is pre-development
```

## NOTES

- Single-user design — no multi-tenant, no data isolation needed (for now)
- Invoice download links in emails require the service to follow URLs and download files
- AI models don't need to be top-tier — cost-effective, fast-response models suffice
- Consider additional UX improvements beyond spec (IDEA.md line 70 encourages this)
