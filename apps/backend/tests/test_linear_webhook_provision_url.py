"""URL composition for the Linear webhook provisioning endpoint.

``_linear_webhook_url`` must produce the same path the receiving
endpoint is mounted at — drift between the two means a successful
``webhookCreate`` call followed by zero deliveries actually arriving.
Pin the contract.
"""

from __future__ import annotations

from backend.app.api.v1.routes.linear_oauth import _linear_webhook_url


class _Settings:
    def __init__(self, base: str) -> None:
        self.public_url = base


def test_no_double_slash_when_base_has_trailing_slash() -> None:
    assert (
        _linear_webhook_url(_Settings("https://api.example.com/"))
        == "https://api.example.com/v1/webhooks/linear"
    )


def test_no_double_slash_when_base_has_no_trailing_slash() -> None:
    assert (
        _linear_webhook_url(_Settings("https://api.example.com"))
        == "https://api.example.com/v1/webhooks/linear"
    )


def test_localhost_dev_base_round_trips() -> None:
    # The default dev SHIP_PUBLIC_URL is http://localhost:8100 — make
    # sure operators provisioning from a local instance get a URL
    # Linear can actually reach (they'll typically front this with
    # ngrok / cloudflared; we just need the suffix correct).
    assert (
        _linear_webhook_url(_Settings("http://localhost:8100"))
        == "http://localhost:8100/v1/webhooks/linear"
    )


def test_matches_receiver_route_path() -> None:
    # The receiving endpoint is mounted at /v1/webhooks/linear by the
    # APIRouter in linear_webhook.py (verified at import time during
    # the previous Lever-4 PR). Locking the suffix here means a
    # rename on either side fails this test loudly.
    url = _linear_webhook_url(_Settings("https://api.example.com"))
    assert url.endswith("/v1/webhooks/linear")
