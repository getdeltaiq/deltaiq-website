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
- `/health` after deploy must show `gates_module=engine.shared.gates` and `overlay.git_sha` = this commit.

## Test

```bash
python3 -m unittest engine.overlay.tests.test_publish exec.tests.test_gates
```
