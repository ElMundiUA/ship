"""Linear OAuth + tracker adapter (pilot Day 2 — tracker WOW flow).

The wizard's "Connect Linear" button hits ``POST /v1/integrations/linear/
install/start`` which mints a signed state token (workspace_id + CSRF
nonce) and returns Linear's authorize URL. After the user approves the
OAuth dance Linear redirects to ``GET /v1/integrations/linear/install/
callback?code=&state=`` on the API origin; we exchange the code for an
access token, encrypt it via the existing :mod:`backend.app.security
.encryption` Fernet key and persist a generic
:class:`backend.app.db.models.tenancy.Integration` row with ``kind=
"linear"``.

The ``LinearTracker`` adapter then implements
:class:`backend.app.integrations.gateway.tracker.TrackerGateway` against
Linear's GraphQL API, talking over the cached access token.
"""

from __future__ import annotations
