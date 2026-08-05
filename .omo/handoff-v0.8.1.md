# v0.8.1 Session Handoff — 2026-04-19

**Written at:** 2026-04-19T16:30 local (session end)
**Latest shipped version:** v0.8.1 (tag `v0.8.1`, commit `0b17914`)
**Production:** https://invoice-maid.helixzz.com (deployed, healthy)
**CI:** green on main for every release this session

---

## TL;DR for the next session

This session shipped **6 releases (v0.7.7 → v0.8.1)** that rescued a catastrophic correctness bug and delivered a measured 3.35× IMAP cold-scan speedup. Production is healthy and has an **in-flight incremental rescan** running on QQ Mail (log 47, started 14:18). Let it finish or restart it — no state will be lost because of v0.7.7's orphan-cleanup and v0.7.8's per-folder state preservation.

**Do NOT ship more v0.8.x code changes without first verifying the in-flight QQ rescan is complete** — doing so will interrupt it for a fifth time and push back the user's invoice recovery.

---

## What shipped this session

| Tag | Theme | Why | Key measurement |
|---|---|---|---|
| `v0.7.7` | Scan fault isolation + orphan log cleanup | QQ `MailboxFetchError` mid-iteration killed entire account scan; orphan `scan_logs` rows accumulated until service restart | Transient errors now log per-folder warning, scan continues; orphans stamped at every scan start |
| `v0.7.8` | STATUS pre-flight + folder telemetry + TCP keepalive + partial-state preservation | Large-mailbox scans were opaque, slow on unchanged folders, and dropped partial progress on session close | Unchanged folders now 1 STATUS round-trip instead of full SELECT+FETCH; folder-level telemetry fields published |
| `v0.7.9` | Frontend surfaces v0.7.8 telemetry | Backend was publishing folder progress but Vue dashboard didn't render it | Users now see "Folder 3/22 · INBOX · fetching ~36k msgs" during live scans |
| `v0.7.10` | **CRITICAL HOTFIX** — hydrate before tier-1 classify | QQ IMAP had saved **0 invoices from 103,732 emails** because tier-1 classifier rejected unhydrated emails, blocking hydration (Catch-22). Production data proof: scan #26 had 100% `not_invoice` outcome | Outlook (`bodyPreview` at scan time) was unaffected; fix unblocks all IMAP accounts |
| `v0.8.0` | 4-connection IMAP parallel fetch + account page UX | QQ IMAP per-IP throughput caps ~32 msg/s. Single conn does 9.6 msg/s. Measured 2/3/4/5 conn benchmarks showed 4 is sweet spot. Separately: `last_scan_uid` rendered as 491-char raw JSON on Settings page | **3.35× cold-scan speedup** on QQ (9.6 → 32.2 msg/s); account UI shows `5 folders · 47,849 messages · UID 40993` instead of JSON blob |
| `v0.8.1` | URL pre-download filter (tracking pixels, unsubscribe, images, HTML Content-Type) | Live v0.7.10/v0.8.0 rescan showed 282 errors/scan from downloading `linktrace.triggerdelivery.com` etc. as fake PDFs | Expected 80-90% reduction in per-scan error count; closes a ROADMAP known-issue |

**Cumulative test state:** 411 tests, 100% coverage (verified both locally and CI-simulated by renaming `backend/.env` and running with only the 5 required env vars).

---

## Production state at handoff time

- **Health:** `GET /api/v1/health` returns `version=0.8.1 status=ok db=ok scheduler=running sqlite_vec=true invoice_count=231`
- **Invoices:** 231 total (was 208 pre-v0.7.10). Recovery is **partial** — the v0.7.10 fix should eventually yield ~580+ invoices on QQ once INBOX completes
- **Breakdown:**
  - 200 × 电子发票（普通发票）
  - 25 × 电子发票（增值税专用发票）
  - 6 × 增值税电子普通发票
  - All `extraction_method=llm` (expected — Chinese 数电票 parsing via LLM)
  - avg_confidence = 0.95
- **In-flight scan:** `log_id=47`, QQ account, started 14:18 local, on folder 1/23 (INBOX, ~35,624 msgs), 9+ min elapsed at handoff. STATUS pre-flight said INBOX changed (some new messages arrived since last successful scan), so it's doing full pass-1 fetch with 4-conn parallelism
- **Active IMAP connections to `imap.qq.com`:** ~4 sockets, each 2 MB sent / 680 KB received (worker pool grinding). No rate-limit errors
- **APScheduler:** fires `scan_all_accounts()` every 60 min. Next scheduled run is independent of in-flight user-triggered scan (they share `_scan_lock` so only one runs at a time)

---

## Outstanding work / known gaps

### In-flight (do not disturb)

- **QQ INBOX rescan on v0.8.1** — log 47 is running. It will eventually either finish (~20–40 min from handoff) or hit QQ rate limits. Either way, v0.7.7 orphan cleanup handles stamping.

### Likely next v0.8.2 (small, well-scoped)

1. **`parseScanState()` unit test** — the helper was added to `SettingsView.vue` in v0.8.0 but is not yet covered by frontend tests (we don't have Vitest/Jest set up). Consider adding minimal frontend tests or at least a manual QA checklist. Low risk without test coverage because it's a pure function called only in template.
2. **Nuonuo CDN workaround** — another ROADMAP known-issue: `nnfp.jss.com.cn` download links return HTML QR-code pages, not PDFs. v0.8.1's Content-Type filter correctly rejects them but the user still loses those invoices. Options: (a) document as "attach PDF to email" user guidance, (b) use headless browser to render + save PDF, (c) call nuonuo open API if one exists.
3. **SQLite `database is locked` under `EMAIL_CONCURRENCY=50`** — surfaces more during full rescans. Two options: (a) lower IMAP-specific concurrency, (b) switch DB to WAL mode with busy-timeout tuning. Likely (b) is the right answer.
4. **GitHub Actions Node 20 deprecation** — workflows emit deprecation warnings. Bump `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4`, `actions/upload-artifact@v4` to latest tags.

### Medium-effort (v0.8.x)

5. **Dark mode toggle** (user-requested, commit `73de390` pre-committed the ROADMAP entry). Tailwind `dark:` variants across `InvoiceList.vue`, `InvoiceDetail.vue`, `SettingsView.vue`, `ScanProgressBar.vue`. ~4-6 hours estimate.
6. **Parallel-worker interim progress** — v0.8.0's parallel workers don't publish `folder_fetch_msg` updates from inside their threads (the scheduler's `_publish` closure is called from the main thread only, not workers). This makes the progress bar look "frozen" during the 10-20 min INBOX fetch. Either (a) thread each worker with `asyncio.run_coroutine_threadsafe` to main loop (complex), or (b) accept the trade-off and show a more informative "N parallel workers running" message. Option (b) is simpler and honest.

### Large/deferred (v0.9.x+)

7. **mbsync + Dovecot local cache** (librarian research in earlier session confirmed **150-500× repeat-scan speedup**). Architecture: `mbsync` syncs QQ → local Maildir on a timer, local Dovecot exposes on `127.0.0.1:143`, invoice-maid points at localhost. ~4 hours setup. Requires deploying two new processes.
8. **Custom SQLite ingestor** — skip the Dovecot IMAP re-serving layer entirely; write directly to invoice-maid's SQLite cache. 2-3 days dev but cleanest integration.
9. **EAS/ex.qq.com experiment** — Tencent officially runs EAS on `ex.qq.com` (confirmed HTTP 401 live probe). WBXML protocol, no mature Python library. **Unknown** if QQ's account-level rate limit applies to EAS the same as IMAP — this is the only untested variable that might beat the 32 msg/s ceiling. ~1 week spike to answer.

---

## Critical context a new session must know

### 1. The v0.7.10 Catch-22 is THE story of this session

The ordering defect in `scheduler._process_single_email` (old `scheduler.py:263-273`) was:
```python
t1 = classifier.classify_tier1(email_data)  # runs on unhydrated data
needs_full_body = ... and (t1 is None or t1.is_invoice)  # blocks on False
if needs_full_body:
    email_data = await scanner.hydrate_email(account, email_data)
    t1 = classifier.classify_tier1(email_data)
```

For IMAP emails that arrive with `body_text=""`, `attachments=[]` (the v0.7.2 lazy-fetch design), tier-1 at `email_classifier.py:132-135` returned `ClassificationResult(False, 1, 0.90, "no content or keywords")`. The gate `(t1 is None or t1.is_invoice)` evaluated False → hydration never ran → email discarded as not_invoice.

**Outlook was unaffected** because `OutlookScanner.scan()` populates `body_text=bodyPreview` at list-time (`email_scanner.py:864`). IMAP has no equivalent preview.

v0.7.10 fix is **5 lines**: always hydrate unhydrated emails before tier-1. The Catch-22 had been present since v0.7.2 but only became visible with v0.7.9's folder telemetry exposing the counter disparity.

### 2. Benchmark truth for QQ IMAP

Ran a full benchmark harness (committed as `backend/scripts/benchmark_imap_scan.py`, untracked — decide whether to commit or move to docs). Results on production QQ INBOX (1000 msgs, live):

| Config | Wall clock | Rate | Speedup |
|---|---|---|---|
| 1-conn baseline | 104.0 s | 9.6 msg/s | 1.00× |
| 2-conn parallel | 43.6 s | 22.9 msg/s | **2.38×** |
| 3-conn parallel | 35.8 s | 28.0 msg/s | **2.92×** |
| **4-conn parallel (v0.8.0)** | **31.0 s** | **32.2 msg/s** | **3.35×** |
| 5-conn | 31.6 s | 31.6 msg/s | 3.29× (plateau) |
| 1-conn pipelining | 62.7 s | 15.9 msg/s | 1.66× |
| 4-conn + pipelining | 151.7 s | 13.2 msg/s | **0.39× (REGRESSES)** |
| Minimal `HEADER.FIELDS` | ≈ same as full HEADER | ≈ no change | ≈ 1× (server is processing-bound, not bandwidth-bound) |

**Honest conclusion:** QQ's ceiling is ~32 msg/s per-IP-per-account. No IMAP trick beats it. Real 100×+ speedup requires local cache (mbsync) or untested EAS.

### 3. Version cadence enforced by `AGENTS.md`

- Every commit batch that changes observable behavior MUST bump `backend/pyproject.toml`, add CHANGELOG entry, add ROADMAP entry, tag `vX.Y.Z`, push the tag, verify CI.
- `invoice-maid-upgrade` deploy script at `/usr/local/sbin/invoice-maid-upgrade` runs alembic + systemctl restart + health-check.
- Production JWT is minted inside the service user:
  ```bash
  sudo bash -c 'set -a; source /etc/invoice-maid/invoice-maid.env; set +a; cd /var/lib/invoice-maid/src/backend && /var/lib/invoice-maid/venv/bin/python -c "from datetime import timedelta; from app.services.auth_service import create_access_token; print(create_access_token({\"sub\":\"admin\"}, expires_delta=timedelta(hours=6)))"'
  ```

### 4. CI gotchas

Two CI issues were fixed earlier in session:
1. **Missing env vars** — 5 tests instantiate `Settings` at runtime without the `settings` fixture. Fixed by injecting env vars as step-level `env:` in `.github/workflows/ci.yml`.
2. **`TYPE_CHECKING` coverage drift** — coverage.py version differences caused `app/config.py:12` and `app/models/email_account.py:10` to count uncovered on CI but not locally. Fixed by adding `if TYPE_CHECKING:` to `.coveragerc` `exclude_lines`.

Commit `7b4f057` fixed both. Now CI mirrors local: 411 tests, 100% coverage.

---

## File locations (new/changed this session)

### Backend
- `backend/app/services/email_scanner.py` — STATUS pre-flight, keepalive, partial-state preservation, `IMAP_FETCH_WORKERS=4`, `IMAP_PARALLEL_THRESHOLD=500`, `_fetch_folder_worker`, folder telemetry via `progress_callback`
- `backend/app/services/scan_progress.py` — new fields `total_folders`, `current_folder_idx`, `current_folder_name`, `folder_fetch_msg`
- `backend/app/tasks/scheduler.py` — **v0.7.10 hotfix** (unconditional hydrate-before-classify), orphan cleanup at scan start, `_is_blocked_download_url`, `LINK_HOST_BLOCKLIST`, `LINK_PATH_BLOCKLIST_SUBSTRINGS`, `ACCEPTABLE_INVOICE_CONTENT_TYPES`
- `backend/scripts/benchmark_imap_scan.py` — **UNTRACKED.** Standalone benchmark harness used to measure QQ throughput. Has a module docstring explaining safe operator usage. Decide in next session: commit under `backend/scripts/` or move to `docs/benchmarks/`

### Frontend
- `frontend/src/views/SettingsView.vue` — `parseScanState()` + `formatScanState()` helpers, replaces raw JSON render with compact summary
- `frontend/src/components/ScanProgressBar.vue` — renders new folder telemetry fields
- `frontend/src/composables/useScanProgress.ts` — `ScanProgressData` extended, `statusLine` computed composes folder+msg
- `frontend/dist/` — rebuilt and committed

### Docs
- `CHANGELOG.md` — entries for v0.7.7 through v0.8.1
- `ROADMAP.md` — per-release summaries, known-issues table with v0.8.1 resolution marked, v0.8.0+ deferred research list
- `README.md` — version badge updated to 0.8.1

### Untracked files to deal with
```
.playwright-mcp/              # browser testing artefacts, safe to delete
01-invoices-page.png           # screenshot from playwright session
02-scan-progress.png
03-scan-completed.png
backend/data/                  # local dev DB directory (in .gitignore? verify)
backend/scripts/               # benchmark tool, decide commit path
```

---

## Resume instructions for the new session

### First 5 minutes: orient

```bash
# 1. Check git state
cd /home/helixzz/invoice-maid
git log --oneline -5
git status
git stash list

# 2. Check production health
curl -sS https://invoice-maid.helixzz.com/api/v1/health | python3 -m json.tool

# 3. Check in-flight scan state
TOKEN=$(sudo bash -c 'set -a; source /etc/invoice-maid/invoice-maid.env; set +a; cd /var/lib/invoice-maid/src/backend && /var/lib/invoice-maid/venv/bin/python -c "from datetime import timedelta; from app.services.auth_service import create_access_token; print(create_access_token({\"sub\":\"admin\"}, expires_delta=timedelta(hours=6)))"' | tail -1)
curl -sS -H "Authorization: Bearer $TOKEN" https://invoice-maid.helixzz.com/api/v1/scan/progress | python3 -m json.tool
curl -sS -H "Authorization: Bearer $TOKEN" "https://invoice-maid.helixzz.com/api/v1/scan/logs?size=5" | python3 -m json.tool
curl -sS -H "Authorization: Bearer $TOKEN" https://invoice-maid.helixzz.com/api/v1/stats | python3 -m json.tool
```

If `phase=done` and `invoice_count > 231`, v0.7.10 recovery is successful. If `invoice_count` is around 600+, the full QQ recovery landed.

### Deciding what to work on

Ask the user, but reasonable defaults in priority order:

1. **Verify QQ recovery** — first rescan under v0.7.10/v0.8.0/v0.8.1 should yield ~580+ invoices (extrapolated from Outlook's 0.56% ratio × 103k QQ emails). If the count is lower, something is blocking — investigate.
2. **Small wins** — GitHub Actions Node 20 bump + SQLite WAL busy-timeout are both < 1-hour each and close ROADMAP known-issues. Good first delivery of a fresh session.
3. **Dark mode** — user requested, low risk, ~4 hours.
4. **Nuonuo CDN workaround** — medium effort, real user value for Chinese e-invoice users.
5. **mbsync + Dovecot cache** — the 150-500× repeat-scan win the user explicitly requested in "continue to find ways to optimize IMAP." Architecturally larger but clean.

### What NOT to touch without confirmation

- The hydrate-before-classify ordering (v0.7.10 fix). Messing with this reintroduces the Catch-22.
- The `IMAP_FETCH_WORKERS=4` constant. 5+ plateaus and risks tripping QQ rate limits; 3 or fewer loses measured throughput.
- The `LINK_HOST_BLOCKLIST`/`LINK_PATH_BLOCKLIST_SUBSTRINGS` content — they correctly reject trackers and correctly accept `fapiao.jd.com` / `nnfp.jss.com.cn`. Adding more hosts requires reproducing the scan-error log first to confirm they're real false positives.

### Verification gate before any new release

1. `pytest --cov=app --cov-fail-under=100 -q` — must be green
2. CI-simulated: `mv backend/.env backend/.env.tmp && DATABASE_URL=... pytest ... && mv backend/.env.tmp backend/.env` — must be green
3. `npm run build` in `frontend/` — must be clean
4. `sudo /usr/local/sbin/invoice-maid-upgrade` — must end with `upgrade OK: vX -> vY`
5. `curl /api/v1/health` returns `status=ok version=<new>` — must match

---

## Open questions / decisions for user

1. **Commit `backend/scripts/benchmark_imap_scan.py`?** It's a useful ops tool (reproducible perf measurements). Suggest commit under `backend/scripts/`. Downside: adds ~500 lines to the repo.
2. **Clean up the `.playwright-mcp/` + PNG screenshots?** They were playwright session artefacts. Safe to delete.
3. **When to ship v0.9.0?** If user wants mbsync cache, that's a clean v0.9.0 milestone. If they want more v0.8.x patch releases first, proceed with the small wins (Node 20, WAL, Nuonuo, dark mode).

---

## Session meta

- **Total commits this session:** 6 releases + 1 CI fix + 1 docs-only = **8 tagged commits** + 2 intermediate commits.
- **All CI runs on `main` since session start:** green.
- **Production deploys:** v0.7.6 → v0.7.7 → v0.7.8 → v0.7.9 → v0.7.10 → v0.8.0 → v0.8.1. **Zero rollbacks.**
- **Tests grew:** 393 → 411 (+18 regression/coverage tests).
- **Coverage:** 100% throughout (with justified `# pragma: no branch` / `# pragma: no cover` annotations where documented in CHANGELOG).

All changes are in `main`, tagged, pushed, and deployed. Nothing in a stash, nothing uncommitted except the benchmark tool + screenshots.
