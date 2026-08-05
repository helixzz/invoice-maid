# Handoff — Phase 2 continuation (v0.9.0 multi-user transition)

**Session date:** 2026-04-21 (prior to this handoff)
**Current version in production:** `v0.9.0-alpha.3`
**Next release to ship:** `v0.9.0-alpha.4` (Phase 2)

---

## Where we are

The v0.9.0 multi-user transition is being rolled out in phases, each a deployable alpha release. Phase 0 (operational hardening) and Phase 1 (auth layer) have shipped and are running cleanly on production. Phase 2 (add `user_id` columns to tenant tables) is **in progress but uncommitted** — 6 of 7 model files have their `user_id` columns staged in the working tree; the remaining webhook_logs model, the Alembic migration, the tests, and the dry-run upgrade verification are all still to do.

**Production is healthy on `v0.9.0-alpha.3`. 239 invoices intact. 1 user (`admin@local`). Schedulers running.**

---

## Completed work (do not redo)

### v0.8.10 — Operational hardening (shipped)
- `PRAGMA journal_size_limit=67108864` (64 MiB cap on WAL file on disk)
- `LLMCache.expires_at` column + hourly TTL eviction (30d for classify/analyze_email_v3, 365d for extract)
- `ExtractionLog` 90-day retention with daily batched cleanup
- Alembic migration `0009_llm_cache_expires_at`
- New `cleanup_llm_cache` + `cleanup_extraction_logs` APScheduler jobs registered in `app/tasks/scheduler.py`
- Tests: 497 passing, 100% coverage

### v0.9.0-alpha.1 — Phase 1 auth layer (shipped)
- New `User` model (`app/models/user.py`): id, email unique, hashed_password, is_active, is_admin, created_at, updated_at
- New `UserSession` model (`app/models/user_session.py`): id, user_id FK ondelete CASCADE, token_hash (SHA-256 of JWT), created_at, expires_at, revoked_at, last_seen_at, user_agent, ip_address
- Alembic migration `0010_users_and_sessions`
- Bootstrap hook in `app/services/bootstrap.py` — creates `users[1]` on first lifespan startup when `users` table is empty, reading `ADMIN_EMAIL` + `ADMIN_PASSWORD_HASH` from settings. Idempotent.
- `ADMIN_EMAIL` setting in `app/config.py` — defaults to `"admin@local"`
- `CurrentUser` dep changed from `Annotated[str, ...]` to `Annotated[User, ...]`
- New `CurrentUserAndSession` dep returning `(User, UserSession)` for endpoints that need the session row
- Auth service helpers: `hash_token`, `create_user_session`, `resolve_active_session`, `revoke_session`, `revoke_all_sessions`
- JWTs now include a `jti` random nonce (required because `user_sessions.token_hash` is unique and two same-second logins would otherwise collide)
- `get_current_user` validates the session, not just the JWT — JWT alone is insufficient; must have an unrevoked `user_sessions` row matching the SHA-256 hash of the token
- New endpoints: `POST /auth/logout`, `POST /auth/logout-all`, `GET /auth/me`, `GET /auth/sessions`
- `/auth/login` now requires `{email, password}` (422 if only password sent)
- Dependencies: `pydantic[email]` added (though see alpha.3 — EmailStr is no longer used)
- Tests: 519 passing, 100% coverage

### v0.9.0-alpha.2 — Frontend login form email field (hotfix, shipped)
- Added email input to `LoginView.vue` alongside password
- `stores/auth.ts`: `login(email, password)` signature
- `api/client.ts`: `login(email, password)` sends `{email, password}` JSON
- Frontend `dist/` rebuilt and committed

### v0.9.0-alpha.3 — Login accepts `admin@local` (hotfix, shipped)
- `LoginRequest.email` changed from `EmailStr` to plain `str` (length-bounded 1-255 chars)
- Rationale: self-hosted deployments use bare-hostname emails like `admin@local`. EmailStr requires a dotted domain, so the bootstrap admin couldn't authenticate. DB lookup is still exact-match string equality, so no security regression.
- Regression test `test_login_accepts_unqualified_hostname_email` added
- Tests: 520 passing, 100% coverage

---

## In-progress Phase 2 work (UNCOMMITTED, in working tree)

6 model files have `user_id: Mapped[int | None]` columns added, nullable, with `ForeignKey("users.id", ondelete="CASCADE")` and `index=True`:

```
 M backend/app/models/correction_log.py
 M backend/app/models/email_account.py
 M backend/app/models/extraction_log.py
 M backend/app/models/invoice.py
 M backend/app/models/saved_view.py
 M backend/app/models/scan_log.py
```

**`backend/app/models/webhook_log.py` is NOT yet edited and must be done in the next session.**

The column is placed directly after `id: Mapped[int] = mapped_column(primary_key=True)` in each model. Example from `invoice.py`:

```python
class Invoice(Base):
    __tablename__: ClassVar[str] = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    invoice_no: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # ... rest unchanged
```

Two models (`email_account.py`, `saved_view.py`) also had `ForeignKey` added to their imports (+1 line each); the other four already had it imported.

### Remaining Phase 2 work

1. **Add `user_id` to `backend/app/models/webhook_log.py`** (same pattern as above — needs `ForeignKey` import added)

2. **Write Alembic migration `0011_add_user_id_to_tenant_tables.py`**:
   - Revision: `0011_add_user_id_to_tenant_tables`
   - Revises: `0010_users_and_sessions`
   - Adds nullable `user_id INTEGER` column to each of the 7 tables
   - Backfills each column with `UPDATE {table} SET user_id = 1 WHERE user_id IS NULL`
   - Creates indexes per the strategy below
   - Does NOT add NOT NULL or FK constraints (that's Phase 3)

   **Indexing strategy (per prior analysis):**
   - `invoices`: composite `(user_id, invoice_date DESC)` — search + list queries hit this
   - `email_accounts`: `(user_id)`
   - `scan_logs`: `(user_id, started_at DESC)`
   - `extraction_logs`: `(user_id, created_at DESC)`
   - `correction_logs`: `(user_id)`
   - `saved_views`: `(user_id)`
   - `webhook_logs`: `(user_id, created_at DESC)`

   Migration docstring should cite: "Phase 2 of v0.9.0. Additive only — no constraint tightening. Existing rows backfilled to users[1] (the bootstrap admin from migration 0010). Phase 3 migration 0012 will convert user_id to NOT NULL and add FKs."

3. **Deliberately NOT touched in Phase 2:**
   - `llm_cache` — stays shared across users by design (Oracle + librarian both recommended this for self-hosted single-org). Per-user caching would be a future config flag, not automatic.
   - `app_settings` — instance-wide by design, not per-user.
   - `users`, `user_sessions` — already user-scoped by construction.

4. **Tests** (all in `backend/tests/`):
   - `test_schema_shape.py` (new file, or extend an existing one) — assert each of the 7 tenant models has a `user_id` column, nullable, FK to users.id
   - Extend or add migration tests verifying backfill correctness
   - Keep 100% coverage

5. **Dry-run upgrade test against a production DB copy:**
   ```bash
   sudo cp /var/lib/invoice-maid/data/invoices.db /tmp/v090a4_test.db
   sudo chown $USER:$USER /tmp/v090a4_test.db
   cd /home/helixzz/invoice-maid/backend
   DATABASE_URL="sqlite+aiosqlite:////tmp/v090a4_test.db" .venv/bin/alembic upgrade head
   sqlite3 /tmp/v090a4_test.db "SELECT count(*) FROM invoices WHERE user_id = 1"  # expect 239
   sqlite3 /tmp/v090a4_test.db "SELECT count(*) FROM email_accounts WHERE user_id = 1"  # expect 3
   rm /tmp/v090a4_test.db
   ```
   All counts must match pre-migration values with every row now having `user_id = 1`.

6. **Ship as `v0.9.0-alpha.4`:**
   - Bump `backend/pyproject.toml` version to `0.9.0a4`
   - Update README badge to `v0.9.0-alpha.4` + test count
   - Add CHANGELOG entry (see writing-rule below — no incident narrative)
   - Add ROADMAP entry
   - Commit, tag `v0.9.0-alpha.4`, push, `sudo /usr/local/sbin/invoice-maid-upgrade`
   - Verify production `/api/v1/health` reports `0.9.0a4`, all invoices still accessible, schedulers still running

---

## Writing rule — READ THIS BEFORE WRITING CHANGELOG/COMMITS

CHANGELOG and release messages are **external product documentation**, not engineering diary. A previous round of PII leaked through commit messages + CHANGELOG and required a full git history rewrite (see commit `7bce5af` and the backup at `/tmp/invoice-maid-backup-20260420-154219` if it still exists). To prevent recurrence:

**ALLOWED:** What the code does, why it's correct (regulation/RFC/CVE citations), test additions, API contract changes, migration steps, performance numbers from reproducible benchmarks.

**FORBIDDEN:** "A user reported...", "The user's N invoices...", specific invoice numbers, amounts, dates, buyer/seller/route names, email addresses of real operators, "Production regression from invoice #N", reference to specific mailbox/account/scan events, content that wouldn't be safe to print on a billboard for a stranger cloning the repo.

**Test before writing:** *If a stranger on GitHub clones this repo tomorrow, does this line make sense without any private context?* If no, it belongs in `.sisyphus/` (gitignored), not CHANGELOG.

**Template:**
```
### Fixed
- **Short symptom** — one-line failure-mode description (not who saw it).
  Root cause in 1-2 sentences. How the fix addresses it. Regression test added.
```

---

## Non-obvious operational facts

1. **Deploy pipeline:** `sudo /usr/local/sbin/invoice-maid-upgrade` pulls origin/main, runs alembic, restarts systemd. Takes ~30 seconds. It auto-takes a pre-upgrade backup at `/var/backups/invoice-maid/<timestamp>/invoices.db`, so rollback is `.restore` from that file.

2. **Production DB location:** `/var/lib/invoice-maid/data/invoices.db` (owned by `invoice-maid:invoice-maid`, mode 640). Read via `sudo sqlite3 ...`.

3. **.env location:** `/etc/invoice-maid/invoice-maid.env` (symlinked into the source tree as `backend/.env` by the deploy script).

4. **`ADMIN_EMAIL` env var** is only used by the bootstrap hook on the **first** boot when `users` table is empty. After that it's a no-op. To change the admin email, update the DB row directly: `sudo sqlite3 /var/lib/invoice-maid/data/invoices.db "UPDATE users SET email='new@example.com' WHERE id=1;"`.

5. **Tests run quickly** but full coverage is enforced at 100%. Use:
   ```bash
   export CI=true PAGER=cat
   cd /home/helixzz/invoice-maid/backend
   .venv/bin/python -m pytest --no-header -q --cov=app --cov-fail-under=100
   ```

6. **CI-simulated test run** (proves the app boots with env vars alone, no `.env`):
   ```bash
   cd /home/helixzz/invoice-maid/backend
   mv .env .env.tmp
   DATABASE_URL='sqlite+aiosqlite:///./data/test-ci.db' \
   ADMIN_PASSWORD_HASH='$2b$12$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKL' \
   ADMIN_EMAIL='admin@example.com' \
   JWT_SECRET='test-secret-at-least-32-chars-long-for-ci' \
   LLM_BASE_URL='https://api.openai.com/v1' \
   LLM_API_KEY='sk-test' \
   .venv/bin/python -m pytest --no-header -q --cov=app --cov-fail-under=100
   mv .env.tmp .env
   rm -f data/test-ci.db*
   ```

7. **Integration testing discipline:** The three alphas in a row (1 → 2 → 3) were caused by skipping end-to-end UI validation after backend changes. Going forward, any alpha that changes the login flow, routing, or auth deps must be validated with a real login round-trip via the deployed UI **before** declaring the release done. Don't repeat the three-alpha dance.

8. **Stash discipline:** If you ever have in-progress Phase 2/3/4 work uncommitted and need to ship a hotfix, use `git stash push <files> -m "phase-N-in-progress"` to set aside, ship the hotfix cleanly, then `git stash pop`. This session did that successfully for the alpha.2 hotfix.

---

## Remaining roadmap (in order)

### v0.9.0-alpha.4 — Phase 2 (next session's focus)
Add nullable `user_id` columns to 7 tenant tables with backfill to users[1]. **Additive only** — no constraint tightening. See "Remaining Phase 2 work" section above.

### v0.9.0-alpha.5 — Phase 3 (highest-risk migration)
Alembic `0012_tighten_user_id_constraints.py`:
- `batch_alter_table` on each of the 7 tables to make `user_id` NOT NULL and add the FK constraint
- Drop + recreate FTS5 triggers on `invoices` (SQLite's `batch_alter_table` rewrites the table, dropping triggers in the process)
- `INSERT INTO invoices_fts(invoices_fts) VALUES ('rebuild')` to repopulate FTS5 index
- Replace `UNIQUE(invoice_no)` with `UNIQUE(user_id, invoice_no)` composite
- Verify FTS5 search still works end-to-end after the rebuild
- **Dry-run against a production DB copy before shipping.** This is the migration most likely to break something.

### v0.9.0-alpha.6 — Phase 4 (app refactor)
- Repository pattern: `InvoiceRepository(db, user_id)`, etc. — every tenant-scoped query goes through a repo that structurally requires `user_id`. No raw `db.execute(select(Invoice))` anywhere.
- `Invoice.source_origin` enum (`email` / `manual` / `api`) added. `email_account_id` made nullable.
- **Retire the `EmailAccount(type='manual')` sentinel** introduced in v0.8.6. Data migration: for each manual-upload invoice currently pointing at the per-user manual-sentinel row, null out `email_account_id` and set `source_origin='manual'`.
- File storage migrated to per-user subdirs: `storage/{user_id}/filename.pdf`. One-shot script moves existing files to `storage/1/` and updates `Invoice.file_path` prefixes.
- Tenant-isolation test suite: for every GET/POST/PUT/DELETE endpoint, every export path, every search path, a test that verifies user A cannot access user B's data.

### v0.9.0-alpha.7 — Phase 5 (UX)
- Admin UI: users list, create user, reset password, disable account, view sessions
- Registration flow (opt-in via feature flag): `POST /auth/register`, `GET /auth/verify-email`
- Per-user settings: timezone, LLM model override, per-user OpenAI API key, `LLMCacheScope=instance|user` flag
- Per-user webhook delivery: `UserWebhook` model, retry with exponential backoff, per-user signing secrets
- **Invoices tab statistics panel** (user specifically requested this): live-updating summary strip showing total count, total amount, date range, by-type breakdown. Reactive to both active filters AND multi-selection. Design decision deferred: client-side from visible-page-only, or dedicated aggregation endpoint returning `COUNT/SUM/MIN/MAX` over the filter query. **Librarian agent recommended the aggregation endpoint** for scalability past a single pagination page.

### v0.9.0 (final)
- Full dry-run upgrade from v0.8.9 through all phases to v0.9.0
- README refresh for multi-user mode
- Upgrade guide for operators
- Tag `v0.9.0` (not alpha)

---

## Files to read first in next session

1. `/home/helixzz/invoice-maid/AGENTS.md` — project knowledge base
2. This handoff file (you're reading it)
3. `backend/alembic/versions/0010_users_and_sessions.py` — template for 0011's structure
4. `backend/alembic/versions/0008_manual_upload_pseudo_account.py` — template for idempotent backfill patterns
5. `backend/app/models/invoice.py` (staged diff) — template for the `user_id` column shape to replicate in webhook_log.py
6. `git diff backend/app/models/` — see exactly what's staged

---

## Snapshot for quick verification

```
Production:  https://invoice-maid.helixzz.com
Version:     v0.9.0-alpha.3 (0.9.0a3)
Invoice #:   239
Users:       1 (admin@local, bootstrap admin)
DB head:     0010_users_and_sessions
Tests:       520 passing, 100% coverage
Last scan:   ~10-11:00 UTC 2026-04-20
Schedulers:  scan_all_accounts, cleanup_llm_cache, cleanup_extraction_logs
```

To verify at start of next session:

```bash
curl -sS https://invoice-maid.helixzz.com/api/v1/health | python3 -m json.tool
cd /home/helixzz/invoice-maid/backend && export CI=true PAGER=cat && \
  .venv/bin/python -m pytest --no-header -q --cov=app --cov-fail-under=100 | tail -3
cd /home/helixzz/invoice-maid && git log --oneline -5 && git status --short
```

Expected: health ok on 0.9.0a3, 520 tests passing, 6 uncommitted model edits in working tree, last commit `7ea1bec`.
