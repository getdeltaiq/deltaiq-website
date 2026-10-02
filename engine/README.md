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
- `admin_n >= sub_n`. Plot `sub_alert_send`. `admin_rows=admin_alert_ledger`. `aligned_copy=false` is SMS **body** (“sign in to view”), not “not a trade.”
- **Channels aligned:** overlay `decide_sub_send` and Tradier `before_bto` share `sub_action_skip_reason`. SMS only if `send=True`. If overlay SMS, Tradier BTO that send (bounce / halt / inflight stay exec-only). Never SMS a skip.
- Starter BTO only after `before_bto` returns `post=True` with `plot=sub_alert_send`.
- Envelope flatten sets `override_trail=True` and `engine_exit_mode=ladder_to_market` (never hold a loser on trail).
- **STC only when `on_manage`/`before_stc` returns `flatten=true`.** HOLD ticks use `take_exit=hold` and `post_stc=false`. Never rest a bid STC on BTO fill (9/30 10:39 10-lot 767-call scratch).
- Halt is sticky for the ET date; `recover_lost` never BTOs. A fill younger than 15s is `fresh_fill` (not an orphan). A stamped ticket older than 15s is `owned` (manage loop). Broker longs with no fill stamp are `adopt_stc_only` and `before_stc` flattens (`orphan_adopt`). `/health` must not show `engine_exit_mode=trail`.
- **9/29 learn (wired at BTO, not just overlay):**
  - Refuse CHOPPY (do **not** `chop_size`). Weak pre-move $0.15–$0.29. Strong pre-move ≥ $0.50. Chase ≥ $0.50.
  - Missing `pre_move_spy` / `chase_spy` is `skip_quality_unknown` (fail closed).
  - First-line halt: −$750 session cash (3 envelope misses) **or** 4 consecutive **0DTE** FAILs, even if halt was never lifted. 1DTE FAILs do not increment the streak (9/28 1DTE wiggles had halted before the 0DTE 767-put).
  - Extra BTO on RUN: fill remaining room to 16 when MFE ≥ $0.20 (`before_extra_bto`).
  - Misfire: 8-minute cooldown after any FAIL (`skip_cooldown_after_fail`). Before **12:45 ET** 1DTE starters require overlay `regime=TREND` (`skip_1dte_not_trend`). From **12:45** the book is 1DTE: trade next-day OCC, refuse leftover 0DTE (`skip_0dte_after_cutover`). Do not freeze 0DTE before cutover.
  - **9/30 alert quality (morning losses >$150):** 0DTE in **10:00–10:20** is `skip_0dte_open_fade` (10:14 BEAR −$190; also 10/1 10:01 SUB ping). 0DTE in **12:30–12:44** is `skip_0dte_near_cutover` (12:44 BULL −$256, STC at the 12:45 cutover). 1DTE TREND still posts in those windows. 10:21–12:29 0DTE still posts. 09:36–09:40 morning rip still sends.
  - **Channel align (10/1):** SUB_signals SMS is a `sub_alert_send`. Overlay and Tradier call the same skip stack. A 10:01 0DTE is admin-only (no SMS, no BTO). Do not SMS a quality/misfire/clock skip.
  - **1DTE adaptive protect (10/1 close):** 0DTE stays `protective $0.15` + `fail_90`. 1DTE uses `protective $0.30` and does **not** 90s-FAIL (10/1 14:04 $0.15-clip then rally; 14:26 fail_90 scratch). Ticket risk −$240 and flatten 15:50 stay. Do not freeze 0DTE TREND.
  - **Orphan adopt (10/2 12:52 1DTE):** 7-lot Monday 769-call 2.71→2.16 sat 90m until a manual STC. The 9/30 fresh-fill guard is not this gap. `recover_lost` now returns `owned` for stamped tickets; true orphans (`ticket_fill_ts` missing) flatten through `before_stc` (`orphan_adopt`). 1DTE −$0.30 still flattens on the manage loop.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

Paste into the `deltaiq-signal-engine` production agent:

```
Copy engine/shared/gates.py, engine/tradier_exec/hooks.py, engine/overlay/publish.py
from getdeltaiq/deltaiq-website PR 24 onto production.
rec_book_ship=2026-10-02-orphan-adopt. Do not lift a halt that is already off.

ORPHAN ADOPT — 10/2 12:52 1DTE 7-lot SPY261005C00769000 2.71→2.16 sat 90m
until a manual STC. This is NOT the 9/30 10:39 fresh-fill scratch.
recover_lost:
  qty 0 => flat
  ticket_fill_ts set and age < 15s => fresh_fill (do NOT STC)
  ticket_fill_ts set and age >= 15s => owned (manage loop; do NOT STC here)
  qty > 0 and no fill stamp => adopt_stc_only, orphan_adopt=true
If orphan_adopt: before_stc / on_manage MUST flatten (reason=orphan_adopt)
even if the envelope has not printed. Do not HOLD a true orphan.
Owned 1DTE still HOLD until $0.30 / ticket_risk / 15:50.
A 1DTE 2.71→2.16 (7-lot, 90m) must flatten_protective on the manage tick.

1DTE ADAPTIVE PROTECT — 10/1 $0.15 clipped 1DTE path winners.
0DTE keeps protective $0.15 and fail_90 (9/29 16-lot).
1DTE protective $0.30. 1DTE does NOT fail_90.
A 14:04 1DTE −$0.21 option dip must HOLD. A 14:26 1DTE 90s tick must HOLD.
Ticket risk −$240 and 15:50 flatten stay. Do not freeze 0DTE TREND.

CHANNEL ALIGN — 10/1 10:01 SUB_signals SMS with no Tradier BTO is the bug.
SMS and BTO share sub_action_skip_reason. One skip stack.
aligned_copy=false is SMS wording ("sign in to view"), NOT "not a trade."
If decide_sub_send send=True, before_bto must POST (except bounce/halt/inflight).
If sub_action_skip_reason returns a code: do not SMS and do not BTO.
A 10:01 0DTE is skip_0dte_open_fade on BOTH channels.

ALERT QUALITY — 9/30 two morning envelope losses >$150 were real sub sends:
10:14 BEAR 10-lot 1.84→1.65 −$190, 12:44 BULL 16-lot 1.19→1.03 −$256.
Path-bind (STC-hold / skip_already_flat) stays. Do not freeze 0DTE TREND.

1) Overlay skip_reason after armed calls sub_action_skip_reason (same as before_bto):
   quality + misfire + clock. Missing dte => skip_dte_unknown (no SMS, no BTO).
   0DTE in 10:00–10:20 ET => skip_0dte_open_fade (admin only, no SMS, no BTO).
   0DTE in 12:30–12:44 ET => skip_0dte_near_cutover (admin only, no SMS, no BTO).
   1DTE still sends/posts in those windows (9/25 TREND). 10:21–12:29 0DTE still posts.
   09:36–09:40 morning rip still sends. 12:45+ is the 1DTE book (15:16 runner).

2) On BTO fill:
   state.on_bto_fill(qty, fill_px)
   Do NOT post STC. Do NOT rest a working bid/ask/mid exit.
   Ignore leftover book.take_exit. last_action=bto_fill. pending_entry=false.

3) Every manage tick / every STC:
   qty = BROKER_QTY (Tradier position, not intended).
   m = on_manage(..., qty=qty, dte=option_dte(symbol, session_date))
   if qty <= 0 or m["flatten"] is not True:
       take_exit=hold; post_stc=false; cancel working STC; return
   stc = before_stc(...)
   if not stc["post"] or stc.get("reason")=="skip_already_flat":
       cancel working STC; return
   Only then walk bid / bid-0.05 / bid-0.10 / MARKET ONCE.
   Disable native close_push. Set flatten_limit_thru_usd=0.
   0DTE: flatten at $0.15 down or fail_90. 1DTE: flatten at $0.30 down;
   do NOT fail_90. Ticket risk −$240 still flattens either DTE.

4) Working BTO lock (11:21/11:29):
   One ask limit at a time. While send age < 90s, replace to CURRENT ask.
   If send age >= 90s OR ask-limit >= 0.10: CANCEL, pending_entry=false,
   inflight=false, last_error=null. Next before_bto(post=true) may POST.

5) DTE clock stays: 12:45 switch to 1DTE, trade it, skip leftover 0DTE.
   STC-hold stays. 1DTE FAILs after 12:45 count toward the 4-fail halt.

6) recover_lost every scan that sees broker_qty:
   kind = state.recover_lost(broker_qty)
   if kind == "fresh_fill": do NOT STC
   if kind == "owned": call on_manage / before_stc as normal (envelope may flatten)
   if kind == "adopt_stc_only": before_stc MUST post (orphan_adopt). Walk ladder.
   NEVER BTO from recover_lost.

Proof: rec_book_ship=2026-10-02-orphan-adopt, channels_aligned=true,
sms_iff_sub_send=true, sms_from=sub_alert_send, skip_0dte_open_fade=true,
open_fade_window=["10:00","10:20"], skip_0dte_near_cutover=true,
near_cutover_0dte_et=12:30, skip_already_flat=true, flatten_limit_thru_usd=0,
dte_cutover_et=12:45, trade_1dte_after_cutover=true,
protective_stop_usd=0.15, protective_stop_1dte_usd=0.30, fail_sec_1dte=null,
recover_lost_owned=true, orphan_adopt_flattens=true.
A 10:01 0DTE must be skip_0dte_open_fade on SMS AND BTO. A 10:14 0DTE BEAR
must be skip_0dte_open_fade. A 12:44 0DTE BULL must be skip_0dte_near_cutover.
An 11:48 0DTE and a 15:16 1DTE must still SEND and POST.
A 1DTE −$0.21 mark in 60s must HOLD. A 0DTE −$0.15 mark must still flatten.
A 1DTE 7-lot 2.71→2.16 must flatten_protective. A true orphan (no fill stamp)
must flatten_orphan_adopt through before_stc. A 3s fill is still fresh_fill.

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
