# v1.2.0 Release Handoff

**Released:** 2026-05-10 00:24 +0800
**Tag:** `v1.2.0` → `eab5f5f`
**Pre-deploy:** v1.1.0 (`32b0934`) since 2026-05-09 02:55:56
**Deploy duration:** 13 seconds (00:24:20 → 00:24:33)
**Production:** `https://invoice-maid.helixzz.com` — `{"status":"ok","version":"1.2.0","db":"ok","scheduler":"running","sqlite_vec":true,"invoice_count":252}`

## Commits shipped (8)

| SHA | Title |
|-----|-------|
| `16fd848` | feat(track-a): A1+A2 — invoice_category schema + migration 0015 |
| `9dcb791` | feat(track-a): A3+A5+A6+A7+A10 — backend taxonomy plumbing |
| `c7b2844` | feat(track-a): A4 — LLM prompt category classifier + live-LLM test harness |
| `d743b45` | feat(track-a): A8+A9+A11 — invoice_category frontend + E2E |
| `2ba1c34` | feat(track-b): B1-B12 — Cursor scraper scaffold + 18-test matrix |
| `213f871` | chore(release): v1.2.0 — bump + changelog + roadmap + README |
| `eab5f5f` | fix(tests): A4 — structural guard runs unconditionally (Oracle followup #2) |

## Diff stats

`git diff v1.1.0..v1.2.0 --stat`: 56 files, +3396 / -148

## Verification

- **Backend tests:** 733 passed, 5 skipped (live-LLM, expected), 100% line+branch coverage (4896 stmts, 1162 branches)
- **Frontend:** vue-tsc clean, vite build clean (117 modules, 308KB gzip JS), vitest 23 tests pass, 19 Playwright E2E specs pass
- **Migrations:** 35 migration tests pass; alembic up + down round-trip clean for both 0015 and 0016
- **Oracle review:** `bg_227e4cfa` (ses_1f2afe48affee54Mr5XieVDqtu) — **PASS** with 35/36 confidence, 0 blockers, 5 v1.2.1 followups
- **Pre-deploy backup:** `/var/backups/invoice-maid/2026-05-10_002421` (152 MB DB integrity-ok + 19.8 MB invoice files)
- **Post-deploy invoice count drift:** 250 → 252 within minutes (proves scheduler scanning correctly)

## What v1.2.0 ships

**Track A — `invoice_category` taxonomy:** 5-category enum (`vat_invoice`, `saas_invoice`, `receipt`, `proforma`, `other`). Non-VAT invoices save with category tag instead of getting rejected. `STRICT_VAT_ONLY=true` reverts to v1.1.x behavior. LLM STEP 0.5 classifier + 5 synthetic PDF fixtures + live-LLM harness (`INVOICE_MAID_LIVE_LLM_TESTS=1`). FTS5 rebuilt to include category token. Frontend filter chips + colored badges + by-category stats panel + `?category=` URL sync.

**Track B — Playwright SaaS scraper (Cursor-first):** `BaseScraper` ABC + `PlaywrightSession` async ctxmgr + `CursorScraper` + `ScraperFactory`. Same parse → LLM enrich → dedupe → save pipeline. Fernet-encrypted credentials. Auto storage_state round-tripping. Graceful degradation when playwright absent (`PLAYWRIGHT_AVAILABLE=False` → `[]` + `auth_required` event). 18-test matrix end-to-end mocked. `scripts/cursor_login_local.py` for Mode-B storage_state capture.

## v1.2.1 followups (from Oracle review)

| # | Item | Severity | Notes |
|---|------|----------|-------|
| 1 | `_generate_totp_code` is a placeholder (`secret[:6].ljust(6, "0")`) — won't generate valid TOTP. Cursor TOTP Mode A blocked until fixed. | Medium | Use Mode B (manual storage_state) until fixed. Add `pyotp` dep + real `pyotp.TOTP(secret).now()`. |
| 2 | ~~Structural guard incorrectly skipped~~ | ~~Low~~ | ✅ **Fixed in eab5f5f.** Now `pytest` reports 733 passed / 5 skipped (was 732 / 6). |
| 3 | `by_category` stats omit zero-count categories — CHANGELOG claims they're included but SQL `GROUP BY` only returns populated. | Low | Either fix SQL (`UNION ALL` with VALUES) or update CHANGELOG. |
| 4 | `PUT /invoices/{id}` doesn't accept `invoice_category` — design §6.2 said it would. | Low | Add `invoice_category: InvoiceCategory \| None` to `InvoiceUpdateRequest`. |
| 5 | No `ENABLE_CURSOR_SCRAPER` feature flag — design §7 called for it. Mitigated by operator-action requirement. | Low | Add `ENABLE_CURSOR_SCRAPER: bool = False` env gate in factory. |

## B13 — Real-Cursor manual QA (still blocked)

User must provide:
- Cursor login method (Google OAuth / email-password / magic link)
- 2FA state (TOTP / phone / disabled)
- Sample invoice PDF or invoice ID for selector validation

When provided: run `scripts/cursor_login_local.py` to capture storage_state → POST cursor account via `/api/v1/email-accounts` (no Settings UI yet) → trigger manual scan → verify invoice landed.

## Production state

- **Version:** 1.2.0
- **Invoices:** 252 (was 250 pre-deploy)
- **Scheduler:** running, 60-min interval
- **Last scan:** 2026-05-09T15:56:16+00:00
- **DB:** 152 MB, integrity-ok
- **sqlite_vec:** enabled
- **Outlook delta:** v1.1.0 path preserved, all 8 folders bridged
- **`STRICT_VAT_ONLY`:** `false` (default — v1.2.0 acceptance behavior)
- **`ENABLE_TEST_HELPERS`:** `false` (default — production-safe)

## Rollback procedure (if needed)

```bash
sudo /usr/local/sbin/invoice-maid-upgrade --tag v1.1.0
```

This runs `alembic downgrade -2` (drops 0016 + 0015) and restores v1.1.0. **Caveat:** `saas_invoice` / `receipt` / `proforma` / `other` invoices created post-deploy + Cursor accounts are dropped on downgrade. As of this handoff, all 252 invoices are `vat_invoice` so rollback is safe.

## Next session

Pick from v1.2.1 followups (Oracle items #1, 3, 4, 5) or pivot to whatever the user prioritizes.
