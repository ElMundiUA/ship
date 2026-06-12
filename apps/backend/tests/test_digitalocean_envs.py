from __future__ import annotations

from backend.app.services.deploy.plan import DeployComponent, DeployPlan, EnvVarSpec
from backend.app.services.deploy.providers.digitalocean import (
    build_app_spec,
    merge_current_envs,
    spec_env_settings,
)


def _plan() -> DeployPlan:
    return DeployPlan(
        summary="api",
        app_name="api",
        confidence="high",
        components=[
            DeployComponent(
                name="api",
                kind="service",
                runtime="node-js",
                source_dir="/",
                http_port=8080,
                env=[
                    EnvVarSpec(key="HOST", value="0.0.0.0", required=False),
                    EnvVarSpec(key="DATABASE_URL", secret=True),
                ],
            )
        ],
    )


def test_secret_without_operator_value_is_not_sent_empty_on_create() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
    )

    envs = spec["services"][0]["envs"]
    assert {e["key"] for e in envs} == {"HOST"}
    assert envs[0]["value"] == "0.0.0.0"


def test_update_preserves_existing_encrypted_component_secret() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
    )
    merged = merge_current_envs(
        spec,
        {
            "services": [
                {
                    "name": "api",
                    "envs": [
                        {
                            "key": "DATABASE_URL",
                            "scope": "RUN_AND_BUILD_TIME",
                            "type": "SECRET",
                            "value": "EV[1:encrypted]",
                        }
                    ],
                }
            ]
        },
    )

    envs = {e["key"]: e for e in merged["services"][0]["envs"]}
    assert envs["DATABASE_URL"]["value"] == "EV[1:encrypted]"


def test_update_preserves_unknown_general_env() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
    )
    merged = merge_current_envs(
        spec,
        {
            "envs": [
                {
                    "key": "VITE_API_RUL",
                    "scope": "RUN_AND_BUILD_TIME",
                    "type": "GENERAL",
                    "value": "https://backend.example",
                }
            ]
        },
    )

    envs = {e["key"]: e for e in merged["envs"]}
    assert envs["VITE_API_RUL"]["value"] == "https://backend.example"


def test_operator_general_env_overrides_existing_general_env() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
        operator_env=[
            {"key": "VITE_API_RUL", "value": "https://new.example", "secret": False}
        ],
    )
    merged = merge_current_envs(
        spec,
        {
            "envs": [
                {
                    "key": "VITE_API_RUL",
                    "scope": "RUN_AND_BUILD_TIME",
                    "type": "GENERAL",
                    "value": "https://old.example",
                }
            ]
        },
    )

    envs = {e["key"]: e for e in merged["envs"]}
    assert envs["VITE_API_RUL"]["value"] == "https://new.example"


def test_operator_env_overrides_declared_secret_value() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
        operator_env=[
            {"key": "DATABASE_URL", "value": "postgres://new", "secret": True}
        ],
    )

    envs = {e["key"]: e for e in spec["services"][0]["envs"]}
    assert envs["DATABASE_URL"]["type"] == "SECRET"
    assert envs["DATABASE_URL"]["value"] == "postgres://new"


def test_freeform_operator_env_goes_to_app_level() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
        operator_env=[
            {"key": "VITE_API_RUL", "value": "https://backend.example", "secret": False}
        ],
    )

    assert spec["envs"] == [
        {
            "key": "VITE_API_RUL",
            "scope": "RUN_AND_BUILD_TIME",
            "type": "GENERAL",
            "value": "https://backend.example",
        }
    ]


def test_operator_env_value_with_key_prefix_is_normalized_before_spec() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
        operator_env=[
            {
                "key": "VITE_API_RUL",
                "value": "VITE_API_RUL=https://backend.example/health",
                "secret": False,
            }
        ],
    )

    assert spec["envs"][0]["value"] == "https://backend.example/health"


def test_empty_freeform_secret_is_not_sent_on_create() -> None:
    spec = build_app_spec(
        _plan(),
        repo_clone_url="https://github.com/acme/api.git",
        full_name="acme/api",
        branch="main",
        private=False,
        operator_env=[{"key": "API_KEY", "value": "", "secret": True}],
    )

    assert "envs" not in spec


def test_spec_env_settings_masks_secret_values() -> None:
    settings = spec_env_settings(
        {
            "envs": [
                {
                    "key": "VITE_API_RUL",
                    "type": "GENERAL",
                    "value": "https://backend.example",
                },
                {
                    "key": "DATABASE_URL",
                    "type": "SECRET",
                    "value": "EV[1:encrypted]",
                },
            ]
        }
    )

    by_key = {e["key"]: e for e in settings}
    assert by_key["VITE_API_RUL"]["value"] == "https://backend.example"
    assert by_key["DATABASE_URL"]["value"] is None
    assert by_key["DATABASE_URL"]["set"] is True
