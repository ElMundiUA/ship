"""Regression: the DigitalOcean adapter must canonicalize route globs.

A DO ingress route ``path`` is a literal PREFIX — globs are not supported.
When the LLM planner emits ``routes: ["/*"]`` (informally meaning
"everything"), the adapter previously passed it through verbatim, so DO
stored a route prefix of ``/*`` which matches no request — the app then
404s on ``/`` (confirmed live: ``/*`` → HTTP 404, ``/`` → HTTP 200).

Other providers (e.g. AWS ALB/CloudFront) accept globs, so this lives in
the DO adapter, next to ``_root_relative_dockerfile`` — it's a
provider-specific encoding detail, not a planner/IR concern.
"""

from __future__ import annotations

from backend.app.services.deploy.plan import DeployComponent
from backend.app.services.deploy.providers import digitalocean as do_adapter


def _static(routes: list[str]) -> DeployComponent:
    return DeployComponent(
        name="frontend",
        kind="static_site",
        runtime="node-js",
        source_dir="/",
        output_dir="dist",
        build_command="npm install && npm run build",
        routes=routes,
    )


def _service(routes: list[str]) -> DeployComponent:
    return DeployComponent(
        name="api",
        kind="service",
        runtime="node-js",
        source_dir="/",
        http_port=8080,
        routes=routes,
        health_check_path="/healthz",
    )


def _build_static(routes: list[str]) -> dict:
    return do_adapter._build_static_site(
        _static(routes), source_key="github", source_dict={}, operator={}
    )


def _build_svc(routes: list[str]) -> dict:
    return do_adapter._build_service(
        _service(routes), source_key="github", source_dict={}, operator={}
    )


def test_static_site_root_glob_becomes_root_prefix() -> None:
    assert _build_static(["/*"])["routes"] == [{"path": "/"}]


def test_static_site_subpath_glob_drops_wildcard() -> None:
    assert _build_static(["/api/*"])["routes"] == [{"path": "/api"}]


def test_service_root_glob_becomes_root_prefix() -> None:
    assert _build_svc(["/*"])["routes"] == [{"path": "/"}]


def test_plain_prefixes_are_unchanged() -> None:
    assert _build_static(["/"])["routes"] == [{"path": "/"}]
    assert _build_svc(["/api"])["routes"] == [{"path": "/api"}]
