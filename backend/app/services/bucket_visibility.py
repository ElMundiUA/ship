"""Shared "which buckets can this user read?" predicate (Phase 8).

The Phase 3 resolver (``GET /buckets/resolved``) already enforces the
privacy contract for *listing*: a caller sees workspace + project +
repo buckets freely, but only their own ``scope=user`` rows. That
predicate is duplicated implicitly in two other code paths that today
do **not** respect it:

1. :meth:`TopicService.retrieve_buckets` — packs "warmed" agent-memory
   into every chat turn.
2. ``search_buckets`` agent tool — lets the LLM search across packed
   memory mid-turn.

Without this helper both paths would happily return *other users'*
``scope=user`` memory to the calling user. That's the biggest gap
vs the Phase 3 guarantee, so Phase 8 centralises the visibility
predicate in one helper and reuses it everywhere.

Design
======

The helper is deliberately a thin SQL predicate (``ColumnElement``
returning ``BooleanClauseList``) rather than a service method —
callers compose it into their own ``select()``s and keep full
control over joins, ordering, and result shape. That's how we
avoid "N+1 style" double fetches.

The predicate:

- **Admits**: every non-USER scope owned by the workspace.
- **Admits**: USER-scoped rows where ``user_id == caller_id``.
- **Denies**: USER-scoped rows owned by a different user.

The workspace filter itself stays in the caller — every existing
query already has ``KnowledgeBucket.workspace_id == ws_id`` on it,
so this helper composes with that rather than duplicating it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_
from sqlalchemy.sql import ColumnElement

from backend.app.db.models.agent_memory import BucketScope, KnowledgeBucket


def visible_to_user_clause(
    caller_user_id: uuid.UUID,
) -> ColumnElement[bool]:
    """SQLAlchemy predicate for "this bucket is visible to ``caller_user_id``".

    Compose into any ``KnowledgeBucket`` ``select()`` that needs the
    same privacy semantics as :func:`get_resolved` (Phase 3). Does
    NOT scope to a workspace — the caller's existing workspace
    filter still applies.

    Truth table:

    ==========================  ================================
    ``scope_kind``              Visible?
    --------------------------  --------------------------------
    ``workspace``               always
    ``project``                 always (project-membership is
                                enforced one layer up; buckets
                                follow the project's membership)
    ``repo``                    always (same as project)
    ``user`` and ``user_id``    caller only
    matches
    ``user`` and ``user_id``    never
    differs
    ==========================  ================================
    """

    return or_(
        KnowledgeBucket.scope_kind != BucketScope.USER,
        and_(
            KnowledgeBucket.scope_kind == BucketScope.USER,
            KnowledgeBucket.user_id == caller_user_id,
        ),
    )


__all__ = ["visible_to_user_clause"]
