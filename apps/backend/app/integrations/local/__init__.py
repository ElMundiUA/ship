"""Laptop-offline Memory adapters (E19).

The vendor-agnostic protocol contracts in
``backend.app.integrations.gateway`` admit any backend that fulfils
their shape — the rest of Ship is provider-agnostic by design. The
adapters in this package are the *offline* implementation: they
store everything in our own Postgres (``memory_tracker_*`` /
``memory_git_*`` / ``memory_ci_runs`` tables, see migration
``0073_local_memory_adapters``) so a developer can run the whole
orchestrator + console without any external account.

Use cases:

- ``make dev-up`` profile — laptop offline by default.
- E2E suite — deterministic fixtures via DB seed, no LLM tokens
  or Linear/GitHub OAuth dance per run.
- Onboarding new contributors — clone, ``make dev-up``, hack.

Resolution: when ``SHIP_USE_MEMORY_ADAPTERS=true`` in the active
settings, the tracker / code-host / CI resolvers short-circuit to
these adapters before consulting any installation row. The flag is
on by default in ``.env.shared`` so the laptop profile picks them
up; production never sets it.
"""

from backend.app.integrations.local.ci import MemoryCi
from backend.app.integrations.local.code_host import MemoryCodeHost
from backend.app.integrations.local.tracker import MemoryTracker


__all__ = ["MemoryCi", "MemoryCodeHost", "MemoryTracker"]
