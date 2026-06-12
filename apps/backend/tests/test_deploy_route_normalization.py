from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.app.api.v1.routes.deploy import _normalize_do_frontend_api_base, _to_out
from backend.app.db.models.deploy import Deployment
from backend.app.db.models.deploy import DeploymentStatus as DS
from backend.app.services.deploy.plan import (
    DeployComponent,
    DeployConnection,
    DeployPlan,
    EnvVarSpec,
)


def test_normalize_do_frontend_api_base_uses_service_route_not_health_route() -> None:
    plan = DeployPlan(
        summary="Monorepo with API and Vite dashboard.",
        app_name="monorepo-janky",
        confidence="high",
        components=[
            DeployComponent(
                name="api",
                kind="service",
                runtime="docker",
                source_dir="api",
                dockerfile_path="Dockerfile",
                http_port=5000,
                routes=["/api", "/healthz"],
                health_check_path="/healthz",
                env=[
                    EnvVarSpec(key="HOST", value="0.0.0.0"),
                    EnvVarSpec(key="PORT", value="5000", required=False),
                ],
            ),
            DeployComponent(
                name="dashboard",
                kind="static_site",
                runtime="static",
                source_dir="dashboard",
                routes=["/"],
                output_dir="dist",
                env=[
                    EnvVarSpec(
                        key="VITE_BACKEND_BASE",
                        value="$APP_URL",
                    )
                ],
            ),
        ],
    )

    fixed = _normalize_do_frontend_api_base(plan)

    dashboard = fixed.components[1]
    assert dashboard.env[0].value == "$APP_URL/api"
    assert fixed.connections[0].public_base_path == "/api"
    assert fixed.connections[0].value == "$APP_URL/api"
    assert plan.components[1].env[0].value == "$APP_URL"


def test_normalize_do_frontend_api_base_uses_explicit_connection_env_key() -> None:
    plan = DeployPlan(
        summary="Monorepo with explicit frontend/backend connection.",
        app_name="monorepo-janky",
        confidence="high",
        components=[
            DeployComponent(
                name="api",
                kind="service",
                runtime="node-js",
                source_dir="api",
                http_port=8080,
                routes=["/api"],
                health_check_path="/healthz",
            ),
            DeployComponent(
                name="dashboard",
                kind="static_site",
                runtime="static",
                source_dir="dashboard",
                output_dir="dist",
                env=[EnvVarSpec(key="VITE_DATA_URL", value="$APP_URL")],
            ),
        ],
        connections=[
            DeployConnection(
                from_component="dashboard",
                to_component="api",
                env_key="VITE_DATA_URL",
                public_base_path="/api",
                value="$APP_URL/api",
            )
        ],
    )

    fixed = _normalize_do_frontend_api_base(plan)

    assert fixed.components[1].env[0].value == "$APP_URL/api"


def test_to_out_exposes_provider_rollback_metadata() -> None:
    source_id = uuid.uuid4()
    dep = Deployment(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        repo_id=uuid.uuid4(),
        provider="digitalocean",
        status=DS.ACTIVE,
        plan={"summary": "ok"},
        provider_ref={
            "app_id": "app-1",
            "deployment_id": "dep-1",
            "rolled_back_from_id": str(source_id),
        },
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    out = _to_out(dep)

    assert out.can_provider_rollback is True
    assert out.rolled_back_from_id == str(source_id)
