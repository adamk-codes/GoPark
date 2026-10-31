# Team briefs — start here

One brief per workstream. Each teammate opens Claude Code in their clone of the repo and pastes the kickoff prompt below, with their brief's file name.

| Brief | Owner | Folders you own |
|---|---|---|
| [P1-mobile.md](P1-mobile.md) | P1 Mobile | `apps/mobile/` |
| [P2-ops-backend.md](P2-ops-backend.md) | P2 Ops backend (integration owner) | `api/ops/`, `api/db.py`, `api/schema.sql`, `api/main.py`, `config/fusion.yaml`, `tools/simulator/`, `tools/demo.py` |
| [P3-rhythm.md](P3-rhythm.md) | P3 Rhythm engine | `rhythm/`, `api/rhythm_api/`, `config/rhythm.yaml` |
| [P4-data-dashboard.md](P4-data-dashboard.md) | P4 Data + Dashboard | `tools/generator/`, `dashboard/`, `docker-compose.yml`, `slides/` |
| [H-hardware.md](H-hardware.md) | Hardware helper (+ optional Claude session) | `edge/` |

## Kickoff prompt (paste into Claude Code)

```
You are one of four parallel builders on the GoPark Sense hackathon project.
Read docs/briefs/README.md (shared rules), then docs/briefs/<YOUR-BRIEF>.md, then
CONTRACT.md and the sections of ARCHITECTURE_PLAN.md your brief points to.
Work only in the folders your brief says you own, on the branch it names.
Start with the first unchecked task of the current phase, and tell me your plan
for that phase before writing code.
```

## Shared rules (every workstream)

1. **Contract first.** `CONTRACT.md`, `contract/schemas/*.json` and `contract/types.ts` define every shape that crosses a folder boundary. To change or add one, make a small PR touching only `contract/` + `CONTRACT.md` and get the owner's OK (API shapes: P2, rhythm outputs: P3, generator files: P4). Then update your code.
2. **Stay in your folders.** If you need something from another workstream, ask its owner (or leave a `TODO(P2): …` in your own code and mock it). Never edit another owner's folder "just to unblock".
3. **Branches.** Work on your branch (`feat/mobile`, `feat/ops`, `feat/rhythm`, `feat/data-dash`, `feat/edge`). Pull `main` often. Open a PR to `main` at least every ~4 hours, small and working. P2 merges and runs the smoke test after each merge.
4. **Never break `main`.** Before every PR, run:
   - `uv run pytest` (Python contract + your tests)
   - `npm --prefix dashboard run build` if you touched the dashboard
   - `npm --prefix apps/mobile run typecheck` if you touched the app
5. **Generated stuff stays out of git:** `data/`, `runs/`, `*.sqlite`, `node_modules/`, `keys/`. Regenerate with the README commands.
6. **TypeScript types:** after a contract change, run `npm run contract` in `dashboard/` and `apps/mobile/` to refresh the copies. Don't edit `src/contract.ts` by hand.
7. **Demo clock.** The API's "now" is a demo clock starting Mon 2026-11-16 07:50 Beirut time. Always use `db.now(conn)` (backend) or the `now` field from the API (frontends), never the real clock, for anything shown in the demo.
8. **Times.** API returns ISO 8601 with offset in `Asia/Beirut`. The DB stores UTC text. Lebanon switches between +03:00 (summer) and +02:00 (winter); never hard-code an offset.
9. **No secrets in git.** No passwords, API tokens or private keys in code or commits.
10. **Report status** at the end of each phase in your PR description: what works, what's mocked, what's next, anything you need from another owner.

## Timeline (36–48 h)
| Phase | Hours | Goal |
|---|---|---|
| P1 | 3–12 | Each part works on its own against the P0 contract |
| P2 | 12–24 | Offline, fusion, real engine + evaluation, main dashboard pages |
| P3 | 24–32 | Connect the challenges: priors → fusion, expected arrivals, driver mode, evaluation page |
| P4 | 32–40 | Field test, polish, slides, two demo rehearsals |
| Buffer | 40–48 | Fixes and sleep |

**Never cut** (protect these over everything else): attendant scan + offline outbox, alerts, simulator-driven auto check-in, pattern table + export, calibrated confidence + backtest vs baselines, the C2 → C1 prior link and the "expected soon" list.

## Open decisions (build so either answer works)
- **Detection hardware:** camera-first (old phones as entrance/overhead cameras) vs the hybrid kit (beacons + NFC). Signals from either arrive through `POST /signals` with different `source` values, so fusion and the apps don't change.
- **iPhone driver build:** depends on a Mac or Apple developer account. Until then, the driver demo targets Android; the attendant app must keep working in Expo Go on both.
