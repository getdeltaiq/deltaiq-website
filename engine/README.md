# Engine (GitHub source of truth)

This conversation **edits these files**. Railway must import them. Do not patch overlay or `tradier_exec` on Railway as a second copy.

## Layout

| Path | What this agent changes |
|---|---|
| `engine/overlay/publish.py` | Admin ledger (100% of candidates) vs sub send (actionable subset). SMS + Tradier read **sub only**. |
| `engine/shared/gates.py` | Tradier size, consume-once, bounce skip, envelope, halt, STC ladder → market |
| `engine/tradier_exec/hooks.py` | `before_bto` / `on_manage` / `/health` proof keys (`gates_module=engine.shared.gates`) |

`exec/` remains a compatibility shim + shore-up installer for the live Railway process until it imports `engine.*` directly.

## Contract

- `admin_n >= sub_n`. Plot `sub_alert_send`. `admin_rows=admin_alert_ledger`. `aligned_copy=false`.
- Starter BTO only after `before_bto` returns `post=True`.
- Envelope flatten sets `override_trail=True` and `engine_exit_mode=ladder_to_market` (never hold a loser on trail).
- Halt is sticky for the ET date; `recover_lost` never BTOs. `/health` must not show `engine_exit_mode=trail`.
- **9/29 learn (wired at BTO, not just overlay):**
  - Refuse CHOPPY (do **not** `chop_size`). Weak pre-move $0.15–$0.29. Strong pre-move ≥ $0.50. Chase ≥ $0.50.
  - Missing `pre_move_spy` / `chase_spy` is `skip_quality_unknown` (fail closed).
  - First-line halt: −$500 session cash **or** 4 consecutive FAILs, even if halt was never lifted.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

Paste into the `deltaiq-signal-engine` production agent:

```
Copy engine/shared/gates.py, engine/tradier_exec/hooks.py, engine/overlay/publish.py
from getdeltaiq/deltaiq-website PR (cover-us gates, 9/29 learn) onto production.

Wire before_bto with choppy, on_arm_bar, pre_move_spy, chase_spy, regime,
same_dir_age_sec, and a Postgres persist= that INSERT ON CONFLICT DO NOTHING
into consumed_sends. Boot SELECT + state.load_consumed. Never DELETE on flatten.

on_manage: if flatten/override_trail/ignore_trail, set trail_armed=False and
engine_exit_mode=ladder_to_market (never trail). Walk STC bid / bid-0.05 /
bid-0.10 / MARKET. Call on_flatten(realized, FAIL|RUN) so consecutive_fail_n
moves. apply_broker_session_cash from Tradier option cash.

chop_size=False. CHOPPY is a refuse. Keep halt on for 2026-09-29.
Do not place/modify/cancel orders from the agent.
Proof on /health: engine_exit_mode=ladder_to_market, consumed_send_ts after a
starter, skip_quality_n, chop_size=false, protect_fills_n on $0.15 down.
```


## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates engine.tradier_exec.tests.test_runtime
python3 -m engine.tradier_exec.replay
```
