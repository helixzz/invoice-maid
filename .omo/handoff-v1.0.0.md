# Handoff: v1.0.0 (In-Progress — Uncommitted)

**Date**: 2026-04-25
**Author**: Sisyphus (previous session)
**Status**: v1.0.0 code + docs **staged but uncommitted**. Frontend built, version bumped, CHANGELOG+ROADMAP written. **NOT YET shipped to production.**

---

## TL;DR for Next Session

1. **Production is healthy on v0.9.1** (not v1.0.0 — `0.9.1` is live, 239 invoices, scheduler running, deployed 2026-04-25 ~02:19).
2. **v1.0.0 work is staged in the working tree but uncommitted.** The only code change since v0.9.1 is a frontend template/logic refactor (Fix 8) in `frontend/src/views/SettingsView.vue`. The backend is untouched.
3. **Immediate next step**: verify the staged Fix 8 output, commit, tag `v1.0.0`, push, deploy to production via `sudo /usr/local/sbin/invoice-maid-upgrade --tag v1.0.0` (the upgrade script's default tag sort doesn't handle PEP 440 post-releases properly — always pass `--tag` explicitly for alpha/beta/rc/post releases).
4. **Do not ship the Delta Query rewrite** — that's v1.1.0 and was explicitly deferred per Oracle review. See "Explicitly Deferred" below.

---

## Uncommitted State (verify first with `git status`)

```
 M CHANGELOG.md                          # v1.0.0 entry added at top
 M README.md                             # version badge 0.9.1 → 1.0.0
 M ROADMAP.md                            # v1.0.0 section added at top
 M backend/pyproject.toml                # version = "1.0.0"
 M frontend/src/views/SettingsView.vue   # Fix 8 aggregation + template refactor
 D frontend/dist/assets/index-DDEnTq1j.css   # old v0.9.1 frontend build artifact
 D frontend/dist/assets/index-pYO-68aV.js    # old v0.9.1 frontend build artifact
 M frontend/dist/index.html              # new asset hashes
?? frontend/dist/assets/index-BGQKYqtC.js   # new v1.0.0 frontend build artifact
?? frontend/dist/assets/index-Db6UovQq.css  # new v1.0.0 frontend build artifact
```

Frontend is already built. `git add -A && git commit ...` will include the new dist/* hashes correctly.

---

## What v1.0.0 Actually Does

**One user-visible change**: the scan-log expanded panel in the Settings > Scan Operations tab now aggregates `extraction_logs` by `email_uid` instead of listing one row per attachment.

**Why**: the 2026-04-20 user investigation ("missed invoice") concluded that no invoice was actually missed (Sam's Club sent the same e-invoice number via two emails 247 seconds apart; the system correctly deduped), but the UI showed 6 extraction_log rows (2 emails × 3 attachments each: pdf, ofd, xml) which looked like "6 things went wrong." Fix 8 aggregates them into **one card per email** with a per-attachment chip row inside.

**Key files**:
- `frontend/src/views/SettingsView.vue` lines 210-305: new `aggregateByEmail()`, `OUTCOME_PRIORITY`, `summarizeEmails()`, `EmailAggregate` interface, `EmailCountBreakdown` interface (all pure TypeScript, no backend contract change)
- Same file lines 959-1088 (approx, after my edit): expanded-panel template rewritten to use these helpers; shows "Summary (by email)" banner with Chinese-plain-language counts ("2 封邮件 · 保存 1 张新发票 · 去重 1 封"), then one card per email
- No backend, no schema, no migration, no API change

**Outcome precedence** (the `best_outcome` on each email card):
```
saved(100) > success(100) > duplicate(80) > skipped_seen(70)
> low_confidence(60) > not_vat_invoice(55) > scam_detected(55)
> parse_failed(40) > error(30) > failed(30) > not_invoice(10)
```

The `duplicate` badge has an explicit tooltip: "Same invoice_no already exists — correctly deduped, no action needed". This addresses the user's exact complaint from the investigation.

---

## Shipped Since the Last Handoff (v0.9.0-alpha.4)

Fifteen releases shipped since last handoff. Full cumulative journey:

| Version | Phase | Headline |
|---|---|---|
| 0.9.0-alpha.5 | 3 | NOT NULL + FK + composite UNIQUE on tenant tables (migration 0012) |
| 0.9.0-alpha.6 | 4a | Tenant isolation on every read path; `assert_owned` helper returns 404 not 403; 22-test tenant-isolation suite |
| 0.9.0-alpha.7 | 4b.1 | Per-user file storage `STORAGE_PATH/users/{user_id}/invoices/` (migration 0013) |
| 0.9.0-alpha.7.post1 | — | **Incident hotfix**: alembic `env.py` didn't auto-load `.env`; migration 0013 rewrote `file_path` in DB but didn't move files on prod. Manually recovered 239 files. Added `python-dotenv` auto-load. |
| 0.9.0-alpha.8 | 5a | `POST /auth/register` (gated by `ALLOW_REGISTRATION`, default false), `PUT /me/password` with cross-device session revocation, UI: RegisterView, profile dropdown, Change Password modal |
| 0.9.0-alpha.9 | 5b | Admin panel: `AdminUser` dep raising 403, `/admin/users` CRUD with anti-lockout (can't demote last admin, can't delete self), orphan-directory scan at lifespan, frontend AdminView |
| **0.9.0** | — | **Multi-user release cap** — docs + README "Multi-User" section, no code change |
| (intermediate) | — | `fix(ci)`: Playwright smoke test updated for multi-user auth UX (login needs email, Logout is now inside profile dropdown) |
| **0.9.1** | — | **8-fix email scanner + classifier hardening bundle** |
| **1.0.0 (uncommitted)** | — | **Fix 8: per-email scan summary UX** |

---

## v0.9.1 Details (currently live)

Shipped 8 fixes from the 2026-04-20 investigation:

1. **Frontend `formatScanState()` account-type-aware**: Outlook accounts now show "8 folders · last synced 2026-04-24 14:47:17" instead of the confusing "8 folders · 0 messages · UID unknown". The old formatter assumed IMAP/QQ `{folder: {uid, uidvalidity, messages}}` JSON shape; Outlook uses `{folder_id: "ISO_timestamp"}`.
2. **7 new Chinese keywords** in `INVOICE_KEYWORDS`: `购物凭证`, `消费凭证`, `购物发票`, `订单完成`, `交易成功`, `支付凭证`, `發票` (traditional).
3. **Tier-1 body-scan window 200 → 2000 chars** in `email_classifier.py:140`.
4. **Outlook `includeHiddenFolders=true`** in `_iter_mail_folders()` — Microsoft Graph API omits Clutter, some Archive configurations without this flag.
5. **Outlook `receivedDateTime gt` → `ge`** — was silently excluding emails whose timestamp equals the watermark. Re-fetched boundary emails are deduped by the existing `_was_attachment_seen` check + composite `UNIQUE(user_id, invoice_no)`.
6. **Trusted-sender domain-aware match**: exact-email (`user@domain.com`) + domain-suffix (`@domain.com` or bare `domain.com`) + legacy substring fallback. **Closes substring-match spoofing vulnerability** — configured `billing@company.com` no longer accidentally trusts `notbilling@company.com`.
7. **LLM classify cache TTL 30d → 7d** in `ai_service.py:_cache_expiry`. Shorter window so single LLM false-negative on a recurring sender stops propagating within a week. Migration 0009's legacy 30-day backfill intentionally preserved for pre-upgrade entries.
8. **Migration 0014**: seeds 7 default trusted senders for Chinese invoice providers (qcloudmail.com, fapiao.jd.com, shuidi.com, noreply@invoice.alipay.com, inv.nuonuo.com, piaoyi.baiwang.com, eeds.chinatax.gov.cn). Additive-only — never overwrites an operator-configured value. Defensive against `app_settings`-created-by-`create_all` schema-lifecycle wart (same pattern as migrations 0011 and 0013).

Dry-run on production DB copy validated. Tests: 619 passing, 100% coverage (+14 from v0.9.0). Production on 0.9.1 since 2026-04-25 02:19.

---

## Production Snapshot

```
https://invoice-maid.helixzz.com/api/v1/health
{
    "status": "ok",
    "version": "0.9.1",
    "db": "ok",
    "scheduler": "running",
    "sqlite_vec": true,
    "invoice_count": 239,
    "last_scan_at": "2026-04-24T17:35:06.788726+00:00"
}
```

- **User count**: 1 (admin@local, user_id=1, the operator)
- **Storage layout**: per-user subdirectories under `/var/lib/invoice-maid/data/invoices/users/1/invoices/`
- **DB revision**: `0014_seed_default_trusted_senders`
- **Alembic**: all migrations 0001-0014 applied; `app_settings.classifier_trusted_senders` seeded with 7 defaults
- **`.env`**: `ALLOW_REGISTRATION=false` (production is single-user; registration closed)

---

## Explicitly Deferred (do NOT ship in v1.0.0)

### Delta Query rewrite (v1.1.0)
Replace per-folder `receivedDateTime` watermarking in `OutlookScanner.scan()` with Microsoft Graph `/me/mailFolders/{id}/messages/delta`. Oracle review (session `ses_23f60d060ffevhyBG6HQGYnePm`) flagged this as too high-blast-radius for a first 1.0 cut.

**Required scope for v1.1.0**:
- State payload versioning: `{"_format":"outlook_delta_v1","folders":{...}}` so future format changes are detectable
- Runtime legacy bridge: if `last_scan_uid` is still in the v0.9.x `{folder_id: ISO_timestamp}` form, do one bridge scan using `receivedDateTime ge {timestamp}` to drain the window, then switch to delta tokens for that folder going forward
- Handle 410 Gone / `resyncRequired` by transparently restarting the folder sync (fresh delta token)
- Respect the 5,000-message cap on delta with `$filter` (don't apply `$filter` — rely on the composite UNIQUE for dedupe)
- Operator kill-switch (env var or classifier-setting) to force-fallback to v0.9.x watermarking if delta produces anomalies

**Test strategy** (per Oracle): state-machine-oriented `(input state, fake Graph pages) → (emails, new state)` tests, using the existing `httpx.AsyncClient.get` monkeypatching pattern already present in `tests/test_services/test_email_scanner.py`. Do NOT introduce a new Microsoft Graph test fixture dependency.

### Other deferred items
- Per-user AI/classifier settings (instance-wide works fine for single-org deployments)
- Per-user webhooks (`WEBHOOK_URL` env var sufficient)
- Repository-pattern refactor (internal code organization — zero user-visible change, tenant filters already at every call site)

---

## Next Steps (Concrete, In Order)

### 1. Ship v1.0.0 (immediate, ~10 min)

```bash
cd /home/helixzz/invoice-maid

# Verify the uncommitted state matches expectations
git status --short
git diff backend/pyproject.toml | grep '^+\|^-'  # should show 0.9.1 → 1.0.0

# Commit
git add -A && git commit -m "$(cat <<'EOF'
feat: v1.0.0 stable multi-user release + per-email scan summary UX

Caps the v0.9.x arc with Fix 8 from the 2026-04-20 investigation —
the only user-visible change since v0.9.1.

Fix 8 (frontend-only):
- aggregateByEmail() groups extraction_logs by email_uid. A 2-email
  / 6-row Sam's Club invoice scan (2 emails × 3 attachments each)
  now renders as 2 cards instead of 6 rows.
- Each email card shows a best-outcome badge (saved > duplicate >
  skipped_seen > low_confidence > parse_failed > error > not_invoice)
  and a chip row with per-attachment outcomes for audit.
- Summary banner shows plain-language Chinese counts ("2 封邮件 ·
  保存 1 张新发票 · 去重 1 封") matching the user's mental model.
- duplicate badge tooltip explicitly states "Same invoice_no
  already exists — correctly deduped, no action needed",
  directly addressing the user confusion from the investigation.

No backend, no schema, no migration, no API change. 619 tests,
100% coverage (unchanged from v0.9.1). Frontend build clean
(93 KB gzipped).

Deferred to v1.1.0: Microsoft Graph Delta Query rewrite per Oracle
review — needs state versioning, legacy bridge, operator
kill-switch.
EOF
)"

# Tag and push
git push origin main
git tag -a v1.0.0 -m "Release v1.0.0 — stable multi-user"
git push origin v1.0.0

# Deploy (must use explicit --tag; upgrade script's sort isn't PEP 440 aware)
sleep 10  # let GitHub propagate the tag
sudo /usr/local/sbin/invoice-maid-upgrade --tag v1.0.0

# Verify
curl -sS https://invoice-maid.helixzz.com/api/v1/health | python3 -m json.tool
# expect: "version": "1.0.0"
```

### 2. Post-deploy smoke (~5 min)

- Log in as admin, go to Settings > Scan Operations, expand one of the recent scan logs that processed Sam's Club (scan #60 on 2026-04-20 is ideal — it has the 2-email / 6-row Sam's Club dedupe scenario)
- Verify the expanded panel shows "Summary (by email)" banner with Chinese plain-language counts
- Verify individual emails are now cards with chips inside, not flat rows
- Verify hovering the `duplicate` badge shows the tooltip

### 3. Watch for v0.9.1 regressions in a few days

Oracle recommended "soak v0.9.1 in production for a few days of scheduled scans" before shipping v1.0.0. You're technically skipping the soak by shipping v1.0.0 immediately, but v1.0.0 has **zero backend/scanner/classifier change** — it's a pure frontend template refactor. The soak concern was about the v0.9.1 scanner changes (`gt→ge`, `includeHiddenFolders=true`, trusted-sender refactor, LLM TTL); those are now running in production since 2026-04-25 02:19 and should be observed over 3-7 days:

- Check `scan_logs` table: does `emails_scanned` correlate with what you'd expect given the new `ge` filter + hidden folders?
- Check `extraction_logs` per scan: any new `error` outcomes after v0.9.1 that weren't there before?
- Watch for new `duplicate` outcomes — `gt→ge` intentionally re-fetches boundary emails on every scan, and the existing `_was_attachment_seen` check should catch them. If you see a SPIKE in `duplicate` rows (much more than ~1 per folder per scan), something's wrong with the dedupe path.

### 4. Plan v1.1.0 (Delta Query) — probably 2-4 weeks out

Read the Oracle report in session `ses_23f60d060ffevhyBG6HQGYnePm` for the full architecture. Key docs to re-read before starting:
- `backend/app/services/email_scanner.py:1145-1252` — current `OutlookScanner.scan()`
- `backend/tests/test_services/test_email_scanner.py` — 102 existing tests exercising Graph API via monkeypatched `httpx.AsyncClient.get`
- Microsoft Graph docs: delta query, token lifetime, 5,000-message cap

---

## Non-Obvious Operational Facts

1. **`invoice-maid-upgrade` tag sort is NOT PEP 440 aware**. Always pass `--tag vX.Y.Z` explicitly for alpha/beta/rc/post releases. Git's `--sort=-v:refname` treats `v0.9.0-alpha.8` as higher than `v0.9.1` alphabetically, so the default resolution picks the wrong tag after a prerelease sequence.

2. **`app_settings` table is NOT created by alembic**. It's born from `Base.metadata.create_all` at app startup. Any migration that touches it MUST be defensive via `inspector.get_table_names()` check (migrations 0011, 0013, 0014 all do this). Same applies to `saved_views` and `webhook_logs`.

3. **Migration 0009 backfill TTL (30 days) is intentionally different from current `AIService._cache_expiry` TTL (7 days)**. Legacy rows from pre-0009 aged out on their original schedule; new v0.9.1+ rows use the shorter window. The cross-file contract test `test_ai_service_cache_expiry_windows_match_migration_contract` now asserts 7 days for new rows; don't regress this.

4. **Trusted-sender substring fallback is intentional**. When the configured pattern doesn't look like an email (`user@domain`) or a domain suffix (`@domain.com`), fall through to legacy substring match. This preserves behavior for pre-v0.9.1 operator-configured patterns that don't fit the new shapes.

5. **`receivedDateTime ge` means we re-fetch one email per scan per folder**. That's correct behavior — the dedupe downstream handles it. Don't "optimize" back to `gt`.

6. **Frontend `dist/` IS committed to git**. The deploy script expects it. After any frontend change, run `cd frontend && npm run build` before committing. The dist/ commit rule is enforced by convention, not by CI.

7. **The Playwright smoke test is currently green** on GitHub CI as of `3df990f`. If you break it, you'll see CI red but production can still deploy (backend CI job is independent and green).

8. **Orphan-directory scan at boot** (from alpha.9): on service start, warns if `STORAGE_PATH/users/{id}/` exists on disk but no `users.id` row in DB. Production has 1 user; no warnings expected in current steady state.

---

## Test Suite Status

- **Backend**: 619 tests, 100% coverage, all passing (as of `3e35d97`)
- **Frontend unit**: no Vitest infrastructure exists; Fix 8 aggregation helpers are covered only by visual inspection
- **E2E**: Playwright smoke passes (login + invoice list + settings nav + logout via profile dropdown)
- **CI**: both backend and frontend jobs green on `main`

If you add Vitest for Fix 8 coverage: the three pure functions to test are `aggregateByEmail`, `summarizeEmails`, and the `OUTCOME_PRIORITY` map. Test cases: 1-email with 1 extraction, 1-email with 3 extractions (the Sam's Club shape), 2-emails with duplicate invoice_no across them (the current Sam's Club shape), empty extractions, missing email_uid (manual uploads).

---

## Files to Know

**Backend**:
- `backend/app/services/email_scanner.py` — 4 scanner implementations (IMAP, QQ, Outlook, POP3). OutlookScanner is the one with most recent changes (lines 1136-1289).
- `backend/app/services/email_classifier.py` — tier-1 heuristics. 144 lines, well-contained.
- `backend/app/services/ai_service.py` — tier-3 LLM + cache. `_cache_expiry()` TTLs at lines 80-97.
- `backend/app/tasks/scheduler.py` — orchestrates scan → classify → parse → dedupe → save → webhook. 966 lines.
- `backend/alembic/versions/0014_seed_default_trusted_senders.py` — last migration.

**Frontend**:
- `frontend/src/views/SettingsView.vue` — 1400+ lines, contains Account CRUD, AI settings, classifier settings, scan operations, and the Fix 8 per-email aggregation (uncommitted).
- `frontend/src/components/AppLayout.vue` — profile dropdown with Change Password modal + Admin link (admin-only visible).
- `frontend/src/views/AdminView.vue` — user management table.

**Ops**:
- `/etc/invoice-maid/invoice-maid.env` — production env vars. Contains `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH`, `JWT_SECRET`, `STORAGE_PATH=/var/lib/invoice-maid/data/invoices`, `ALLOW_REGISTRATION=false`.
- `/usr/local/sbin/invoice-maid-upgrade` — deploy script. Always pass `--tag` for pre-releases.
- `/var/lib/invoice-maid/data/invoices.db` + `.db-wal` + `.db-shm` — production SQLite DB with WAL mode.
- `/var/lib/invoice-maid/data/invoices/users/1/invoices/` — 239 invoice files, all owned by the sole admin user.

---

## Open Questions / Future Work

1. **Should v1.1.0 Delta Query be account-level opt-in?** Per Oracle's "Watch out for" note: "If large folders make initial delta bootstrap too slow, consider account-level rollout instead of global enablement." Production has 8 folders with the oldest watermark at `2016-05-18T06:44:05Z` — a full delta sync from that point could pull a LOT of email. Plan for per-account enable/disable.

2. **Does the `invoice-maid-upgrade` script need a PEP 440 sort fix?** Currently requires `--tag` for pre-releases. A proper fix is in the deploy tooling repo, not here. Low priority since it's a one-flag workaround.

3. **Scan-summary UX for manual uploads?** Fix 8 groups by `email_uid`. Manual uploads have `email_uid="manual:{scan_log.id}"` — each upload gets a unique synthetic UID, so each is its own card. Looks correct but hasn't been manually verified post-Fix 8.

4. **Should v1.0.0 also bump frontend badge "1.0"?** Currently README shows "v1.0.0" badge. If you want a "stable" badge, add one.

---

## Session Records (for future Oracle follow-ups)

- **v1.0.0 scope + Delta Query design**: `ses_23f60d060ffevhyBG6HQGYnePm` (Oracle, 1m 41s). Key verdict: "Do NOT ship Delta Query in the first v1.0.0 cut. Cut v0.9.1 with fixes 1-7+10, soak, then v1.0.0 with fix 8 + docs."
- **Classifier rules and LLM gap**: `ses_23f6b5db2ffeA4wr4XEO1F0VzK` (explore)
- **Email scanner Outlook classification path**: `ses_23f6bfd8bffeGMc71D0fC5yAbV` (explore)
- **Microsoft Graph API**: `ses_23f6b961dffeu8WtkBeSAnLV0k` (librarian)
- **Frontend scan-state display**: `ses_23f6bd18dffecHgTIuOvIhuue3` (explore)

Resume any of these with `task(session_id=..., load_skills=[], run_in_background=false, prompt="follow-up: ...")` to preserve full context.
