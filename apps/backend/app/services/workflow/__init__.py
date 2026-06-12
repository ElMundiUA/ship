"""Deterministic workflow primitive (thesis 8, Phase 8).

Bounded imperative fan-outs invoked for ONE job — complementary to
the FSM (reactive per-ticket lifecycle) and to /process (state-machine
definitions). Modules:

- :mod:`spec` — the ``.ship/workflows/*.yaml`` definition language.
- :mod:`gate` — the single control-plane chokepoint every leaf spawn
  passes through (lease / cap / cascade / idempotency).
- :mod:`runtime` — the DAG executor.
"""
