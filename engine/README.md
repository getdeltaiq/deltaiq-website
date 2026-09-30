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
- **STC only when `on_manage`/`before_stc` returns `flatten=true`.** HOLD ticks use `take_exit=hold` and `post_stc=false`. Never rest a bid STC on BTO fill (9/30 10:39 10-lot 767-call scratch).
- Halt is sticky for the ET date; `recover_lost` never BTOs. A fill younger than 15s is not an orphan. `/health` must not show `engine_exit_mode=trail`.
- **9/29 learn (wired at BTO, not just overlay):**
  - Refuse CHOPPY (do **not** `chop_size`). Weak pre-move $0.15–$0.29. Strong pre-move ≥ $0.50. Chase ≥ $0.50.
  - Missing `pre_move_spy` / `chase_spy` is `skip_quality_unknown` (fail closed).
  - First-line halt: −$750 session cash (3 envelope misses) **or** 4 consecutive **0DTE** FAILs, even if halt was never lifted. 1DTE FAILs do not increment the streak (9/28 1DTE wiggles had halted before the 0DTE 767-put).
  - Extra BTO on RUN: fill remaining room to 16 when MFE ≥ $0.20 (`before_extra_bto`).
  - Misfire: 8-minute cooldown after any FAIL (`skip_cooldown_after_fail`). 1DTE starters require overlay `regime=TREND` (`skip_1dte_not_trend`); CHOPPY/RANGE/missing is a refuse.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

Paste into the `deltaiq-signal-engine` production agent:

```
Copy engine/shared/gates.py, engine/tradier_exec/hooks.py, engine/overlay/publish.py
from getdeltaiq/deltaiq-website PR 24 onto production. Rec book is the live book
(rec_book_ship=2026-09-30-stc-hold). Overwrite leftover Railway knobs:
queue_opposite=false, take_exit=ladder_to_market on FLATTEN only.
HOLD ticks must set take_exit=hold and post_stc=false. Session cash halt is −$750
(3 envelope misses), not −$500. Do not keep a parallel knob set.

9/30 10:39 bug: BTO 10-lot 767-call @ $1.84 then STC @ $1.83 in ~3s.
That was native bid-exit on fill / take_exit on HOLD. Rec envelope needs
$0.15 option down or 90s FAIL. Fix:
- Call state.on_bto_fill(qty, fill_px) on fill. Do NOT post STC there.
- Do NOT rest a working bid/ask STC on entry (book.take_exit leftover
  "bid" is ignored). working_stc_on_fill=false. stc_on_bto_fill=false.
- Every STC must go through before_stc. If post is False, cancel working
  STC and return. Only when flatten=true walk bid / bid-0.05 / bid-0.10 / MARKET.
- recover_lost must not STC a fill younger than 15s (fresh_fill).
- Clamp seconds_since_fill to this ticket's fill clock.

Wire before_bto with plot=sub_alert_send, overlay_queued, is_opposite,
choppy, on_arm_bar, pre_move_spy, chase_spy, regime, same_dir_age_sec,
option_symbol, and a Postgres persist= that INSERT ON CONFLICT DO NOTHING
into consumed_sends. Boot SELECT + state.load_consumed. Never DELETE on flatten.

on_manage: if extra_bto, POST remaining room to 16 (not a second starter).
Disable Railway extra_bto_on_run native. 1DTE without TREND is skip_1dte_not_trend.
Missing option_symbol is skip_dte_unknown. 8-minute cooldown after FAIL.
on_flatten(..., dte=option_dte(symbol, session_date), et_hhmm=).
1DTE must not increment consecutive_fail_n.

Proof on /health (must match rec_book()): rec_book=true,
rec_book_ship=2026-09-30-stc-hold, session_loss_halt_usd=750,
engine_exit_mode=ladder_to_market,
take_exit=ladder_to_market, hold_exit=hold, stc_requires_envelope=true,
stc_on_bto_fill=false, working_stc_on_fill=false, before_stc=true,
bto_source=sub_alert_send, queue_opposite=false,
chop_size=false, fail_streak_0dte_only=true, extra_bto=true,
extra_bto_fill_to=16, skip_1dte_not_trend=true, cooldown_after_fail_sec=480,
consumed_send_ts after a starter, skip_quality_n, skip_misfire_n.
After a BTO fill: last_action=bto_fill, post_stc=false until envelope.
A 1-cent scratch in 3s must not happen.
```


## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates engine.tradier_exec.tests.test_runtime engine.tradier_exec.tests.test_week_replay
python3 -m engine.tradier_exec.replay
python3 -m engine.tradier_exec.week_replay
```

30 trading days through 9/29, weekly Rec vs live: `python3 -m engine.tradier_exec.week_replay`.
Rec tape is 9/15–9/29 (envelope + 0DTE fail-streak + extra BTO on RUN + 1DTE skip
unless TREND). Earlier weeks have no SPY lots; Rec equals live (impact $0).
Halt resets on the ET date.
