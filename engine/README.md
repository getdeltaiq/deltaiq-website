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
  - Misfire: 8-minute cooldown after any FAIL (`skip_cooldown_after_fail`). Before **12:45 ET** 1DTE starters require overlay `regime=TREND` (`skip_1dte_not_trend`). From **12:45** the book is 1DTE: trade next-day OCC, refuse leftover 0DTE (`skip_0dte_after_cutover`). Do not freeze 0DTE before cutover.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

Paste into the `deltaiq-signal-engine` production agent:

```
Copy engine/shared/gates.py, engine/tradier_exec/hooks.py, engine/overlay/publish.py
from getdeltaiq/deltaiq-website PR 24 onto production.
rec_book_ship=2026-09-30-path-bind. Do not lift today's halt.

PATH BIND — flags are on /health; native still bid-scratched 9/30 10:39,
11:04, 12:02 and sprayed rejected STCs. close_push_skip=exit_not_filled
and flatten_limit_thru_usd=0.1 are the leftover spray. 11:21/11:29 missed
because pending_entry + working_bto locked later subs.

1) On BTO fill:
   state.on_bto_fill(qty, fill_px)
   Do NOT post STC. Do NOT rest a working bid/ask/mid exit.
   Ignore leftover book.take_exit. last_action=bto_fill. pending_entry=false.

2) Every manage tick / every STC:
   qty = BROKER_QTY (Tradier position, not intended).
   m = on_manage(..., qty=qty)
   if qty <= 0 or m["flatten"] is not True:
       take_exit=hold; post_stc=false; cancel working STC; return
   stc = before_stc(...)
   if not stc["post"] or stc.get("reason")=="skip_already_flat":
       cancel working STC; return
   Only then walk bid / bid-0.05 / bid-0.10 / MARKET ONCE.
   Disable native close_push. Set flatten_limit_thru_usd=0.
   Do not spray a second ladder. close_push_skip must not be exit_not_filled
   after a filled STC.

3) Working BTO lock (11:21/11:29):
   One ask limit at a time. While send age < 90s, replace to CURRENT ask.
   If send age >= 90s OR ask-limit >= 0.10: CANCEL, pending_entry=false,
   inflight=false, last_error=null. Next before_bto(post=true) may POST.
   recover_lost_flat must not keep pending_entry true when broker_qty=0
   and the only working order is a stale unfilled BTO.

4) DTE clock stays: 12:45 switch to 1DTE, trade it, skip leftover 0DTE.
   STC-hold stays. 1DTE FAILs after 12:45 count toward the 4-fail halt.

Proof: rec_book_ship=2026-09-30-path-bind, skip_already_flat=true,
flatten_limit_thru_usd=0, before_stc=true, stc_on_bto_fill=false,
working_stc_on_fill=false, dte_cutover_et=12:45, trade_1dte_after_cutover=true.
A 1–2 cent scratch in <15s must not happen. After a filled STC, no rejected
0/N ladder. After a stale unfilled BTO cancel, the next sub send BTOs.

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
