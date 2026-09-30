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

- **Adopted book:** overlay **sub** is the performer (PF 1.86 paper). Cover-us is the cash floor. Never BTO `admin_alert_ledger`. Never exec-queue an opposite (`queue_opposite=false`) unless overlay itself queued that send. Flatten `take_exit=ladder_to_market` (not bid).
- `admin_n >= sub_n`. Plot `sub_alert_send`. `admin_rows=admin_alert_ledger`. `aligned_copy=false`.
- Starter BTO only after `before_bto` returns `post=True` with `plot=sub_alert_send`.
- Envelope flatten sets `override_trail=True` and `engine_exit_mode=ladder_to_market` (never hold a loser on trail).
- Halt is sticky for the ET date; `recover_lost` never BTOs. `/health` must not show `engine_exit_mode=trail`.
- **9/29 learn (wired at BTO, not just overlay):**
  - Refuse CHOPPY (do **not** `chop_size`). Weak pre-move $0.15–$0.29. Strong pre-move ≥ $0.50. Chase ≥ $0.50.
  - Missing `pre_move_spy` / `chase_spy` is `skip_quality_unknown` (fail closed).
  - First-line halt: −$500 session cash **or** 4 consecutive FAILs, even if halt was never lifted.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

Paste into the `deltaiq-signal-engine` production agent:

```
Copy engine/shared/gates.py, engine/tradier_exec/hooks.py, engine/overlay/publish.py
from getdeltaiq/deltaiq-website PR (cover-us gates, 9/29 learn) onto production.

Wire before_bto with plot=sub_alert_send, overlay_queued, is_opposite,
choppy, on_arm_bar, pre_move_spy, chase_spy, regime, same_dir_age_sec,
and a Postgres persist= that INSERT ON CONFLICT DO NOTHING
into consumed_sends. Boot SELECT + state.load_consumed. Never DELETE on flatten.

Admin ledger is not a BTO source. queue_opposite=false unless overlay_queued.
take_exit=ladder_to_market (never bid). on_manage flatten disables trail.

Proof on /health: engine_exit_mode=ladder_to_market, take_exit=ladder_to_market,
bto_source=sub_alert_send, queue_opposite=false, chop_size=false,
skip_strong_pre_move=true, consumed_send_ts after a starter, skip_quality_n.
```


## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates engine.tradier_exec.tests.test_runtime engine.tradier_exec.tests.test_week_replay
python3 -m engine.tradier_exec.replay
python3 -m engine.tradier_exec.week_replay
```

Last-week tape (9/23–9/29, no quality flags — those were not on BTO): envelope + first-line halt cuts the 9/23 14-lot 770C and the 9/29 0-for-N grind. Halt resets on the ET date so 9/24–9/28 still trade. Winners *after* halt on a red tape are not kept.
