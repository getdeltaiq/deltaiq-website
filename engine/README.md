# Engine (GitHub source of truth)

This conversation **edits these files**. Railway must import them. Do not patch overlay or `tradier_exec` on Railway as a second copy.

## Layout

| Path | What this agent changes |
|---|---|
| `engine/overlay/publish.py` | Admin ledger (100% of candidates) vs sub send (actionable subset). SMS + Tradier read **sub only**. |
| `engine/shared/gates.py` | Tradier size, consume-once, bounce skip, envelope, halt, STC ladder → market |
| `engine/tradier_exec/hooks.py` | `before_bto` / `on_manage` / `on_ladder_step` / `on_flatten_fill` / `/health` proof keys (`gates_module=engine.shared.gates`) |
| `engine/tradier_exec/persist.py` | Durable `consumed_sends` **and** `flatten_exits` (survive ET midnight) |

`exec/` remains a compatibility shim + shore-up installer for the live Railway process until it imports `engine.*` directly.

## Contract

- **Adopted book:** overlay **sub** is the performer (PF 1.86 paper). Cover-us is the cash floor. Never BTO `admin_alert_ledger`. Never exec-queue an opposite (`queue_opposite=false`) unless overlay itself queued that send. Flatten `take_exit=ladder_to_market` (not bid).
- `admin_n >= sub_n`. Plot `sub_alert_send`. `admin_rows=admin_alert_ledger`. `aligned_copy=false` is SMS **body** (“sign in to view”), not “not a trade.”
- **Channels aligned:** overlay `decide_sub_send` and Tradier `before_bto` share `sub_action_skip_reason`. SMS only if `send=True`. If overlay SMS, Tradier BTO that send (bounce / halt / inflight stay exec-only). Never SMS a skip.
- Starter BTO only after `before_bto` returns `post=True` with `plot=sub_alert_send`.
- Envelope flatten sets `override_trail=True` and `engine_exit_mode=ladder_to_market` (never hold a loser on trail).
- **STC only when `on_manage`/`before_stc` returns `flatten=true`.** HOLD ticks use `take_exit=hold` and `post_stc=false`. Never rest a bid STC on BTO fill (9/30 10:39 10-lot 767-call scratch).
- Halt is sticky for the ET date; `recover_lost` never BTOs. Unstamped broker qty is a live fill: stamp now and `fresh_fill` (`HOLD_UNSTAMPED_FILL`). A stamped ticket younger than 15s is `fresh_fill`. Older than 15s is `owned` (manage loop). True `adopt_stc_only` / `orphan_adopt` is only when `HOLD_UNSTAMPED_FILL` is off. `/health` must not show `engine_exit_mode=trail`.
- **9/29 learn (wired at BTO, not just overlay):**
  - Refuse CHOPPY (do **not** `chop_size`). Weak pre-move $0.15–$0.29. Strong pre-move ≥ $0.50. Chase ≥ $0.50.
  - Missing `pre_move_spy` / `chase_spy` is `skip_quality_unknown` (fail closed).
  - First-line halt: −$750 session cash (3 envelope misses) **or** 4 consecutive **0DTE** FAILs, even if halt was never lifted. 1DTE FAILs do not increment the streak (9/28 1DTE wiggles had halted before the 0DTE 767-put).
  - Extra BTO on RUN: fill remaining room to 16 when MFE ≥ $0.20 (`before_extra_bto`).
  - Misfire: 8-minute cooldown after any FAIL (`skip_cooldown_after_fail`). Before **12:45 ET** 1DTE starters require overlay `regime=TREND` (`skip_1dte_not_trend`). From **12:30** overlay scores **1DTE** (`starter_dte_for_clock`). From **12:45** leftover 0DTE is refused (`skip_0dte_after_cutover`). Do not freeze 0DTE TREND before 12:30.
  - **9/30 alert quality (morning losses >$150):** 0DTE in **10:00–10:20** is `skip_0dte_open_fade` (10:14 BEAR −$190; also 10/1 10:01 SUB ping). Overlay scores **1DTE TREND** in that window (`open_fade_1dte_book`) so it is not a dark gap. 0DTE in **12:30–12:44** is `skip_0dte_near_cutover` (12:44 BULL −$256, STC at the 12:45 cutover). Overlay must select 1DTE from 12:30. 1DTE TREND still posts. 10:21–12:29 0DTE still posts. Publish is continuous **09:30–15:50** (09:41–09:59 is no longer `skip_outside_window`).
  - **Channel align (10/1):** SUB_signals SMS is a `sub_alert_send`. Overlay and Tradier call the same skip stack. A 10:01 0DTE is admin-only (no SMS, no BTO). Do not SMS a quality/misfire/clock skip.
  - **1DTE adaptive protect (10/1 close):** 0DTE stays `protective $0.15` + `fail_90`. 1DTE uses `protective $0.30` and does **not** 90s-FAIL (10/1 14:04 $0.15-clip then rally; 14:26 fail_90 scratch). Ticket risk −$240 and flatten 15:50 stay. Do not freeze 0DTE TREND.
  - **1-minute rip (10/2, production):** SUB losers were 1-minute prints (11:42 +$0.44 bar / 3-minute +$0.12). The 10:54 BEAR winner had a 3-minute dump (−$0.87). Overlay MUST stamp signed `rip_1m_spy` / `trend_3m_spy` via `signed_spy_deltas` / `with_spy_deltas` on the last four 1-minute SPY closes. Missing is `skip_1min_unconfirmed`. A 1-minute print without a 3-minute trend in the send direction is `skip_1min_rip` (admin only, no SMS, no BTO). Fewer SUB alerts is the bar. Extra BTO on RUN sizes impactful winners to 16. Do not freeze 0DTE TREND.
  - **1-minute stall (10/5):** 10:58 BULL −$182 and 11:42 BULL −$320 had a 3-minute move (≥ $0.20) with a dead last minute (+$0.03 / +$0.02). SUB also requires the last minute still print WITH the send by at least **$0.08** (`skip_1min_stall`). 10/5 12:22 +$1,344 was +$0.175 / +$0.235 and still SENDS. 9/24 10:43 BEAR, 9/25 11:55 BULL, 9/28 10:42 BEAR, 10/2 10:54 BEAR still SEND. Do not raise the 3-minute floor to $0.30 (that would skip 12:22). Do not freeze 0DTE TREND.
  - **Climax + 1DTE book (10/8):** The 12:17 BULL spike was +$1.55 / +$1.61 (96% of the 3-minute in one bar) then a BEAR dump. Skip when last minute ≥ **80%** of the 3-minute **and** ≥ **$0.50** (`skip_1min_rip`). 9/28 10:42 −$0.24 / −$0.26 still SENDS (grind, not a $0.50 spike). From **12:30** overlay selects 1DTE (`starter_dte_for_clock`); 0DTE in 12:30–12:44 stays admin-only. 12:37–12:38 BEAR −$0.22 / −$0.67 on 1DTE TREND SENDS. Do not loosen stall $0.08 or the $0.20 3-minute floor. Do not freeze 0DTE TREND before 12:30 except the 10:00–10:20 fade tape.
  - **RTH hold (10/8 scratches):** Do not enter and immediately STC unless SPY reversed **$0.30** against the send. 0DTE `fail_90` holds unless `spy_adverse >= $0.30` (`FAIL_90_REQUIRES_REVERSAL`). Protective $0.15 / cata still flatten a real option dump. Unstamped broker qty is a new ticket (`HOLD_UNSTAMPED_FILL`): stamp and HOLD (10/8 13:20 9s scratch). After 15s the ticket is `owned`; 10/2 leftover still clips at 1DTE $0.30. Overlay stays armed **09:30–15:50** with a product at every clock.
  - **Rip hold (10/9 62s scratch):** Peak giveback does not fire in the first **90s** (`PEAK_GIVEBACK_MIN_SEC`). 10/9 10:45 BULL 13-lot 1.17→1.23 ($78) then STC at 1.10 in 62s dumped the continuation. First 90s only flatten on fill **$0.15** / cata / ticket_risk. Do not move the $0.15 stop to the print high. Do not fire a starter $37.50 giveback before 90s. Skip stack (stall $0.08, 3m $0.20, chase $0.50, climax $0.50/80%) is unchanged, so prior SEND winners stay SEND.
  - **Clock identity (10/9 11:04 SMS vs 11:07 put):** One unix `send_ts` in America/New_York is SMS publication time, overlay label, strike copy, and Tradier consume. Do not use `legacy_ledger_hhmm` for live SMS (10:32 vs 10:45 same send). Do not SMS at arm/receipt then consume a later opposite seal (11:04 BULL 777/778 at 777.41 vs 11:07 BEAR 777 put). `sms_at_seal=true`. `clock_fallback=historical_audits_only`.
  - **Overlay = Pushover (10/12):** Overlay SUB markers ARE the Pushover/Twilio publications (`overlay_iff_sms`). Same `send_ts`, `et_hms`, direction, SPY. 10/12 overlay showed 1 (11:13 BEAR 778.12) while the phone got 3 (10:35:53, 11:49:03, 12:22:52) because `follow_delivered_sub` SMS'd receipts that were never overlay-ingested. `follow_delivered_sub=false`. `alert_overlay.html` (including `view=observational`) plots `PublishLedgers.overlay_sends()` only. Twilio body is `pushover_from_send(decide_sub_send)`. Do not plot `confirmed_overrides_ledger`. Do not SMS a receipt then overlay a different seal.
  - **Min hold (10/9 C/D/E 5–12s scratches):** `MIN_HOLD_SEC=90` on **every** flatten except fill `$0.15` / 1DTE `$0.30` / cata / ticket_risk. Live sold C in 5s, D in 7s, E (a winner) in 12s via `orphan_adopt` / `recover_lost_flat` before SPY could move. Same 90s as fail_90 / peak min — not a one-day patch. 10/5 773C still locks after 90s. 10/2 leftover still clips `$0.30`. 9/28 10:42 / 10/2 10:54 / 10/5 12:22 still SEND. Do not STC on BTO fill. Do not fire starter `$37.50` giveback before 90s.
  - **Scratch hold (10/13):** `/health` advertised `min_hold_sec=90` then sold a 14-lot 1.07→1.05 in **4s** (`recover_lost_flat` / `broker_flat` ladder) and a 12-lot 1.19→1.15 in **122s** (−$48, live `$37.50` peak_starter from print high). `flatten_allowed` is fail-closed: only envelope reasons post (`protective` / `ticket_risk` / cata / `fail_90` / `peak_giveback` / `orphan_adopt` / `rth_end`). Live scratch names never flatten.
  - **Scratch lock (10/13, holds):** Listing leftover names is not enough — live can relabel a $0.04 dip as `protective` / `peak_giveback` / `fail_90` and sell. `may_post_stc` **recomputes** `envelope_hit` from fill/bid/qty and **ignores the claimed reason**. `on_manage` and `before_stc` share that gate. Missing marks fail closed. `morning_rip_flatten=false` (live 09:45). 15:50 leftover still `rth_end`. A 4s $0.02 dip HOLDs. A 122s $0.04 dip HOLDs even if named `peak_giveback`. A 20s fill $0.15 dump still clips. 10/5 773C at 180s still peak_giveback $50.
  - **1DTE exit log + short ladder (10/15):** `get_orders` is same-day and lost the 10/13 dump clock. Every flatten writes `flatten_exits` (trigger time, trigger bid, planned ladder, each posted step, final fill) and **keeps it across ET midnight**. 1DTE **protective** only: rest the trigger bid, then **market after 2s** (skip bid−0.05 / bid−0.10). 0DTE $0.15 keeps the four-step ladder. 1DTE size holds at **6** (`one_dte_qty_cap`); 1DTE extra BTO is off. Scaled 1DTE giveback (`max($0.08/contract, 25% of peak)`) is **replay-only** until a logged high-water exists — 10/14 +$30 never tagged $50, +$90 has no bid path. Do not change 0DTE size or 0DTE peak $50.
  - **Match Rec dollars (10/15):** Tradier must run the Rec book, not leftover size. Overwrite live `$3000` / `$1125` / `$360` / `$75` / `extra_bto=false` / `fill_to 24`. Rec is starter **$2000**, extra BTO **on** to **16**, ticket **−$240**, halt **−$750**, peak **$50**, 1DTE cap **6**. `extra_bto_on_run=true`. Skip+hold already in SoT; leftover knobs were the revenue hole.
  - **Rec revenue lock (10/15):** Rec beats live on priced tape (30-day Rec +$5,314 vs −$2,480; Oct 1–8 Rec +$1,637 vs +$958). Those rails stay frozen: stall **$0.08**, 3m **$0.20**, chase **$0.50**, climax **$0.50/80%**, CHOPPY refuse, extra BTO to 16, halt **−$750**, 90s hold, peak **$50**, envelope still clips fill **$0.15** / 1DTE **$0.30**. Do not tighten (kills 10/5 12:22). Do not loosen (reopens 10/8 A/D/E and 9/29). `rec_revenue_lock=true`.
  - **SUB clock = Tradier clock (10/15 B/F):** Overlay marker unix `send_ts` is the BTO clock. `plot=sub_alert_send` forces `overlay_queued` so opposite SUBs post (B 11:47). Inflight does **not** consume (B/F retry when flat). `after_recover_lost("flat")` is `skip_already_flat`. 1DTE $0.02 dips at 2–7s HOLD is the **exit** track, not A/C/D/G.
  - **Entry at seal (10/15 A/C/D/G):** Those clocks erred at `decide_sub_send`, not STC. Live stamped 1m/3m at arm, waited `PUBLISH_ARM_SEC`, then sent into the opposite tape. Rec stamps the send bar. Call `at_seal(c, last_four_1m_closes_at_send_ts)` immediately before `decide_sub_send` / ingest. A 10:26 BEAR into a rising 3-minute is `skip_1min_rip`. C 12:45 stall, D 13:12 BEAR into rally, G 15:20 climax same skip. Do not retune stall $0.08 / 3m $0.20 / chase $0.50 / climax $0.50/80%. Flatten/hold stays a separate track. `quality_at_seal=true`.
  - **Singular Rec book (10/15):** One book. Rec in `engine/shared/gates.py` (`rec_book()`, ship `2026-10-15-match-rec`) is the winner — 30-day Rec +$5,314 vs live −$2,480. Rec-on-prod was a **label**: `/health` advertised `rec_book_ship` while `code_ship` stayed `2026-09-29b` and leftover `$3000` / extra off / `$37.50` peak-from-print-high still sized and flattened. `code_ship` MUST equal `rec_book_ship`. Process constants MUST match `REC_BOOK_REQUIRED`. Leftover live knobs are `skip_not_rec_book` and do **not** consume. `rec_book_identity=true`. `rec_book_mismatch=[]`. Do not keep a second knob set.
  - **Peak lock (10/5):** A runner’s bid high is the high-water mark. Once unrealized ≥ **$50** **and the ticket is ≥ 90s old**, flatten if the ticket gives back **$50** from that high (`peak_giveback`). 10/5 16-lot 773C 0.79→1.67 (~+$1,408) must NOT sit to the fill $0.15 stop at 0.64. Fill $0.15 / 1DTE $0.30 still protect tickets that never made a high. Quote grace 8s and no STC on fill stay. Do not freeze 0DTE TREND.
  - `consumed_sends` SQL persist on every consume; boot `load_consumed`. Never DELETE on flatten.
  - Pass `plot=sub_alert_send`, `overlay_queued`, `is_opposite`, `choppy` / `on_arm_bar` / `pre_move_spy` / `chase_spy` / `regime` / `same_dir_age_sec` / `rip_1m_spy` / `trend_3m_spy` into `before_bto`.
- Live Railway repo is `getdeltaiq/deltaiq-signal-engine` branch `production`. Copy `engine/shared/gates.py`, `engine/tradier_exec/hooks.py`, and `engine/overlay/publish.py` there to ship. This website PR does not deploy the bot.

## Copy prompt for the signal-engine agent

This website PR does not deploy the bot. Paste into the `deltaiq-signal-engine` **production** agent and ship now:

```
REPLACE PRODUCTION WITH GITHUB REC. Do not patch a second book.
The website files ARE the book. curl them over engine/* and DELETE
leftover Railway knobs. Do not merge-keep. Do not wrap health_overlay
with a second dict (peak_from_print / never_green_withhold /
opposite_sub_consumes / qty_max=100 / logic_rebuild 2026-09-29b).
Before 09:30 ET. ONE BOOK: Tradier IS GitHub rec_book().
code_ship MUST equal rec_book_ship=2026-10-15-match-rec.
FAIL if logic_rebuild.code_ship or performance.code_ship or
cover_paths.code_ship is still 2026-09-29b. FAIL if
rec_book_mismatch is nonempty. rec_book_identity=true.
rec_revenue_lock=true. quality_at_seal / SUB clock / scratch-lock
ARE Rec, not add-ons. Do not retune stall $0.08 / 3m $0.20 /
chase $0.50 / climax $0.50/80%.
DELETE these live leftovers (they are a 2nd book):
  peak_from_print, never_green_withhold, opposite_sub_consumes,
  qty_max=100, peak_starter $37.50, starter $3000, extra_bto=false,
  fill_to 24, halt $1125. Rec is starter $2000, extra BTO on to 16,
  halt $750, ticket $240, peak $50, qty_max=16, peak_from_print=false,
  never_green_withhold=false, opposite_sub_consumes=false.
Import engine.shared.gates + engine.tradier_exec.hooks. Call
at_seal then decide_sub_send then before_bto. health IS
health_overlay(state). Do not overlay a second knob set.

Copy these files from getdeltaiq/deltaiq-website SHA 3ce1abe onto
getdeltaiq/deltaiq-signal-engine production (REPLACE, do not patch):
  engine/shared/gates.py
  engine/tradier_exec/hooks.py
  engine/tradier_exec/persist.py
  engine/overlay/publish.py
Public raw (website is public; curl these, then SET Rec knobs):
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/3ce1abe/engine/shared/gates.py
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/3ce1abe/engine/tradier_exec/hooks.py
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/3ce1abe/engine/tradier_exec/persist.py
  https://raw.githubusercontent.com/getdeltaiq/deltaiq-website/3ce1abe/engine/overlay/publish.py

1) Continuous RTH: in_publish_window is 09:30–15:50 inclusive.
   Delete the 09:36–09:40 + 10:00–15:50 split. 09:41–09:59 is no longer
   skip_outside_window. Quality bar still applies every minute.

2) Open-fade 1DTE book: starter_dte_for_clock returns 1 in 10:00–10:20
   AND from 12:30. 0DTE in 10:00–10:20 stays skip_0dte_open_fade
   (no SMS, no BTO). 1DTE TREND still SENDS in that window.

3) HOLD_UNSTAMPED_FILL: recover_lost with qty>0 and no ticket_fill_ts
   stamps now and returns fresh_fill. 10/8 13:20 filled then STC in 9s
   because the stamp never landed. After 15s the ticket is owned;
   envelope still clips a 10/2 leftover (1DTE $0.30). True
   adopt_stc_only only if HOLD_UNSTAMPED_FILL is false.

4) FAIL_90_REQUIRES_REVERSAL: 0DTE fail_90 returns None unless
   spy_adverse >= $0.30. Immediate STC is only for a major reversal
   after the send, or protective $0.15 / cata / ticket_risk. 1DTE
   still has no fail_90.

5) PEAK_GIVEBACK_MIN_SEC=90: peak_giveback returns None before 90s.
   10/9 10:45 BULL 13-lot 1.17→1.23 ($78) STC 62s at 1.10 must HOLD.
   Same giveback at 90s is still peak_giveback. 10/5 773C 16-lot at
   180s still flattens $50 off the high. Fill $0.15 still clips a
   real dump in the first 90s. Do not apply $0.15 to hold_high.

6) CLOCK_IDENTITY_LOCK: one unix send_ts is SMS publication time,
   overlay et_hhmm, strike copy, and Tradier consume. Call
   with_clock_identity / locked_et_hhmm before decide_sub_send AND
   before_bto. Twilio "publication available at" MUST be
   et_hms_from_ts(send_ts), never legacy_ledger_hhmm, never arm/receipt.
   Strike ITM/OTM MUST be strike_copy(direction, spy) at seal
   (BULL 777.41 => 777/778 calls; BEAR 776.92 => 777/776 puts).
   SMS only after decide_sub_send send=True (sms_at_seal). Do not SMS a
   BULL candidate at 11:04:45 then consume a BEAR seal at 11:07:35.
   clock_fallback=historical_audits_only (frozen sessions only).
   overlay_obs.contract.clock stays "America/New_York unix send instant".

7) MIN_HOLD_SEC=90: before_stc / on_manage / recover_lost may flatten
   in the first 90s ONLY for protective (fill_px − bid), cata, or
   ticket_risk. orphan_adopt / fail_90 / peak_giveback / recover_lost_flat
   / starter $37.50 print-high giveback wait 90s. 10/9 C 5s, D 7s, E 12s
   (even $2.00→$2.02) must HOLD. A 62s $78 flicker HOLDs. A 185s put
   with spy_adverse $0.10 HOLDs. A 16s 1DTE 2.71→2.16 still
   flatten_protective. 10/5 773C at 180s still peak_giveback.
   This is the same 90s clock as fail_90 — not a 10/9-only patch.

8) OVERLAY_IFF_SMS: overlay SUB markers ARE the Pushover publications.
   follow_delivered_sub=false. After decide_sub_send send=True, ingest
   that Candidate, plot overlay_send_row, AND fire pushover_from_send
   with the same send_ts / et_hms / direction / spy. Twilio
   "publication available at" is that et_hms. alert_overlay.html
   (view=observational included) plots PublishLedgers.overlay_sends()
   / overlay_obs.sessions[].sends from that list — never
   confirmed_overrides_ledger, never a receipt clock, never an 11:13
   seal that was not texted. 10/12 phone 10:35:53 / 11:49:03 /
   12:22:52 MUST be the three overlay SUB markers. An overlay-only
   11:13 vs those three texts is a FAIL. Do not SMS a receipt that
   decide_sub_send did not send. Do not overlay-label a send that
   Pushover did not get.

9) SCRATCH LOCK: may_post_stc is the ONLY gate in front of any
   Tradier sell_to_close. Wrap on_manage, recover_lost, native
   close_push, working STC, and the bid/bid-0.05/bid-0.10/market
   ladder. If may_post_stc / before_stc / on_manage flatten is
   false: do NOT send, do NOT walk the ladder.
   may_post_stc RECOMPUTES envelope_hit from fill_px / mark_bid /
   qty. The claimed reason is ignored. Relabeling a $0.04 dip as
   protective / peak_giveback / fail_90 / ticket_risk / cata
   MUST HOLD. Missing marks fail closed. flatten_allowed is
   fail-closed — only protective / ticket_risk / cata / fail_90 /
   peak_giveback / orphan_adopt / rth_end. peak_starter_giveback,
   broker_flat, recover_lost_flat, spike_fail, print_high,
   morning_rip_flatten, close_push NEVER flatten (not at 4s,
   not at 122s, not at 09:45). peak_starter_giveback=false and
   peak_starter_giveback_usd=0 (kill live $37.50 from print
   high). morning_rip_flatten=false (kill live 09:45).
   native_close_push=false. Protective down is fill_px − bid,
   never hold_high − bid. 15:50 leftover is rth_end (still
   flatten). 10/13 14-lot 1.07→1.05 at 4s HOLDs. 10/13 12-lot
   1.19→1.15 at 122s (−$48) HOLDs even as peak_giveback.
   10/13 12-lot 1.19→1.04 at 20s still flatten_protective.
   10/9 C/D/E 5–12s still HOLD. 10/5 773C at 180s still
   peak_giveback $50. recover_lost_flat means broker qty is
   already 0 — skip_already_flat, do not spray STC.
   stc_recomputes_envelope=true.

10) 1DTE EXIT LOG + SHORT LADDER: CREATE flatten_exits from
    FLATTEN_EXITS_CREATE_SQL. Never DELETE on flatten or ET
    rollover (keep 30 days). On every flatten_now, persist the
    row (trigger_ts, trigger_et, occ, dte, qty, fill_px,
    trigger_bid, reason, ladder_steps). After each posted STC
    step call on_ladder_step / note_ladder_step. On broker fill
    call on_flatten_fill with the actual price. /health must
    show flatten_exits (last 20, including prior ET dates) —
    not hold_watch.events[] which clears at midnight.
    1DTE protective ladder is bid, then market after 2s
    (one_dte_protective_short_ladder=true). 0DTE stays
    bid / bid-0.05 / bid-0.10 / market. 1DTE starter qty
    min(notional, one_dte_qty_cap=6). extra_bto_1dte=false.
    one_dte_scaled_giveback=false (replay only). 0DTE extra
    BTO to 16 and 0DTE peak $50 stay. 10/13 6× 2.318→1.77
    still flattens protective. 10/14 5-lot winners still
    size under the cap.

Wire overlay ingest to at_seal every Candidate on the last four 1m
SPY closes AT send_ts before decide_sub_send. Arm-bar deltas must not
survive PUBLISH_ARM_SEC.
OCC tenor MUST be starter_dte_for_clock(et_hhmm): 0 at 09:30–09:59 and
10:21–12:29; 1 in 10:00–10:20 and from 12:30. Leftover 0DTE after 12:30
is skip_0dte_near_cutover (admin only). rec_book_ship MUST become
2026-10-15-match-rec on /health.

HIGH-WATER $50 — 10/5 16-lot 773C 0.79 ran to ~1.67 (~+$1,408).
The fill $0.15 stop (0.64) would give back ~$1,650. That is not the exit.
Ratchet ticket_peak_unrealized on the BID every manage tick.
Once peak >= $50 AND age >= 90s, if peak - unrealized >= $50 => flatten
peak_giveback (ladder to market). A 1.67 high flattens near 1.63, not 0.64.
A 62s $78 flicker (10/9 10:45) HOLDs. Fill $0.15 (0DTE) / $0.30 (1DTE)
still protect tickets with no high, from FILL not from print high.
Quote grace 8s. Do NOT STC on BTO fill. Extra BTO on RUN stays.

QUALITY BAR — SUB SMS = Tradier BTO. Alerts that fire should be 3-minute
confirmed trends still moving in the last minute, not 1-minute prints
and not stalled 3-minute rips. Impactful winners (10:54 BEAR
−0.28 / −0.87, 10/5 12:22 +0.175 / +0.235) still SEND and extra-BTO
to 16 on RUN (MFE ≥ $0.20). Admin ledger still records the skips.

OVERLAY TAPE — every Candidate MUST be stamped AT SEAL before ingest/decide_sub_send:
  from engine.overlay.publish import at_seal, with_spy_deltas
  from engine.shared.gates import signed_spy_deltas
  c = at_seal(c, last_four_1m_spy_closes_at_send_ts)  # oldest → now, not the arm bar
  # or with_spy_deltas(c, last_four_1m_spy_closes_at_send_ts)
  # or Candidate(..., **signed_spy_deltas(last_four_1m_spy_closes_at_send_ts))
Up is +. Need 4 closes. Short tape => None, None => skip_1min_unconfirmed
(no SMS, no BTO). Do NOT default 1m/3m. Do NOT inject trend_confirm_kwargs
on live sends (that is replay-only). Do NOT reuse the arm-bar four after
PUBLISH_ARM_SEC. 10/15 A 10:26 BEAR / C 12:45 BULL / D 13:12 BEAR /
G 15:20 BULL SENT because quality was the arm tape. Rec already stamps
the send bar. Flatten/hold is not this lock.

1-MINUTE RIP — 10/2 SUB losers were 1-minute prints. Do not send those.
SUB requires the 3-minute move in the send direction of at least $0.20.
If abs(1m) >= abs(3m) and abs(1m) >= $0.20, it is a one-bar climax => skip_1min_rip.
Also skip when abs(1m) >= $0.50 AND abs(1m) >= 80% of abs(3m) (10/8 12:17
BULL +1.55 / +1.61 was 96% of the 3-minute in one bar, then the dump).
Floor stays $0.50 so 9/28 10:42 BEAR −0.24 / −0.26 (93% grind) still SENDS.
10/2 11:42 BULL +0.44 / +0.12 => skip_1min_rip.
10/2 10:34 BEAR +0.16 / +0.08 => skip_1min_rip.
10/8 12:17 BULL +1.55 / +1.61 => skip_1min_rip (climax, not a trend).
10/2 10:54 BEAR −0.28 / −0.87 => SEND (3-minute dump). extra BTO on RUN.
10/5 12:22 BULL +0.175 / +0.235 => SEND.
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
This is NOT the 9/30 10:39 fresh-fill scratch, and NOT the 10/8 13:20
9s unstamped scratch. recover_lost:
  qty 0 => flat
  qty > 0 and no fill stamp => stamp now, fresh_fill (HOLD_UNSTAMPED_FILL)
  ticket_fill_ts set and age < 15s => fresh_fill (do NOT STC)
  ticket_fill_ts set and age >= 15s => owned (manage loop; do NOT STC here)
  HOLD_UNSTAMPED_FILL false and no stamp => adopt_stc_only, orphan_adopt=true
If orphan_adopt: before_stc / on_manage MUST flatten (reason=orphan_adopt)
even if the envelope has not printed. Do not HOLD a true orphan.
Owned 1DTE still HOLD until $0.30 / ticket_risk / 15:50.
A 1DTE 2.71→2.16 (7-lot, 90m) must flatten_protective on the manage tick.
A 9s unstamped fill must HOLD (fresh_fill), not flatten_orphan_adopt.

1DTE ADAPTIVE PROTECT — 10/1 $0.15 clipped 1DTE path winners.
0DTE keeps protective $0.15. 0DTE fail_90 only if SPY reversed $0.30
(FAIL_90_REQUIRES_REVERSAL). A 91s 0DTE with spy_adverse $0.10 must HOLD.
A 91s 0DTE with spy_adverse $0.30 is fail_90. Protective $0.15 still
flattens a real option dump with no SPY wait. 1DTE protective $0.30.
1DTE does NOT fail_90. A 14:04 1DTE −$0.21 option dip must HOLD.
A 14:26 1DTE 90s tick must HOLD. Ticket risk −$240 and 15:50 flatten stay.
Do not freeze 0DTE TREND.

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
   Overlay MUST select 1DTE in 10:00–10:20 and from 12:30
   (starter_dte_for_clock). 1DTE TREND still sends/posts in those windows
   (9/25; 10/8 12:37 dump). 10:21–12:29 0DTE still posts. Publish is
   09:30–15:50 with no dark gap. 12:45+ leftover 0DTE is
   skip_0dte_after_cutover; 1DTE is the book (15:16 runner).

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

5) DTE clock: overlay scores 1DTE in 10:00–10:20 and from 12:30
   (TREND until 12:45). From 12:45 leftover 0DTE is skip_0dte_after_cutover;
   trade 1DTE. STC-hold stays. 1DTE FAILs after 12:45 count toward the
   4-fail halt. in_publish_window is 09:30–15:50 (no 09:41–09:59 hole).

6) recover_lost every scan that sees broker_qty:
   kind = state.recover_lost(broker_qty)
   if kind == "fresh_fill": do NOT STC (includes first unstamped sight)
   if kind == "owned": call on_manage / before_stc as normal (envelope may flatten)
   if kind == "adopt_stc_only": before_stc MUST post (orphan_adopt). Walk ladder.
   NEVER BTO from recover_lost.

Proof: rec_book_ship=2026-10-15-match-rec, code_ship MUST equal
rec_book_ship (FAIL if 2026-09-29b), rec_book_identity=true,
rec_book_mismatch=[], rec_revenue_lock=true,
quality_at_seal=true, sub_clock_is_tradier_clock=true,
overlay_sub_forces_queued=true,
inflight_does_not_consume=true, after_recover_lost=true, min_hold_sec=90,
stc_requires_before_stc=true, stc_recomputes_envelope=true,
native_close_push=false, morning_rip_flatten=false,
peak_starter_giveback=false, peak_starter_giveback_usd=0,
protect_from_high=true,
clock_identity_lock=true, sms_at_seal=true,
overlay_iff_sms=true, follow_delivered_sub=false,
clock=America/New_York unix send instant,
clock_fallback=historical_audits_only,
peak_giveback_min_sec=90, open_fade_1dte_book=true,
rth_start_et=09:30, rth_end_et=15:50,
hold_unstamped_fill=true, fresh_fill_sec=15,
fail_90_requires_reversal=true, channels_aligned=true,
sms_iff_sub_send=true, sms_from=sub_alert_send, skip_0dte_open_fade=true,
open_fade_window=["10:00","10:20"], skip_0dte_near_cutover=true,
near_cutover_0dte_et=12:30, skip_already_flat=true, flatten_limit_thru_usd=0,
dte_book_et=12:30, trade_1dte_from_book_et=true,
dte_cutover_et=12:45, trade_1dte_after_cutover=true,
protective_stop_usd=0.15, protective_stop_1dte_usd=0.30, fail_sec_1dte=null,
recover_lost_owned=true, orphan_adopt_flattens=true,
skip_1min_rip=true, one_min_rip_usd=0.20, one_min_climax_frac=0.80,
one_min_climax_usd=0.50, trend_3m_min_usd=0.20,
skip_1min_stall=true, stall_1m_usd=0.08.
REQUIRE peak_giveback_usd=50 / session_loss_halt_usd=750 /
starter_notional_usd=2000 / ticket_risk_usd=240 / extra_bto=true /
extra_bto_on_run=true / extra_bto_fill_to=16 / risk_qty_cap=16
on live. Overwrite Railway $75 / $1125 / $3000 / $360 / extra off / fill_to 24.
A 10:01 0DTE must be skip_0dte_open_fade on SMS AND BTO. A 10:14 0DTE BEAR
must be skip_0dte_open_fade. A 12:44 0DTE BULL must be skip_0dte_near_cutover.
An 11:48 0DTE and a 15:16 1DTE must still SEND and POST.
A 1DTE −$0.21 mark in 60s must HOLD. A 0DTE −$0.15 mark must still flatten.
A 0DTE 91s tick with spy_adverse $0.10 must HOLD. With spy_adverse $0.30
it is fail_90. A 1DTE 7-lot 2.71→2.16 must flatten_protective. An
unstamped live fill is fresh_fill (do not orphan-STC in 9s). A 3s stamped
fill is still fresh_fill. A 09:50 0DTE TREND must still SEND. A 10:14
0DTE is skip_0dte_open_fade; a 10:14 1DTE TREND must SEND.
A 11:42 1-min +0.44 / 3-min +0.12 must be skip_1min_rip on SMS AND BTO.
A 10:54 BEAR 1-min −0.28 / 3-min −0.87 must still SEND and POST.
A 10/8 12:17 BULL +1.55 / +1.61 must be skip_1min_rip on SMS AND BTO.
A 9/28 10:42 BEAR −0.24 / −0.26 must still SEND (grind, not a $0.50 spike).
A 10/8 12:37 0DTE is skip_0dte_near_cutover. A 12:37 1DTE TREND BEAR
−0.22 / −0.67 must SEND and POST (starter_dte_for_clock=1).
A 10/5 10:58 BULL +0.03 / +0.505 must be skip_1min_stall.
A 10/5 12:22 BULL +0.175 / +0.235 must still SEND and POST.
A 10/15 10:26 BEAR into a rising 3-minute (arm dump, seal rally) must
be skip_1min_rip at seal. C 12:45 stall, D 13:12 BEAR into rally, and
G 15:20 $0.50/80% climax must be skip_1min_rip at seal. Arm-bar 1m/3m
must not survive PUBLISH_ARM_SEC. Flatten/hold is a separate track.
A 16-lot 0.79 fill marked 1.67 must HOLD. Marked 1.63 after 90s
must flatten_peak_giveback. Do not sit to fill-0.15 (0.64).
A 13-lot 1.17 marked 1.10 at 62s with peak $78 must HOLD. Same mark
at 90s is peak_giveback. Marked 1.02 at 20s is flatten_protective.
A 24-lot 1.235 marked 1.21 at 5s (10/9 C) must HOLD even if
orphan_adopt. A 14-lot 1.02 marked 0.99 at 7s (10/9 D) must HOLD.
A 7-lot 2.00 marked 2.02 at 12s (10/9 E) must HOLD. A 15-lot 0.94
marked 0.90 at 185s with spy_adverse $0.10 (10/9 B) must HOLD.
SMS publication time for send_ts 1791557156.23214 is 10:45:56, not 10:32.
SMS publication time for send_ts 1791558455.695518 is 11:07:35 BEAR puts
777/776, not 11:04:45 BULL calls 777/778.
10/12 overlay SUB markers MUST equal Pushover clocks 10:35:53, 11:49:03,
12:22:52. An overlay-only 11:13 vs those three texts is a FAIL.
Do not SMS follow_delivered_sub receipts. Do not plot a seal that was
not texted.
A 14-lot 1.07 marked 1.05 at 4s (10/13 10:29) must HOLD even if
recover_lost_flat / broker_flat / on_manage. A 12-lot 1.19 marked
1.15 at 122s (10/13 09:46, −$48) must HOLD even if the claimed
reason is peak_giveback / protective / fail_90 — that is not
$0.15 and not $50 peak. morning_rip_flatten at 09:45 must HOLD.
peak_starter_giveback must not flatten. A 12-lot 1.19 marked 1.04
at 20s is flatten_protective. 15:50 leftover is rth_end.
Do not walk the STC ladder unless may_post_stc / before_stc /
on_manage flatten is true.
flatten_exits_durable=true, flatten_exits_keep_days=30,
one_dte_protective_short_ladder=true,
one_dte_protective_market_after_sec=2,
one_dte_qty_cap=6, extra_bto_1dte=false,
one_dte_scaled_giveback=false, on_ladder_step=true,
on_flatten_fill=true.
A 1DTE 6-lot 2.318 marked 2.018 is flatten_protective with a
2-step ladder (bid then market in 2s). A 0DTE 1.25 protective
is still 4 steps. A 1DTE 2.63 ask sizes 6, not 10. 1DTE extra
    BTO is top_up_blocked. 10/5 16-lot 0DTE still extra_bto to 16
    and still peak_giveback $50.

11) MATCH REC DOLLARS: Tradier cash must be the Rec book, not leftover
    size. Overwrite (do not merge-keep):
      starter_notional_usd=2000
      notional_usd=2000
      live_notional_usd=2000
      ticket_risk_usd=240
      risk_qty_cap=16
      extra_bto=true
      extra_bto_on_run=true
      extra_bto_fill_to=16
      extra_bto_1dte=false
      one_dte_qty_cap=6
      session_loss_halt_usd=750
      peak_giveback_usd=50
    Kill live extra_bto=false / fill_to 24 / extra_bto_on_run=false /
    starter $3000 / halt $1125 / ticket $360 / peak $75.
    0DTE extra BTO on RUN to 16 is how Rec sizes 10/5 12:22.
    1DTE stays cap 6 / no extra. A 2.63 1DTE ask sizes 6, not 10.

12) REC REVENUE LOCK: Rec IS the book. Do not retune the skip bar,
    extra BTO, halt, 90s hold, or $50 peak for one-day tape.
    rec_revenue_lock=true.
    stall_1m_usd=0.08, trend_3m_min_usd=0.20, chase_spy=0.50,
    one_min_climax_usd=0.50, one_min_climax_frac=0.80,
    skip_choppy_is_refuse=true, chop_size=false,
    extra_bto=true, extra_bto_on_run=true, extra_bto_fill_to=16,
    session_loss_halt_usd=750, peak_giveback_usd=50,
    min_hold_sec=90, stc_recomputes_envelope=true.
    10/5 12:22 +0.175 / +0.235 MUST still SEND (raising 3m to $0.30
    skips it). 10/8 12:17 +1.55 / +1.61 MUST stay skip_1min_rip.
    10/5 16-lot 0.79 marked 1.67 MUST HOLD until $50 giveback after 90s
    — do not sit to fill $0.15. A 20s fill $0.15 dump still flattens.
    9/29 CHOPPY is skip, not chop_size.

13) SUB CLOCK = TRADIER CLOCK: One unix send_ts is SMS publication_et,
    overlay marker, and before_bto consume. Pass publication_et into
    before_bto; mismatch is skip_clock_identity and does NOT consume.
    overlay_sub_forces_queued=true: plot=sub_alert_send is overlay-queued,
    so an opposite SUB (10/15 B 11:47 after A 10:26) POSTS. Do not invent
    opposites (queue_opposite=false still). extra BTO on an exec opposite
    stays blocked. Twilio body is et_hms_from_ts(send_ts). follow_delivered_sub
    stays false. overlay_sends MUST equal those publication clocks.

14) INFLIGHT DOES NOT CONSUME: halt still consumes (lift cannot recycle).
    pending_entry / inflight / broker_qty is skip_inflight WITHOUT
    consume. 10/15 B 11:47 and F 14:49 were burned with no order.
    When the mutex clears, the same send_ts may POST.

15) AFTER RECOVER LOST: kind=flat / qty=0 is skip_already_flat — never
    walk the ladder. fresh_fill HOLDs. owned goes through on_manage /
    may_post_stc (envelope only). 10/15 C 12:45 2.73→2.71 at 4s, D 13:12
    2.83→2.81 at 2s, G 15:20 2.71→2.70 at 7s HOLD. 10/15 A 10:26 2.05 vs
    1.83 at 22s is still flatten_protective. Call after_recover_lost
    from the lost-scan. Do not STC on recover_lost_flat / broker_flat.
    This is the EXIT track. A/C/D/G send clocks are item 16.

16) ENTRY AT SEAL: 10/15 overlay A 10:26 BEAR, C 12:45 BULL, D 13:12
    BEAR, G 15:20 BULL erred at decide_sub_send, not STC. Live stamped
    rip_1m_spy / trend_3m_spy at arm, waited PUBLISH_ARM_SEC (180s),
    then sent into the opposite / stalled / climax tape. Rec stamps
    the last four 1-minute closes on the SEND bar.
      from engine.overlay.publish import at_seal
      c = at_seal(c, last_four_1m_spy_closes_at_send_ts)
      d = decide_sub_send(c)  # or decide_sub_send(c, closes)
    quality_at_seal=true. Arm-bar 1m/3m must not survive the wait.
    Missing seal tape is skip_1min_unconfirmed (no SMS, no BTO).
    A 10:26 BEAR into a rising 3-minute is skip_1min_rip.
    C 12:45 last-minute stall is skip_1min_rip.
    D 13:12 BEAR into a rally is skip_1min_rip.
    G 15:20 $0.50 / 80% climax is skip_1min_rip.
    10/2 10:54 −0.28 / −0.87 and 10/5 12:22 +0.175 / +0.235 still
    SEND at seal. Do not retune stall $0.08, 3m $0.20, chase $0.50,
    or climax $0.50/80%. Do not mix this with flatten/hold.

17) SINGULAR REC BOOK: REPLACE live with GitHub rec_book().
    Do not keep a patched 2nd version. code_ship == rec_book_ship
    == 2026-10-15-match-rec on tradier_exec AND logic_rebuild
    AND performance AND cover_paths. rec_book_identity=true.
    rec_book_mismatch=[]. health_is_rec_book=true.
    do_not_merge_leftover_knobs=true. DELETE peak_from_print,
    never_green_withhold, opposite_sub_consumes, qty_max=100.
    Rec: peak_from_print=false, never_green_withhold=false,
    opposite_sub_consumes=false, qty_max=16. A leftover name
    that is still True is skip_not_rec_book and MUST NOT consume.
    Rec process still POSTs 10/5 12:22.

```


## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates engine.tradier_exec.tests.test_runtime engine.tradier_exec.tests.test_week_replay engine.tradier_exec.tests.test_two_week_gates
python3 -m engine.tradier_exec.replay
python3 -m engine.tradier_exec.week_replay
python3 -m engine.tradier_exec.two_week_gates
```

RTH-hold vs stall-1m, last 11 sessions (9/24–10/8 SPY 1m):
`python3 -m engine.tradier_exec.two_week_gates`. 19 climax minutes the old
book would have sent are skipped (including 10/8 12:17). 41 1DTE TREND
minutes in 12:30–12:44 and 74 1DTE TREND minutes in 10:00–10:20 light up.
85 former dark 09:30–09:35 / 09:41–09:59 minutes now sit in-window.
9/24 10:43, 9/25 11:55, 9/28 10:42, 10/2 10:54, 10/5 12:22 still SEND.

30 trading days through 9/29, weekly Rec vs live: `python3 -m engine.tradier_exec.week_replay`.
Rec tape is 9/15–9/29 (envelope + 0DTE fail-streak + extra BTO on RUN + 1DTE skip
unless TREND). Earlier weeks have no SPY lots; Rec equals live (impact $0).
Halt resets on the ET date.
