# Handoff: v1.2.0 in progress (Tracks A + B)

**Date:** 2026-05-09 (updated session 2)
**Status:** v1.1.0 in production; v1.2.0 Track A **backend-complete**; frontend + A4 LLM prompt + Track B pending next session.

---

## Progress status

**v1.1.0 (shipped — tag v1.1.0):** Microsoft Graph Delta Query for OutlookScanner. 8/8 folders bridged successfully, 0 anomalies. 682 tests, 100% coverage.

**v1.2.0 Track A (backend-complete, uncommitted / to-be-committed in the next commit):**
- ✅ **A1**: Migration 0015 + `InvoiceCategory` Pydantic enum + `Invoice.invoice_category` ORM column
- ✅ **A2**: FTS5 schema rebuild + triggers include `invoice_category`; `database.py:_create_fts5_objects` updated
- ✅ **A3**: Decoupled save-path rejection from `VALID_INVOICE_TYPES`. New rule: only `invoice_category=vat_invoice` + `is_valid_tax_invoice=False` rejects; non-VAT categories save. `STRICT_VAT_ONLY` env flag reverts to v1.1.x behavior. LLM merge extended to fire for non-VAT categories (critical: was blocking saas invoices from populating `invoice_no`).
- ✅ **A5**: `GET /invoices?category=X&category=Y` + `GET /invoices/export?category=...` filter threaded into `search_service.search_fts`
- ✅ **A6**: `GET /stats` adds `by_category` alongside `by_type`
- ✅ **A7**: CSV/ZIP export include `invoice_category` column (inserted before `invoice_type`)
- ✅ **A10**: `POST /test-helpers/seed-invoice-category-mix` seeds 5 invoices (one per category) for future Playwright E2E
- ⚠️ **Test coverage maintained at 100%** (701 backend tests, up from 682 in v1.1.0)

**Remaining Track A tasks (next session):**
- **A4**: Rewrite `extract_invoice.txt` STEP 0 + create `tests/fixtures/invoice_samples/` tree + `tests/fixtures/generate_category_samples.py` + `tests/test_prompts/test_extract_categories.py`. Adds reportlab dev dep. **Requires `INVOICE_MAID_LIVE_LLM_TESTS=1`** + real LLM access for QA (CI skips by default).
- **A8**: Frontend — `InvoiceCategory` type, `MultiSelectChips.vue`, `CategoryBadge.vue`, filter mount in `InvoiceListView.vue`, persist in `filter_json`
- **A9**: Frontend stats dashboard `by_category` panel
- **A11**: `frontend/e2e/invoice-category-filter.spec.ts` (uses `/test-helpers/seed-invoice-category-mix` from A10)
- **A12**: Version bump + CHANGELOG + ROADMAP + README (combined with Track B when both ready)

**Track B (all tasks pending, next session, blocked on user input for B13):**
See `.sisyphus/plans/v1.2.0-track-b-saas-scraper.md` §9 — 13 tasks, 2-3 working sessions. B6/B7/B10/B11 parallelizable after B1-B5.

Track B manual QA (B13) requires user input:
1. Cursor login method (email+password strongly preferred)
2. 2FA status + preferred mode (stored TOTP vs manual storage_state paste)
3. One sample Cursor invoice PDF (drop to `.sisyphus/research/samples/`, gitignored)

---

## Momus-approved design docs (round 4 OKAY on both)

- **`.sisyphus/plans/v1.2.0-track-a-invoice-category.md`** (Track A)
- **`.sisyphus/plans/v1.2.0-track-b-saas-scraper.md`** (Track B)

---

## Files modified in this session (to be committed)

Session 1 (already committed as 16fd848):
- `backend/alembic/versions/0015_add_invoice_category.py` (new)
- `backend/app/database.py` (FTS5 include invoice_category)
- `backend/app/models/invoice.py` (add invoice_category Mapped column)
- `backend/app/schemas/invoice.py` (add InvoiceCategory enum)
- `backend/app/api/invoices.py` (_serialize_invoice includes field)
- `backend/tests/test_migrations.py` (+6 tests)

Session 2 (uncommitted, pending this commit):
- `backend/.env.example` (document STRICT_VAT_ONLY)
- `backend/app/config.py` (STRICT_VAT_ONLY: bool = False)
- `backend/app/services/invoice_parser.py` (ParsedInvoice.invoice_category field)
- `backend/app/services/manual_upload.py` (A3 rejection decoupling + LLM merge extension)
- `backend/app/tasks/scheduler.py` (A3 + InvoiceCategory import + LLM merge extension)
- `backend/app/api/invoices.py` (A5 category filter query params)
- `backend/app/services/search_service.py` (A5 categories filter)
- `backend/app/api/stats.py` (A6 by_category)
- `backend/app/services/invoice_csv.py` (A7 column added)
- `backend/app/api/test_helpers.py` (A10 seed-invoice-category-mix endpoint)
- `backend/tests/test_scheduler.py` (+2 new tests + 3 updated assertions + InvoiceExtract import)
- `backend/tests/test_api/test_invoice_upload.py` (+2 new tests)
- `backend/tests/test_api/test_invoices.py` (+5 new tests + 1 updated assertion)
- `backend/tests/test_api/test_stats.py` (+1 new test + 2 updated assertions)
- `backend/tests/test_services/test_invoice_csv.py` (3 updated assertions)
- `backend/tests/test_api/test_test_helpers.py` (+3 new tests)

---

## Key design decisions locked in (don't reverse without re-reading design §5.2)

### The v1.2.0 rejection contract

```python
# services/manual_upload.py + tasks/scheduler.py
if settings.STRICT_VAT_ONLY:
    # v1.1.x revert
    should_reject = llm_rejected or (not type_is_valid and heuristic_rejected)
else:
    # v1.2.0 default
    should_reject = (
        final_category == "vat_invoice"
        and (llm_rejected or (not type_is_valid and heuristic_rejected))
    )
```

### The LLM-merge extension (critical bug fix during A3)

```python
# Both manual_upload.py and scheduler.py
if extracted is not None and (
    extracted.is_valid_tax_invoice
    or extracted.invoice_category != InvoiceCategory.VAT_INVOICE
):
    # merge...
```

Without the `OR invoice_category != VAT_INVOICE` extension, saas_invoice extracts with `is_valid_tax_invoice=False` would skip the merge block entirely — `parsed.invoice_no` stays None → low_confidence rejection. **This is load-bearing; do not remove.**

### Coverage-trimmed LLM merge

The `extracted.invoice_category is not None` guard was removed because Pydantic default is `VAT_INVOICE` (never None in production). Dead branch eliminated. Tests depend on the simpler form `parsed.invoice_category = extracted.invoice_category.value`.

---

## Recommended next-session flow

1. **Context prime:** Read the Momus-approved design docs (`.sisyphus/plans/v1.2.0-*.md`) — authoritative, don't deviate.
2. **A4 (LLM prompt rewrite):** requires live LLM. Follow the exact sub-task structure A4a-A4e per the Momus-approved design.
3. **A8-A11 (frontend):** parallel-able once A10 seed endpoint exists (done this session).
4. **Track B Tasks B1-B12:** All writable against mocked Playwright. B13 real-Cursor QA blocks on user credentials.
5. **Ship:** combined v1.2.0 CHANGELOG + ROADMAP + README, Oracle review, deploy.

---

## Production state

- v1.1.0 live; 250 invoices; Outlook folders on v1.1.0 delta state; zero `delta_resync` / `delta_bridge_overflow` events
- `last_scan_at` advancing cleanly every SCAN_INTERVAL_MINUTES (60min)
- Health endpoint: `"version": "1.1.0"`, scheduler running, sqlite_vec true

---

## Blocking on user input (non-urgent)

1. **Cursor login method** (email+password / Google / GitHub / Apple) — needed for Track B Task B6 real-Cursor QA
2. **2FA decision** — needed for Track B Task B6 (TOTP mode A vs storage-state paste mode B)
3. **Sample Cursor invoice PDF** — needed for Track B B13 AND Track A A4 live-LLM validation

When you provide these, we can close Track B's real-Cursor QA step AND make A4's prompt validation run against a real SaaS invoice instead of a synthetic fixture.

---

## What just landed (commit 32b0934 + the uncommitted Track A A1+A2 work)

**v1.1.0 (shipped — 32b0934):** Microsoft Graph Delta Query for OutlookScanner. 8/8 folders bridged successfully on scheduled scan, 0 anomalies. 682 tests, 100% coverage.

**v1.2.0 (in progress, uncommitted):**
- Track A Task A1: migration 0015 + `InvoiceCategory` Pydantic enum + `Invoice.invoice_category` ORM column
- Track A Task A2: FTS5 schema rebuild + triggers include `invoice_category`; `database.py:_create_fts5_objects` updated for fresh installs
- `InvoiceResponse` schema includes `invoice_category`; `_serialize_invoice` populates it
- 6 new migration tests (0015 up/down/backfill/FTS5/index/enum-literal sync) all passing
- 688 tests, 100% coverage maintained

---

## Momus-approved design docs (round 4, OKAY both)

- **`.sisyphus/plans/v1.2.0-track-a-invoice-category.md`** — Track A: structured `invoice_category` enum (vat_invoice / receipt / proforma / saas_invoice / other) with backfill migration, category filter on list endpoint + stats + CSV + frontend multi-select. **A1+A2 done; A3–A12 pending.**
- **`.sisyphus/plans/v1.2.0-track-b-saas-scraper.md`** — Track B: SaaS portal scraping (Cursor-first) via Playwright, new `BaseScraper` abstract class, EmailAccount extended with `playwright_storage_state` + secondary creds + TOTP secret, scheduler persists updated storage state. **All tasks pending.**

Both docs passed Momus after 4 rounds of review. They're authoritative — follow them exactly.

---

## Remaining work (Track A)

| Task | Scope | Estimated effort |
|---|---|---|
| A3 | Decouple save-path rejection from VALID_INVOICE_TYPES. `manual_upload.py:322-341` and `scheduler.py:513-536`. Add `STRICT_VAT_ONLY` env var for soft rollback. | 30 min |
| A4 | Rewrite `extract_invoice.txt` + create `tests/fixtures/invoice_samples/` tree + `tests/fixtures/generate_category_samples.py` + `tests/test_prompts/test_extract_categories.py`. Add reportlab to dev deps. **Requires `INVOICE_MAID_LIVE_LLM_TESTS=1` + real LLM access for QA.** | 1 hr |
| A5 | `GET /invoices` + `GET /invoices/export` accept `category: list[str] = Query([])`; push into `search_service.py`. Update `frontend/src/api/client.ts`. | 45 min |
| A6 | `GET /stats` adds `by_category` field. Add to schema + handler. | 20 min |
| A7 | CSV/ZIP export include `invoice_category` column (before `invoice_type`). | 20 min |
| A8 | Frontend: `InvoiceCategory` type, `MultiSelectChips.vue`, `CategoryBadge.vue`, filter mount in `InvoiceListView.vue`, persist in `filter_json`. | 2 hr |
| A9 | Frontend stats dashboard `by_category` panel. | 45 min |
| A10 | `POST /test-helpers/seed-invoice-category-mix`. | 20 min |
| A11 | `frontend/e2e/invoice-category-filter.spec.ts`. | 30 min |
| A12 | Version bump + CHANGELOG + ROADMAP + README (combined with Track B when both ready). | 15 min |

**Estimated total Track A remaining: 6-7 hours of focused work.**

## Remaining work (Track B)

See `.sisyphus/plans/v1.2.0-track-b-saas-scraper.md` §9 — 13 tasks, 2-3 working sessions. **B6/B7/B10/B11 parallelizable after B1-B5.**

Track B has a **user-input-blocked final QA step**: requires user's Cursor login email + password + 2FA decision + sample invoice PDF. All code (B1-B12) can be completed with mocked Playwright tests; only the real-Cursor manual QA is blocked.

---

## Critical execution details for the next session

### Track A Task A3 specifics

The current rejection logic (v1.1.0) in `manual_upload.py:322-341`:
```python
final_type = parsed.invoice_type or ""
type_is_valid = final_type in VALID_INVOICE_TYPES or any(
    vt in final_type for vt in VALID_INVOICE_TYPES if len(vt) > 3
)
if (
    not type_is_valid
    and extracted is not None
    and not extracted.is_valid_tax_invoice
):
    return UploadResult(outcome="not_vat_invoice", ...)
```

v1.2.0 replacement per design §5.2:
```python
final_category = (extracted.invoice_category if extracted else None) or InvoiceCategory.VAT_INVOICE

# Rejection rule v1.2.0: only reject when category=vat_invoice AND LLM
# says invalid. Non-VAT categories save with is_valid_tax_invoice=False.
# STRICT_VAT_ONLY env var reverts to legacy rejection for emergency rollback.
settings = get_settings()
if settings.STRICT_VAT_ONLY:
    if not type_is_valid and extracted is not None and not extracted.is_valid_tax_invoice:
        return UploadResult(outcome="not_vat_invoice", ...)
elif (
    final_category == InvoiceCategory.VAT_INVOICE
    and extracted is not None
    and not extracted.is_valid_tax_invoice
):
    return UploadResult(outcome="not_vat_invoice", ...)
```

Mirror the same change in `scheduler.py:513-536`. Add `STRICT_VAT_ONLY: bool = False` to `config.py`.

### Track A: Invoice ORM constructor callsites

Any place that creates an `Invoice(...)` must now either pass `invoice_category=` explicitly OR rely on the `server_default="vat_invoice"` (which triggers only if the column is omitted from the INSERT). Places to check:
- `app/tasks/scheduler.py:615` — constructs `Invoice(...)` with `invoice_type=parsed.invoice_type or "未知"`. **Must add** `invoice_category=extracted.invoice_category if extracted else InvoiceCategory.VAT_INVOICE` to the kwargs. Or let the DB default handle it — but explicit is safer.
- `app/services/manual_upload.py:441` — same treatment as scheduler.
- `app/api/test_helpers.py` — the existing seed data uses `invoice_type=SMOKE_INVOICE_TYPE`; should pass `invoice_category="vat_invoice"` for clarity in tests.
- `tests/conftest.py:281` — same.

### Track A: LLM extraction merge

`manual_upload.py:182-183`:
```python
if extracted.invoice_type and extracted.invoice_type != "未知":
    parsed.invoice_type = extracted.invoice_type
```

Also need to merge `invoice_category`:
```python
if extracted.invoice_category:
    parsed.invoice_category = extracted.invoice_category
```

And `ParsedInvoice` dataclass in `invoice_parser.py:20-36` needs an `invoice_category: str | None = None` field.

### Track A Task A5 SQL shape

```python
# search_service.py:search_fts additional filter
if categories:
    stmt = stmt.where(Invoice.invoice_category.in_(categories))
    count_stmt = count_stmt.where(Invoice.invoice_category.in_(categories))
```

### Track A: Frontend badge re-coloring

`InvoiceListView.vue:557` and `InvoiceDetailView.vue:184` currently hardcode indigo-vs-slate based on substring match on `invoice_type`. v1.2.0 drives color from `invoice_category`:

```typescript
const CATEGORY_COLORS: Record<string, string> = {
  vat_invoice: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  saas_invoice: 'bg-violet-50 text-violet-700 border-violet-200',
  receipt: 'bg-amber-50 text-amber-700 border-amber-200',
  proforma: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  other: 'bg-slate-100 text-slate-700 border-slate-200',
}
```

Extract to a new `components/CategoryBadge.vue` so the logic is reusable.

---

## Production state snapshot

- v1.1.0 live; 250 invoices; all Outlook folders on v1.1.0 delta state; zero `delta_resync` / `delta_bridge_overflow` events
- Last bridged scan: 2026-05-09 03:56:13 (scan #852, 25 emails processed, 18 seconds)
- Health endpoint: `"version": "1.1.0"`, scheduler running, sqlite_vec true

---

## Recommended next-session flow

1. **Context prime:** Read both design docs fresh (`.sisyphus/plans/v1.2.0-*.md`) — they're authoritative, don't deviate.
2. **Commit A1+A2:** Use commit message `feat(track-a): v1.2.0 A1+A2 — invoice_category schema + migration 0015 (688 tests, 100% coverage)`.
3. **Execute A3 → A7 (backend-only):** Each task has executable QA in the design doc. Run `pytest --cov=app --cov-fail-under=100` after each task.
4. **Execute A8 → A11 (frontend):** Then `npm run build && npx vitest run && npx playwright test` gate.
5. **Start Track B in parallel** when context permits. B1–B5 are backend scaffolding; B6 is the CursorScraper core with mocked tests.
6. **Ship v1.2.0 via A12 + B13:** combined version bump, CHANGELOG for both tracks, Oracle review, deploy.
7. **Block on user input** for Track B real-Cursor manual QA — document what's needed in the v1.2.0 release notes.

---

## Files modified in this session (uncommitted)

- `backend/alembic/versions/0015_add_invoice_category.py` (new, ~150 lines)
- `backend/app/database.py` (FTS5 include invoice_category)
- `backend/app/models/invoice.py` (add invoice_category Mapped column)
- `backend/app/schemas/invoice.py` (add InvoiceCategory enum + InvoiceExtract.invoice_category + InvoiceResponse.invoice_category)
- `backend/app/api/invoices.py` (_serialize_invoice populates invoice_category)
- `backend/tests/test_migrations.py` (+6 tests for 0015)
- `.sisyphus/plans/v1.2.0-track-a-invoice-category.md` (new, design doc)
- `.sisyphus/plans/v1.2.0-track-b-saas-scraper.md` (new, design doc)
- `.sisyphus/research/v1.2.0-scoping.md` (pre-existing, updated with user context)

The plans directory is gitignored — the design docs ship via git only if explicitly committed. Intentionally keeping them internal for now.
