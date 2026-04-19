"""Vendor integrations live here, behind typed Gateway interfaces.

The package is organised vendor-first under :mod:`backend.app.integrations`
so that a future ``broker`` (self-hosted) gateway can sit alongside
``github``, ``linear``, ``notion`` etc. without surgical changes to the rest
of the codebase. Domain code (controllers, pipelines, resolvers) depends on
the abstract interfaces in :mod:`backend.app.integrations.gateway`, never on
a concrete adapter directly. DI selects the concrete adapter from
``workspace.code_host_kind`` / ``tracker_kind``.
"""
