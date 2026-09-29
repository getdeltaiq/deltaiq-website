# Exec cover-us gates (GitHub source of truth)

Railway is not the source of this logic. **This directory is.** `tradier_exec` on Railway must import or vendor `exec/gates.py` and call `decide_starter` / `decide_manage` before any Tradier order. Do not keep a second knob-only copy on Railway.

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

## Halt (rest of a blown session)

Keep `session_halt` on after `session_loss_after_lift`. New session date (America/New_York) calls `new_session()`.

## Test

```bash
python3 -m unittest exec.tests.test_gates
```
