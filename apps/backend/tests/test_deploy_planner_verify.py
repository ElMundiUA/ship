from __future__ import annotations

from backend.app.services.deploy.plan import DeployComponent, DeployPlan, EnvVarSpec
from backend.app.services.deploy.planner import _verify_plan


def _plan(frontend_api_base: str) -> DeployPlan:
    return DeployPlan(
        summary="Janky monorepo with a static frontend and Express API.",
        app_name="monorepo-janky",
        confidence="high",
        components=[
            DeployComponent(
                name="api",
                kind="service",
                runtime="node-js",
                source_dir="api",
                run_command="npm start",
                http_port=8080,
                routes=["/api"],
                health_check_path="/healthz",
                env=[EnvVarSpec(key="HOST", value="0.0.0.0", secret=False)],
            ),
            DeployComponent(
                name="dashboard",
                kind="static_site",
                runtime="static",
                source_dir="dashboard",
                build_command="npm run build",
                output_dir="dist",
                routes=["/"],
                env=[
                    EnvVarSpec(
                        key="VITE_BACKEND_BASE",
                        value=frontend_api_base,
                        secret=False,
                    )
                ],
            ),
        ],
    )


def test_verify_plan_rejects_app_url_without_backend_route_prefix() -> None:
    issues = _verify_plan(
        _plan("$APP_URL"),
        files={
            "dashboard/src/App.jsx": (
                "const base = import.meta.env.VITE_BACKEND_BASE || "
                "'http://localhost:3001';"
            )
        },
        paths=[
            "api/package.json",
            "api/server.js",
            "dashboard/package.json",
            "dashboard/src/App.jsx",
        ],
    )

    assert issues
    assert "$APP_URL/api" in issues[0]


def test_verify_plan_accepts_app_url_with_backend_route_prefix() -> None:
    issues = _verify_plan(
        _plan("$APP_URL/api"),
        files={
            "dashboard/src/App.jsx": (
                "const base = import.meta.env.VITE_BACKEND_BASE || "
                "'http://localhost:3001';"
            )
        },
        paths=[
            "api/package.json",
            "api/server.js",
            "dashboard/package.json",
            "dashboard/src/App.jsx",
        ],
    )

    assert issues == []


def test_verify_plan_rejects_bare_app_url_even_without_loopback_excerpt() -> None:
    issues = _verify_plan(
        _plan("$APP_URL"),
        files={"dashboard/package.json": '{"scripts":{"build":"vite build"}}'},
        paths=[
            "api/package.json",
            "api/server.js",
            "dashboard/package.json",
            "dashboard/src/App.jsx",
        ],
    )

    assert issues
    assert "$APP_URL/api" in issues[0]
