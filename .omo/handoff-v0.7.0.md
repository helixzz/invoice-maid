> **SUPERSEDED by `.sisyphus/handoff-v0.8.1.md` (2026-04-19).** This file is kept as historical reference for what v0.7.0 looked like. Read the newer handoff first.

HANDOFF CONTEXT
===============

USER REQUESTS (AS-IS)
---------------------
- "Please continue. Let's not save LLM usage that much. Use it to enpower the service."
- "Btw, almost none of parsed invoices are real tax invoices. the accuracy is terrible. Please find ways to improve recognition accuracy after solving the robust email fetching problem."
- "keep iterate, validate, improve and polish the website and backend service until I said stop or you're pretty satisfied that the website is mature enough to provide above 95% accuracy for extracting all tax invoices from configured mailboxes"
- "Also, I think there are more invoices in my mailboxes than you discovered. Feel free to scan the whole mailbox (not just 500 emails, 1M emails should be OK) and pass more possibilities to LLM to determine (I think there should be hundreds of invoices at least)."
- "Btw, the LLM config panel has some unexpected design. the test connection button should trigger model query to selected models" (DONE in v0.7.0)
- "Also, is it possible to introduce some concurrency to speed up the process?" (DONE in v0.6.0)
- "the start & end time on the recent scans panel seems off" (DONE in v0.6.2)

GOAL
----
Improve invoice recognition accuracy from current state (7 saved invoices, all with buyer/seller/type=未知) to 95%+ by using LLM enrichment more aggressively — always call LLM for saved invoices that have missing fields, tune the prompt to reduce false negatives, and increase scan coverage.

WORK COMPLETED
--------------
- I shipped v0.5.6 through v0.7.0 in this session
- v0.5.6: LLM-first email analysis pipeline (EmailAnalysis schema, smart prompt, SafeLink resolution)
- v0.5.7: Fixed invoice count bug in scan logs, CHANGELOG ordering, release workflow changelog extraction, backfilled all GitHub releases
- v0.6.0: Concurrent email processing (50 workers via asyncio.Semaphore), removed scanner pagination caps (500 first-scan, unlimited incremental), thread-safe progress tracking, progress bar 100% fix
- v0.6.1: Fixed IMAP AND() crash on incremental scans, progress emails_processed reset per-account, added Full Rescan button
- v0.6.2: Fixed scan log timestamp inconsistency (naive vs timezone-aware datetimes)
- v0.6.3: Bumped concurrency from 5 to 50, fixed overall_pct formula
- v0.7.0: VAT invoice recognition accuracy overhaul:
  - VALID_INVOICE_TYPES whitelist (frozenset in schemas/invoice.py)
  - InvoiceExtract.is_valid_tax_invoice field for LLM explicit rejection
  - Rewritten extract_invoice.txt prompt with strict validation + rejection logic
  - VAT whitelist gate in scheduler with not_vat_invoice outcome
  - Confidence threshold raised from 0.5 to 0.6
  - Amount sentinel guard (< 0.10 = parse failure)
  - dedupe_chars for doubled PDF characters
  - CID artifact detection with PyMuPDF fallback
  - QR validation (parts[0]=="01", valid STA type codes, correct field order)
  - XML tag expansion (14 new element names for 航信/百望/数电票)
  - GBK decode fallback for legacy XML
  - _vat_confidence() weighted scoring function
  - _is_vat_document() heuristic for LLM-unavailable scenarios
  - AI Settings test connection now tests actual chat completion
  - 314 tests, 100% coverage

CURRENT STATE
-------------
- Production running v0.7.0 at https://invoice-maid.helixzz.com (port 8001)
- 314 tests, 100% coverage
- Full rescan completed: 974 emails (474 Outlook + 500 QQ), 7 invoices saved, 55 not_vat_invoice rejected
- All 7 saved invoices have buyer=未知, seller=未知, invoice_type=未知
- The 7 saved invoices have valid 20-digit 数电票 numbers and amounts, extracted via regex
- LLM enrichment does NOT trigger because confidence is already 0.7 (above 0.6 threshold)
- One false negative: LLM rejected a document with type "电子普通发票" (valid type)
- Admin password: Zz112996
- JWT token (for API calls): eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsImV4cCI6MTc3NjUzMjA0NX0.Yi6sFv3l6LgQzpM0yX-0lvfexKX5sKHASZ-kAd3euew

PENDING TASKS
-------------
1. CRITICAL: Remove FIRST_SCAN_LIMIT=500 cap entirely. The user said "1M emails should be OK, scan the whole mailbox". Set FIRST_SCAN_LIMIT to None (unlimited) for ALL scanners (IMAP, POP3, Outlook). The user expects hundreds of invoices and wants full mailbox coverage.
2. CRITICAL: Always call LLM enrichment for saved invoices that have missing fields (buyer=未知 OR seller=未知 OR invoice_type=未知), regardless of confidence level. Currently the LLM enrichment only triggers at confidence < 0.6. The user explicitly said "let's not save LLM usage that much — use it to empower the service." Change the logic so LLM enrichment runs for ALL invoices that pass the VAT gate, not just low-confidence ones.
3. HIGH: Tune LLM prompt to reduce false negatives — the "电子普通发票" rejection was wrong. The prompt may be too strict on requiring ALL 5 criteria simultaneously.
4. MEDIUM: The `skipped_seen` count (82) means rescans skip previously-seen attachments even if they failed before. May need to allow retry of failed extractions.
5. LOW: Clean up old scan log entries (many error rows from v0.5.x)

KEY FILES
---------
- backend/app/tasks/scheduler.py - Main email processing pipeline, VAT gate, LLM enrichment trigger (line ~324-334 is the enrichment block)
- backend/app/services/invoice_parser.py - PDF/XML/OFD parsing, _vat_confidence(), _is_vat_document()
- backend/app/prompts/extract_invoice.txt - LLM extraction prompt with VAT validation
- backend/app/prompts/classify_email.txt - LLM email classification prompt
- backend/app/schemas/invoice.py - VALID_INVOICE_TYPES, InvoiceExtract, EmailAnalysis
- backend/app/services/ai_service.py - analyze_email(), extract_invoice_fields(), _resolve_safelink()
- backend/app/services/email_scanner.py - IMAP/POP3/Outlook scanners with FIRST_SCAN_LIMIT=500
- backend/app/services/scan_progress.py - Async-locked progress tracking
- frontend/src/views/SettingsView.vue - Settings UI with scan, AI model, test connection
- AGENTS.md - Project knowledge base with conventions

IMPORTANT DECISIONS
-------------------
- dedupe_chars(tolerance=1, extra_attrs=()) fixes doubled PDF characters
- QR validation: only 01,XX format with valid STA type codes (01,03,04,08,10,11,14,15)
- VALID_INVOICE_TYPES includes numeric codes AND Chinese names AND aliases
- _is_vat_document() heuristic used when LLM unavailable (checks text markers)
- Amount < 0.10 treated as sentinel for parse failure (prompt tells LLM to use 0.01)
- EMAIL_CONCURRENCY = 50 (asyncio.Semaphore)
- FIRST_SCAN_LIMIT = 500 (configurable per scanner)
- Per-email DB sessions for concurrent processing
- asyncio.Lock (not threading.Lock) for progress tracking

EXPLICIT CONSTRAINTS
--------------------
- "Every behavioral change must increment backend/pyproject.toml version AND move the release tag to HEAD before pushing" (from AGENTS.md)
- "CHANGELOG.md and README.md MUST be updated with every release" (from AGENTS.md)
- "100% unit test coverage for all pushed code" (from AGENTS.md)
- "IDEA.md never pushed to remote" (from AGENTS.md)
- "--workers 1 mandatory" (APScheduler in-process)
- "expire_on_commit=False on all session factories" (from AGENTS.md)
- Python venv: /home/helixzz/invoice-maid/backend/.venv/bin/python
- Tests: /home/helixzz/invoice-maid/backend/.venv/bin/python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=100 -x -q
- Deploy: sudo /usr/local/sbin/invoice-maid-upgrade

CONTEXT FOR CONTINUATION
------------------------
- The immediate fix needed: in scheduler.py, change the LLM enrichment trigger from "confidence < 0.6" to "always run for invoices passing VAT gate when fields are missing". The block is around line 324-334 in scheduler.py.
- The user wants aggressive LLM usage — don't try to save API calls
- After fixing enrichment, trigger another full rescan to verify accuracy improves
- Check that all 7 invoice_no values get proper buyer/seller/type after LLM enrichment
- The false negative (电子普通发票 rejected by LLM) suggests the prompt's STEP 1 validation is too strict — consider relaxing "ALL of these present" to "at least 3 of 5"
- Production DB was cleaned (all invoices deleted) at start of v0.7.0 testing
- Use Playwright MCP for browser verification: skill_mcp(mcp_name="playwright", tool_name="browser_navigate", arguments={"url": "https://invoice-maid.helixzz.com"})
