"""Agent services (C12 — real agent).

Sub-modules:

- :mod:`backend.app.services.agent.client` — :class:`AgentClient` Protocol
  and the OpenAI / Anthropic implementations. Call sites depend on the
  Protocol so swapping vendors is one env flip.
- :mod:`backend.app.services.agent.embedding` — one-shot OpenAI embeddings
  used by the KB indexer and TopicService (bucket retrieval).
- :mod:`backend.app.services.agent.kb_indexer` — ingests
  ``.ship/knowledge/**/*.md`` from each activated repo into ``kb_chunks``
  with pgvector embeddings.
- :mod:`backend.app.services.agent.tools` — :class:`ToolBox` wiring every
  tool the agent can call (repo KB search, file fetch, ticket creation,
  artifact feedback, recent activity, bucket retrieval) behind a JSON
  schema each vendor SDK understands.
- :mod:`backend.app.services.agent.topic` — :class:`TopicService`: topic-
  shift classifier, bucket retrieval, conversation packing / summarising,
  message assembly.
"""

from __future__ import annotations
