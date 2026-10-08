# Exec cover-us gates (GitHub source of truth)

Railway is not the source of this logic. **`engine/` is.** This directory is a compatibility shim.

- Overlay (admin vs sub): `engine/overlay/publish.py`
- Tradier entry/exit: `engine/shared/gates.py` + `engine/tradier_exec/hooks.py`
- `exec/gates.py` re-exports `engine.shared.gates` for existing tests.

`tradier_exec` on Railway must import `engine.tradier_exec.hooks` (`before_bto` / `on_manage`) or vendor `engine/shared/gates.py`. Do not keep a second knob-only copy on Railway.

## Contract

- **Admin** (`admin_alert_ledger`): every scored/armed candidate.
- **Sub** (`sub_alert_send`): quality-gated `armed_rip` only. SMS and Tradier consume **sub only**.
- Invariant: `admin_n >= sub_n`.

## What this closes

| Gap | Function |
|---|---|
| 10:00 14+14 race | `try_consume` before POST; inflight mutex |
| FAIL recycle (10:13, 10:24, 10:49, 12:56, 13:52) | consume survives flatten |
| `recover_lost` BTO | `recover_lost` adopts/STC only |
| Enter, market goes opposite | `bounce_against` $0.30 / 10:00–10:02 |
| 16-lot held to −$336 | `envelope_hit` on **broker qty**; STC ladder → **market** |
| Halt cleared on flatten | `on_flatten` refreshes halt; does not clear it |
| Size | `starter_qty = min(floor(2000 / (ask * 100)), 16)` never cap 8 |
| CHOPPY / weak / chase still bought | `quality_skip_reason` in `before_bto`; fail closed if fields missing; `chop_size=False` |
| 1DTE misfire in RANGE/CHOP before 12:45 | `skip_1dte_not_trend` unless `regime=TREND` |
| After 12:45 still looking for 0DTE / skipping 1DTE | `dte_cutover_et=12:45`; trade 1DTE; `skip_0dte_after_cutover` |
| 10/8 12:30–12:44 dump had no product | Overlay `starter_dte_for_clock` is 1 from **12:30**; leftover 0DTE stays `skip_0dte_near_cutover`; 1DTE TREND posts |
| 10/8 12:17 +$1.55 / +$1.61 climax then dump | `skip_1min_rip` when last minute ≥ 80% of 3-minute **and** ≥ $0.50 |
| 9/30 10:14 −$190 and 12:44 −$256 sub sends | `skip_0dte_open_fade` 10:00–10:20; `skip_0dte_near_cutover` 12:30–12:44; 1DTE still posts |
| 10/1 10:01 SUB SMS with no Tradier BTO | Overlay + Tradier share `sub_action_skip_reason`; SMS iff `decide_sub_send`; 10:01 0DTE is admin-only |
| 10/1 $0.15 clipped 1DTE path winners | 0DTE keep $0.15 + fail_90; 1DTE protective $0.30 and no fail_90; ticket_risk −$240 stays |
| Bid-scratch + rejected STC spray | `before_stc`; `skip_already_flat` when broker qty 0; no close_push |
| Rapid FAIL re-entry | `skip_cooldown_after_fail` (480s); `COOLDOWN_AFTER_FAIL_SEC` is wired |
| Trail held 16-lot to −$336 | `apply_manage_result` sets `disable_trail`; `engine_exit_mode=ladder_to_market` |
| consumed_send_ts [] after flatten | `persist=` INSERT ON CONFLICT; `load_consumed` at boot; never DELETE |
| 0-for-N never halted until a lift | first-line halt: −$750 (3 envelope misses) or 4 **0DTE** FAILs; 1DTE does not increment the streak |
| 9/30 10:39 bid scratch in 3s | `before_stc`; HOLD `take_exit=hold`; no working STC on fill; recover_lost `fresh_fill` |
| 10/2 1-minute fade rips (11:42, 12:13, …) | `skip_1min_rip` unless 3-minute SPY trend confirms; missing 1m/3m is `skip_1min_unconfirmed` |
| 10/5 773C 16-lot ~+$1,400 held toward fill $0.15 | Ratchet bid high; flatten `peak_giveback` at $50 off that high |

## Halt (rest of a blown session)

Keep `session_halt` on after `session_loss_after_lift`. New session date (America/New_York) calls `new_session()`.

## Test

```bash
python3 -m unittest exec.tests.test_gates
```

## Tradier / Cursor exec agent (run this, do not lift halt)

Copy `exec/gates.py` and `exec/shore_up_tradier_exec.py` into the tradier_exec repo, then:

```bash
python3 shore_up_tradier_exec.py --apply --dest .
python3 shore_up_tradier_exec.py --test
python3 shore_up_tradier_exec.py --find-bto --dest .
psql "$DATABASE_URL" -f consumed_sends.sql
```

Keep `session_halt` on for `2026-09-29`. Wire `before_bto` / `on_manage` from `cover_us_hooks.py` at every BTO and manage tick. Details in `WIRE_CONTRACT.txt` after `--apply`.
