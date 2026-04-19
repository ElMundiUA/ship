"""GitHub vendor namespace: App OAuth + Code-host adapter + Webhooks.

Pilot scope (Day 1):
- :mod:`backend.app.integrations.github.app_auth` — JWT/Installation token plumbing
- :mod:`backend.app.integrations.github.oauth` — install start/callback URL builders
- :mod:`backend.app.integrations.github.webhook` — signature verification helper
- :mod:`backend.app.integrations.github.code_host_adapter` — :class:`CodeHostGateway` impl

The HTTP routes that wire these into FastAPI live in
:mod:`backend.app.api.v1.routes.github_app`.
"""
