# Handoff — current status

> **Read this first** if you're a new Claude Code / Cursor session picking up the work.
> Last updated: 2026-10-31, during phase 1 (parallel build in progress).
> Repo: https://github.com/adamk-codes/GoPark · default branch `main`

## 1. What this project is (30 seconds)
One system for both GoPark × AUB hackathon challenges:
- **C1:** attendant app + automatic arrival/departure detection.
- **C2:** "Rhythm", the booking pattern engine.

C2's predictions feed C1's detection and the "expected soon" list.

Source-of-truth docs:
- `ARCHITECTURE_PLAN.md`: design.
- `CONTRACT.md`: every shared shape and endpoint.
- `docs/briefs/`: one brief per workstream, with phase checklists.
- `README.md`: run commands.

## 2. Done (on `main`)
| Commit | What |
|---|---|
| `8b6b03a` | **Phase 0:** contract + schemas + TS types, config (lots, holidays, fusion, rhythm), generator v0 (3 personas), Rhythm baseline B2, FastAPI ops + rhythm endpoints (scan/check-in/out, overview with predictions, alerts on the fly, audit, signals, basic sync, demo clock), React dashboard (live lots, pattern table), Expo attendant skeleton, 10 contract tests |
| `c1ec1ce` | `docs/briefs/`: shared rules, kickoff prompt, briefs P1–P4 + H |
| *(this file)* | `HANDOFF.md` |

Verified at P0: `uv run pytest` → 10 passed; dashboard builds; mobile type-checks (never run on a device).

## 3. In progress: phase 1 (hours 3–12 of each brief)
Four Claude Code sessions, each in its own git worktree and **local** branch (not pushed). Each was told to commit on its branch, not push, not merge.

| Workstream | Brief | Branch (local, name starts with) | Scope this phase |
|---|---|---|---|
| P2 Ops backend | `docs/briefs/P2-ops-backend.md` | `…feat-ops-p1…` | **Contract additions first** (`/bookings/{id}/qr`, `/walk_ins`, `/alerts/{id}/resolve`, `/stays/{id}/evidence`, `/dev/gate/{lot_id}`, `Alert` schema), Ed25519 QR signing, gate code, walk-ins, persisted alerts, sync cursor + anomalies |
| P3 Rhythm | `docs/briefs/P3-rhythm.md` | `…feat-rhythm-p1…` | `rhythm/stages/` clean, activity, discover, habit; real `_patterns()`; `rhythm evaluate` vs B1/B2/B3 |
| P4 Data + Dashboard | `docs/briefs/P4-data-dashboard.md` | `…feat-data-dash-p1…` | 8 more personas + 2 hidden (gitignored), capacity, `--messy`, driver traces; dashboard design pass, filters, drill-down |
| P1 Mobile | `docs/briefs/P1-mobile.md` | `…feat-mobile-p1…` | Expo Router tabs, settings, camera QR scan, overview, history (mocks for P2's new endpoints) |

Find the branches with `git branch --list "*feat-*"` (the app may add a prefix/suffix to names). Each session's worktree is listed by `git worktree list`.

## 4. Next steps (in order)
1. **Merge phase-1 branches into `main`**, in this order to limit conflicts: ops (contract changes) → rhythm → data-dash → mobile. For each:
   ```bash
   git merge --no-ff <branch>
   ```
   Then run `uv run pytest`, `npm --prefix dashboard run build` and `npm --prefix apps/mobile run typecheck`.
   - Conflicts are most likely in `dashboard/src/contract.ts` / `apps/mobile/src/contract.ts` (regenerate with `npm run contract`) and `.gitignore`.
2. If P2 changed the contract, refresh the copies (`npm --prefix dashboard run contract`, `npm --prefix apps/mobile run contract`) and replace the mobile/dashboard mocks with the real endpoints.
3. Regenerate data with the new generator, re-run the engine and evaluation:
   ```bash
   uv run python -m tools.generator --users 300 --weeks 26 --seed 42
   ```
   ```bash
   uv run python -m rhythm run
   ```
   ```bash
   uv run python -m rhythm evaluate
   ```
   Then `POST /dev/reset` on the API.
4. Push `main`, update this file (section 2 + 3).
5. Start **phase P2** of each brief (fusion + simulator; offline outbox; drift/calibration/reminders; detection log + evaluation page). The kickoff prompt is in `docs/briefs/README.md`.

## 5. Open decisions (ask the user)
- **Detection hardware:** camera-first (recommended; old phones as cameras) vs hybrid kit (BLE beacons + NFC). The code accepts both through `POST /signals`.
- **iPhone driver build:** needs a Mac or Apple developer account. Otherwise the driver demo is Android-only.
- Presentation length / whether a live demo is required.

## 6. How to resume (any tool)
```bash
git clone https://github.com/adamk-codes/GoPark.git
```
```bash
uv sync
```
```bash
uv run python -m tools.generator
```
```bash
uv run python -m rhythm run
```
```bash
uv run uvicorn api.main:app --reload --port 8000
```
```bash
npm --prefix dashboard install
```
```bash
npm --prefix dashboard run dev
```
Then read `docs/briefs/README.md` and the brief for the workstream you're continuing. Follow the shared rules there (contract-first, stay in your folders, tests before merge).

## 7. Housekeeping rule
Before any session runs out of context it must update this file (done / in progress / next, with exact commands) and commit + push it. Never push code that breaks `main`; describe unfinished work here instead.
