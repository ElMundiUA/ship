# Navigator tools — internal reference

> **Audience:** Ship backend + frontend engineers adding or modifying
> tools the in-product Navigator (C12 chat at `/chat`) can call.
>
> **Last updated:** Phase 6 (Inbox / Plays / Automations / Runs /
> Coverage / Repo intel surfaces). For product framing see
> `documentation/protocol/rfc-0010-plays-and-inbox.md` and
> `documentation/internal/inbox-redesign-planning.md`.

---

## §1 · Where Navigator tools live

| Concern | File |
|---|---|
| Tool registry, JSON-schema specs, dispatch | `backend/app/services/agent/tools.py` (single `ToolBox` class) |
| System prompt that teaches the model **when** to use them | `backend/app/services/agent/topic.py` (`_AGENT_SYSTEM_PROMPT`) |
| Per-turn orchestration (tool loop, cost guard, audit commit) | `backend/app/api/v1/routes/chat.py` (`chat_stream` + `_run_agent_turn`) |
| Vendor adapter (OpenAI / Anthropic schema translation) | `backend/app/services/agent/client.py` (`AgentClient`) |
| Front-end rendering of tool results as cards | `console/src/app/chat/tool-renderers/index.tsx` |
| Front-end shimmer "Calling …" status | `console/src/app/chat/single-window-chat.tsx` |

There is **no separate MCP server**. Tools run **in-process** in the
chat-turn request, sharing the request's `AsyncSession` and resolved
`AuthContext` (`workspace_id`, `user_id`, `active_repo_id?`). An MCP
adapter could be bolted on later — RFC-0005/-0009 mention this — but
Phase 6 explicitly chose the in-process path and no work is currently
in flight.

---

## §2 · Tool inventory by category

The numbers in parentheses are `ToolBox.specs()` counts as of
Phase 6 Wave D. Total: **52** tools (31 legacy + 21 added by Phase 6).

### Inbox (8)

| Tool | Mutates | Audit | Description |
|---|---|---|---|
| `inbox_list` | no | no | List items (filters: type, status, owner=me\|all\|unassigned\|user_id, repo, play, limit, cursor). |
| `inbox_counts` | no | no | Aggregate counts by_status / by_type / total. |
| `inbox_get` | no | no | Single item detail + events timeline. |
| `inbox_dispose` | yes | yes | Apply typed disposition (accept/reject/snooze/reassign/comment). Supports `dry_run` preview. |
| `inbox_snooze` | yes | yes | Focused snooze-only alternative to `inbox_dispose`. |
| `inbox_reassign` | yes | yes | Focused reassign-only alternative. Verifies new owner is workspace member. |
| `inbox_routing_list` | no | no | List routing rules + handles. |
| `inbox_routing_preview` | no | no | Side-effect-free dry-run resolver against a synthetic item. |
| `inbox_routing_upsert` | yes | yes | Insert or update an `inbox_routing_rules` row. |

### Plays / Coverage (3)

| Tool | Mutates | Audit | Description |
|---|---|---|---|
| `plays_list` | no | no | Catalog enumeration with `category` / `critical_only` / `q` filters. Richer than legacy `list_catalog_artifacts`. |
| `plays_get` | no | no | Single Play detail (title, category, critical, summary, body, includes, default execution mode, default inbox profile). |
| `plays_coverage` | no | no | Aggregated `play x repo` coverage. Filters: `category`, `critical_only`, `has_gaps`. Sorted critical-desc, gaps-desc. |

### Runs (2)

| Tool | Mutates | Audit | Description |
|---|---|---|---|
| `runs_query` | no | no | Outcome-first list (filters: play, repo, status, trigger, has_escalations, since). Returns `outcome_text`, `findings_by_severity`, escalation count. |
| `run_detail` | no | no | Full run including artifacts, findings, escalations as deeplink-friendly fields. |

### Automations (4)

| Tool | Mutates | Audit | Description |
|---|---|---|---|
| `automations_list` | no | no | Consolidated `pipelines + lanes + fleet_lanes`. Scope filter `all|fleet|repo`. |
| `play_run_now` | yes | yes | Queues a manual `PipelineRun` for `(play_key, repo_id)`. Returns `{error: no_automation}` if the Play isn't yet automated. |
| `play_automate` | yes | yes | Creates `Lane(origin='manual')` (scope=repo) or `FleetLane` (scope=fleet) with the requested cadence. |
| `automation_toggle` | yes | yes | Enables/disables a `Pipeline`. Returns `prior_enabled`. |

### Knowledge (3)

| Tool | Mutates | Audit | Description |
|---|---|---|---|
| `repo_intel_get` | no | no | Current `repo_intel` snapshot (languages, frameworks, structure, commit style, visual tokens). |
| `intel_harvest_trigger` | yes | yes | Dispatches `enqueue_harvest()`. **Rate limit:** 1 per hour per repo per workspace (enforced via own `audit_events`). |
| `knowledge_search_v2` | no | no | Workspace-wide vector search. Adds `bucket_slug` filter and `intel_facts: bool` flag (when true, prepends synthetic intel summary at the top of results). |

### Legacy (31)

The pre-Phase-6 tools (`search_repo_kb`, `get_repo_file`,
`list_code_map`, `create_ticket`, `list_pipelines`,
`list_clarifications`, `list_workspace_members`, …) remain
unchanged. See `tools.py` module docstring for the full list. They
will be retired or merged into the Phase 6 surfaces in a future
phase only if doing so removes value — for now, both surfaces
coexist (e.g. `list_pipelines` still works alongside
`automations_list`).

---

## §3 · Auth & RBAC

### Front door
- `POST /v1/workspaces/{ws}/chat/stream` requires **`ROLES_MEMBER`**
  (`owner` / `admin` / `maintainer` / `member`). Lowered from
  `ROLES_ADMIN` in P6-22 — the agent is now available to every
  member, not just admins.
- `viewer` role is intentionally excluded (no token spend, no
  ability to mutate).

### Per-tool gates
- **Read tools**: no extra gate beyond the chat front door.
- **Mutating tools** (`inbox_dispose`, `inbox_snooze`, `inbox_reassign`,
  `inbox_routing_upsert`, `play_run_now`, `play_automate`,
  `automation_toggle`, `intel_harvest_trigger`): each calls the shared
  helper `_require_admin_or_error(self, *, tool_name=...)` at the top.
  Non-admin → returns `{"error": "forbidden", "message": "navigator
  tool '<name>' requires workspace admin role"}`. **No HTTPException
  is raised**; the chat loop expects dict returns and the front-end
  surfaces the error via `ErrorCard`.

### Audit
- Every successful mutation calls `_audit_navigator_tool(self, *,
  tool_name=..., payload=..., target=...)` which writes
  `AuditLog(event_name=f"navigator.tool.{name}", actor_kind="navigator",
  actor_user_id=...)`.
- `_audit_navigator_tool` calls `flush()` only — the chat-turn
  handler in `chat.py` owns the transaction commit, so a failed
  downstream tool can roll back the audit too.
- Payload redaction: any string field longer than 4 KB is replaced
  with `{"_redacted": True, "len": <orig_len>}`.
- **Rate-limit denial does NOT audit** (e.g. `intel_harvest_trigger`
  returning `{error: rate_limited}` writes nothing).

---

## §4 · How to add a new tool

1. **Decide if it mutates.** Read-only tools are dramatically simpler
   — no admin gate, no audit, no transaction concerns. Prefer them
   when the user can express intent → result without state change
   (use a separate explicit tool for the mutation if needed).

2. **Find the underlying service.** Tools should call
   `app/services/...` helpers or query SQLAlchemy models directly,
   **not** `httpx`-call the public REST API. This keeps the
   tenancy boundary single-source-of-truth (`self._workspace_id`).

3. **Add the spec.** Append a `ToolSpec(name, description,
   parameters)` to `ToolBox.specs()` in **alphabetical order within
   its phase block**. Keep the description **model-friendly** —
   include WHEN to choose this over similar tools.

4. **Add the handler.** Append `name -> self._tool_<name>` to
   `_handlers()`.

5. **Implement the method.** Place at the end of the class, in the
   matching phase section. Async, type-hinted, returns a
   JSON-serializable `dict` (use ISO-8601 strings for datetimes,
   `str(uuid)` for UUIDs, never raise `HTTPException`).

6. **Tenancy.** Every query MUST filter by `self._workspace_id`. If
   the tool takes a `repo_id`, validate it via `_verify_repo_in_workspace`
   before any further work. Cross-workspace lookups return `{error:
   not_found}`.

7. **Mutating extras.** First call `_require_admin_or_error`,
   short-circuit on denial. After the mutation succeeds, call
   `_audit_navigator_tool`. Do NOT call `await self._session.commit()`.

8. **Update the system prompt** (`topic.py`) — add a one-line
   intent → tool mapping in the "New IA tools" section. Without
   this, the model won't discover the tool.

9. **Add tests.** Use the existing `test_navigator_*_tools.py`
   files as templates. At minimum: one happy-path, one error-path
   (forbidden / not_found / validation), one tenancy guard.

10. **(Optional) Add a renderer.** If the tool result is structured
    enough to deserve a card, register a renderer in
    `console/src/app/chat/tool-renderers/index.tsx`. Otherwise it
    falls through to `JsonFallback`, which is acceptable.

---

## §5 · Error-code conventions

Tools return `{"error": "<code>", "message": "<human msg>"}` on
failure. The recognised codes (used by the FE `ErrorCard` to choose a
visual tier):

| Code | When | FE tier |
|---|---|---|
| `forbidden` | Caller lacks required role | amber-yellow ("requires admin") |
| `not_found` | Target doesn't exist or belongs to another workspace | coral (hard error) |
| `validation_failed` | Args malformed or violate schema | coral |
| `rate_limited` | Tool's own rate-limit hit (e.g. intel harvest) | amber-400 (fixable by waiting) |
| `conflict` | Insert would duplicate (e.g. `play_automate` on existing Lane) | amber-400 |
| `precondition_failed` | Logical precondition unmet (e.g. `inbox_dispose` on already-resolved item) | amber-400 |
| `no_automation` | `play_run_now` called for a Play that isn't yet automated for the repo | amber-400 (suggest `play_automate`) |
| `internal` | Unexpected exception caught and rewrapped | coral |

Tools should **never raise** — wrap unexpected exceptions and return
the appropriate code. The chat loop assumes every tool call resolves
to a dict.

---

## §6 · Front-end rendering

| Surface | Where | Notes |
|---|---|---|
| Per-turn shimmer "Calling X…" | `single-window-chat.tsx` | Existing behaviour, unchanged. Driven by `tools` state with no `result` yet. |
| Tool result cards | `single-window-chat.tsx` → `<ToolResultsList>` → `tool-renderers/index.tsx` | Stacks below the message stream, above the shimmer status. Cleared on next `send()`. |
| Per-tool renderer | `tool-renderers/index.tsx`, `TOOL_RENDERERS` registry | 9 renderers as of Phase 6 (see Wave C report). Unknown tools → `JsonFallback`. |
| Errors | `ErrorCard` in `tool-renderers/index.tsx` | Reads `result.error` + `result.message`, picks tier from §5 table. |
| Deeplinks | `Chip` component in `tool-renderers/index.tsx` | Wraps `next/link`. Routes used: `/inbox/<id>`, `/runs/<id>`, `/plays?play=<key>`, `/automations?…`. |

The renderer registry keys are tool names (strings). To add a new
renderer, write a `function Render<Name>(result): React.ReactNode`
and add `name: Render<Name>` to `TOOL_RENDERERS`. No need to touch
`single-window-chat.tsx` — the dispatcher is data-driven.

---

## §7 · Testing

Test files (one per topic, see Phase 6 Wave D):

- `backend/tests/test_navigator_inbox_tools.py`
- `backend/tests/test_navigator_inbox_routing_tools.py`
- `backend/tests/test_navigator_plays_tools.py`
- `backend/tests/test_navigator_runs_tools.py`
- `backend/tests/test_navigator_automations_tools.py`
- `backend/tests/test_navigator_intel_tools.py`
- `backend/tests/test_navigator_tool_chain.py` (multi-tool integration)

Patterns:

- Use `pytest.mark.asyncio` (existing convention) and the
  `async_session` / `workspace` / `user` / `member` fixtures from
  `conftest.py`.
- Use **real DB rows**, not mocked `_session` — tenancy guards only
  fire against real queries.
- Mock external dependencies at the module boundary
  (`enqueue_harvest`, GitHub fetches in routing preview) with
  `monkeypatch`.
- Assert one happy-path, one error-path, and one tenancy guard per
  tool at minimum.
- For mutating tools, also assert the `audit_events` row exists
  (for happy path) and is **absent** for `dry_run=true` /
  rate-limit-denied paths.

---

## §8 · Open follow-ups

The following are deliberate Phase 6 compromises, captured for a
future wave:

1. **`play_run_now` queues, doesn't dispatch.** It inserts a
   `PipelineRun(status='queued')` and lets the worker pick it up.
   Doing the full GitHub Actions dispatch from a chat turn (token
   mint, callback URL, app install lookup) was deemed too risky for
   a chat path. Follow-up: factor the route's dispatch logic into a
   shared service so both the route and the tool can call it.

2. **`play_automate` writes `Lane(origin='manual')` directly.** No
   canonical admin-upsert service exists yet. Follow-up: extract
   `app/services/lanes_admin.py::upsert_lane()` and have the tool
   delegate.

3. **`inbox_routing_upsert` arg surface vs. real schema.** The
   chat-friendly args (`name`, `when`, `then_assign_to`, `priority`)
   don't 1:1 match the table's `(handle_key, target_type,
   target_value, assignment_strategy, strategy_config)`. The mapping
   stores `when` and `priority` under `strategy_config` for
   forward-compat. Follow-up: when the resolver evolves to use
   `when` / `priority`, surface them as first-class columns.

4. **`knowledge_search_v2` `bucket_slug` filter is post-query.** The
   underlying `search_workspace_knowledge` doesn't natively accept
   `bucket_slug`. We filter results in Python after the vector
   search. Follow-up: add native SQL filtering.

5. **`POST /chat/active/new` (open new thread) still admin-gated.**
   P6-22 only opened the streaming route to members; opening a new
   thread also archives the prior one and triggers
   pack-to-knowledge, which is workspace-mutating. Follow-up:
   product call on whether members should be able to start fresh
   threads.

6. **No MCP export.** Phase 6 keeps tools in-process. If/when an
   external agent (Claude Desktop, IDE plugin) needs to call the
   same toolbox, write a thin MCP adapter that translates
   `ToolSpec` → MCP tool definitions and proxies to `ToolBox.invoke`
   inside an authenticated request context.
