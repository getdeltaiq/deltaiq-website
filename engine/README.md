# Engine (GitHub source of truth)

This conversation **edits these files**. Railway must import them. Do not patch overlay or `tradier_exec` on Railway as a second copy.

## Layout

| Path | What this agent changes |
|---|---|
| `engine/overlay/publish.py` | Admin ledger (100% of candidates) vs sub send (actionable subset). SMS + Tradier read **sub only**. |
| `engine/shared/gates.py` | Tradier size, consume-once, bounce skip, envelope, halt, STC ladder → market |
| `engine/tradier_exec/hooks.py` | `before_bto` / `on_manage` / `/health` proof keys (`gates_module=engine.shared.gates`) |
| `engine/tradier_exec/sources.py` | Recap/grid source rank: live equity first; do not treat empty history as $0 |

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
  - **1-minute rip (10/2, production):** SUB losers were 1-minute prints (11:42 +$0.44 bar / 3-minute +$0.12). The 10:54 BEAR winner had a 3-minute dump (−$0.87). Overlay MUST stamp signed `rip_1m_spy` / `trend_3m_spy` via `signed_spy_deltas` / `with_spy_deltas` on the last four 1-minute SPY closes. Missing is `skip_1min_unconfirmed`. A 1-minute print without a 3-minute trend in the send direction is `skip_1min_rip` (admin only, no SMS, no BTO). Fewer SUB alerts is the bar. Extra BTO on RUN sizes impactful winners to 16. Do not freeze 0DTE TREND.
  - **1-minute stall (10/5):** 10:58 BULL −$182 and 11:42 BULL −$320 had a 3-minute move (≥ $0.20) with a dead last minute (+$0.03 / +$0.02). SUB also requires the last minute still print WITH the send by at least **$0.08** (`skip_1min_stall`). 10/5 12:22 +$1,344 was +$0.175 / +$0.235 and still SENDS. 9/24 10:43 BEAR, 9/25 11:55 BULL, 9/28 10:42 BEAR, 10/2 10:54 BEAR still SEND. Do not raise the 3-minute floor to $0.30 (that would skip 12:22). Do not freeze 0DTE TREND.
  - **Peak lock (10/5):** A runner’s bid high is the high-water mark. Once unrealized ≥ **$50**, flatten if the ticket gives back **$50** from that high (`peak_giveback`). 10/5 16-lot 773C 0.79→1.67 (~+$1,408) must NOT sit to the fill $0.15 stop at 0.64. Fill $0.15 / 1DTE $0.30 still protect tickets that never made a high. Quote grace 8s and no STC on fill stay. Do not freeze 0DTE TREND.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` / `rip_1m_spy` / `trend_3m_spy` into `before_bto`.
- **Recap sources (10/7–10/8):** Daily net uses live `totalEquity` minus the last posted hist close. Ticket rows use posted `get_account_history` **fill** prices and cash (query `type=trade`; FIFO per OCC). `get_orders` is same-calendar-day only and `price` is often the LIMIT, not the fill — empty the next morning is not “no trades.” Overnight 10/7 history cash was WIN +$300.14 (premium +$423), not the limit-tape LOSS −$284. Empty history is still not FLAT $0 while fills are unposted. Do not let a ticket sum override the live balance.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

This website PR does not deploy the bot. Paste into the `deltaiq-signal-engine` **production** agent and ship now:

```
SHIP TO PRODUCTION NOW. Live /health is still rec_book_ship=2026-10-02-1min-rip
(missing stall-1m and peak-lock). RTH is closed — copy before the next open.
Do not keep a second knob set. Do not lift a halt that is already off.
Do not freeze 0DTE TREND. Fewer SUB alerts is correct.

Copy these three files from getdeltaiq/deltaiq-website SHA 33f8d2a (PR 24)
onto getdeltaiq/deltaiq-signal-engine production (replace Railway copies):
  engine/shared/gates.py
  engine/tradier_exec/hooks.py
  engine/overlay/publish.py
Public raw (website is public; curl these, do not rewrite knobs):
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/33f8d2a6d3822d964b3eafa58b173591ecbb7432/engine/shared/gates.py
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/33f8d2a6d3822d964b3eafa58b173591ecbb7432/engine/tradier_exec/hooks.py
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/33f8d2a6d3822d964b3eafa58b173591ecbb7432/engine/overlay/publish.py
Wire overlay ingest to stamp every Candidate with with_spy_deltas /
signed_spy_deltas on the last four 1m SPY closes before decide_sub_send.
rec_book_ship MUST become 2026-10-06-1min-stall on /health.

HIGH-WATER $50 — 10/5 16-lot 773C 0.79 ran to ~1.67 (~+$1,408).
The fill $0.15 stop (0.64) would give back ~$1,650. That is not the exit.
Ratchet ticket_peak_unrealized on the BID every manage tick.
Once peak >= $50, if peak - unrealized >= $50 => flatten peak_giveback
(ladder to market). A 1.67 high flattens near 1.63, not 0.64.
Fill $0.15 (0DTE) / $0.30 (1DTE) still protect tickets with no high.
Quote grace 8s. Do NOT STC on BTO fill. Extra BTO on RUN stays.

QUALITY BAR — SUB SMS = Tradier BTO. Alerts that fire should be 3-minute
confirmed trends still moving in the last minute, not 1-minute prints
and not stalled 3-minute rips. Impactful winners (10:54 BEAR
−0.28 / −0.87, 10/5 12:22 +0.175 / +0.235) still SEND and extra-BTO
to 16 on RUN (MFE ≥ $0.20). Admin ledger still records the skips.

OVERLAY TAPE — every Candidate MUST be stamped before ingest/decide_sub_send:
  from engine.overlay.publish import with_spy_deltas
  from engine.shared.gates import signed_spy_deltas
  c = with_spy_deltas(c, last_four_1m_spy_closes)  # oldest → now
  # or Candidate(..., **signed_spy_deltas(last_four_1m_spy_closes))
Up is +. Need 4 closes. Short tape => None, None => skip_1min_unconfirmed
(no SMS, no BTO). Do NOT default 1m/3m. Do NOT inject trend_confirm_kwargs
on live sends (that is replay-only).

1-MINUTE RIP — 10/2 SUB losers were 1-minute prints. Do not send those.
SUB requires the 3-minute move in the send direction of at least $0.20.
If abs(1m) >= abs(3m) and abs(1m) >= $0.20, it is a one-bar climax => skip_1min_rip.
10/2 11:42 BULL +0.44 / +0.12 => skip_1min_rip.
10/2 10:34 BEAR +0.16 / +0.08 => skip_1min_rip.
10/2 10:54 BEAR −0.28 / −0.87 => SEND (3-minute dump). extra BTO on RUN.
10/2 12:52 1DTE BULL +0.14 / +0.27 => SEND (exit/orphan, not a rip skip).
1-MINUTE STALL — 10/5 10:58 / 11:42 3-minute rips had already stalled.
Last minute must still print WITH the send by at least $0.08.
10/5 10:58 BULL +0.03 / +0.505 => skip_1min_stall (−$182).
10/5 11:42 BULL +0.02 / +0.25 => skip_1min_stall (−$320).
10/5 10:40 BULL +0.015 / +0.22 => skip_1min_stall (−$64).
10/5 12:22 BULL +0.175 / +0.235 => SEND (+$1,344).
10/5 10:33 BEAR −0.18 / −0.50 => SEND (+$48).
9/24 10:43 BEAR −0.19 / −0.43, 9/25 11:55 BULL +0.25 / +0.94,
9/28 10:42 BEAR −0.24 / −0.26, 10/2 10:54 BEAR −0.28 / −0.87 => SEND.
Do not raise trend_3m_min to $0.30 (skips 12:22).
Do not freeze 0DTE TREND. Admin ledger still gets the 1-minute candidates.

ORPHAN ADOPT — 10/2 12:52 1DTE 7-lot sat 90m until a manual STC.
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

Proof: rec_book_ship=2026-10-06-1min-stall, protect_from_high=true,
peak_giveback_usd=50, channels_aligned=true,
sms_iff_sub_send=true, sms_from=sub_alert_send, skip_0dte_open_fade=true,
open_fade_window=["10:00","10:20"], skip_0dte_near_cutover=true,
near_cutover_0dte_et=12:30, skip_already_flat=true, flatten_limit_thru_usd=0,
dte_cutover_et=12:45, trade_1dte_after_cutover=true,
protective_stop_usd=0.15, protective_stop_1dte_usd=0.30, fail_sec_1dte=null,
recover_lost_owned=true, orphan_adopt_flattens=true,
skip_1min_rip=true, one_min_rip_usd=0.20, trend_3m_min_usd=0.20,
skip_1min_stall=true, stall_1m_usd=0.08.
A 10:01 0DTE must be skip_0dte_open_fade on SMS AND BTO. A 10:14 0DTE BEAR
must be skip_0dte_open_fade. A 12:44 0DTE BULL must be skip_0dte_near_cutover.
An 11:48 0DTE and a 15:16 1DTE must still SEND and POST.
A 1DTE −$0.21 mark in 60s must HOLD. A 0DTE −$0.15 mark must still flatten.
A 1DTE 7-lot 2.71→2.16 must flatten_protective. A true orphan (no fill stamp)
must flatten_orphan_adopt through before_stc. A 3s fill is still fresh_fill.
A 11:42 1-min +0.44 / 3-min +0.12 must be skip_1min_rip on SMS AND BTO.
A 10:54 BEAR 1-min −0.28 / 3-min −0.87 must still SEND and POST.
A 10/5 10:58 BULL +0.03 / +0.505 must be skip_1min_stall.
A 10/5 12:22 BULL +0.175 / +0.235 must still SEND and POST.
A 16-lot 0.79 fill marked 1.67 must HOLD. Marked 1.63 after that high
must flatten_peak_giveback. Do not sit to fill-0.15 (0.64).

```


## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates engine.tradier_exec.tests.test_runtime engine.tradier_exec.tests.test_week_replay engine.tradier_exec.tests.test_sources
python3 -m engine.tradier_exec.replay
python3 -m engine.tradier_exec.week_replay
```

30 trading days through 9/29, weekly Rec vs live: `python3 -m engine.tradier_exec.week_replay`.
Rec tape is 9/15–9/29 (envelope + 0DTE fail-streak + extra BTO on RUN + 1DTE skip
unless TREND). Earlier weeks have no SPY lots; Rec equals live (impact $0).
Halt resets on the ET date.
