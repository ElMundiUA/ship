---
title: From chat to Navigator
slug: from-chat-to-navigator
date: 2026-04-20
kicker: Case study
description: A chat window is a failure mode dressed as a product surface. Over two days the Ship chat became a Navigator — fewer bubbles, word-by-word reveal, typed widgets, and a turn that no longer jumps the viewport. A case study in treating a surface as part of the agent.
reading_time: 9
author: Denys Kuzin
tags: [product, agent, case-study, build-in-public]
---

On Apr 20 morning we shipped a real agent behind a chat window — SSE streaming, seven tools, pgvector memory. It was good and it was wrong.

Over the next two days we rewrote the surface six times, renamed it from Chat to Navigator, and landed on something that reads as a different product even though the backend is the same AgentClient we had on Monday morning. This is the story of those two days, told off the commits.

## A chat window is not a neutral surface

Calling a surface "chat" is a commitment. Three of them, actually. You promise instant replies. You promise idle small talk will be tolerated. You promise a linear scroll of history the user can page through like iMessage. The word does all of that work before the first message is typed.

Our agent is none of those things. It is a delivery worker. You give it a task, it goes and reads files in your repo, files a ticket in Linear, writes a note to a knowledge bucket, and comes back with a paragraph. The median turn involves two or three tool calls and takes eight to twenty seconds. There is no small talk. A scrolling history is not the point; the current turn is.

The mismatch showed up the first time we watched someone new use it. Their first reaction was "why is it so slow," which was a fair question if you thought you were using a messaging app and an absurd one if you thought you were using a search-plus-write tool. We had trained them to expect the wrong thing with the label in the nav.

`chore(console): rename Chat nav and shell title to Navigator` (`00a46aa`) on Apr 20 night was the smallest possible commit — three or four string changes — and it unlocked every later design decision. We stopped asking "how should the chat behave" and started asking "what does the navigator do." Different question, different answer.

> The word in the nav is the first commitment the product makes. We were keeping the wrong one.

## C12: the good bones

Be fair to the thing we were about to rewrite six times.

`C12: real agent — single-window SSE chat, named buckets, RAG tools` (`c98f251`) landed on Apr 20 morning. What it was:

- A vendor-agnostic `AgentClient` — OpenAI as the default, Anthropic as an alternate — streaming tool-use through a unified `AgentEvent` protocol. The client did not know which vendor it was talking to and did not need to.
- A pgvector-backed memory: `.ship/knowledge` RAG chunks plus named knowledge buckets with per-summary embeddings. A `TopicService` that classified topic shifts, retrieved relevant buckets, and packed the thread for the next call.
- A ToolBox with seven tools — `search_repo_kb`, `get_repo_file`, `list_code_map`, `create_ticket` against Linear, Notion, or GitHub Issues, `create_artifact_feedback`, `list_recent_activity`, `search_buckets`.
- Cost guards. A per-turn token budget and an eight-iteration cap on the tool loop, so a misbehaving model could not spend the credit card. Sentry `ai.chat` spans carrying model and token usage.
- Migration 0010 to take the schema the rest of the way: `knowledge_buckets`, `bucket_summaries`, `kb_chunks`, `artifact_feedback`, plus the `chat_threads` extensions the topic service needed.

The backend was solid and boring and correct. Two hundred and eighty-three tests green. We are happy with it today; we ship new tools into that same `AgentClient` every few days. Everything that came after C12 was about whether the substrate was visible in the right way.

Because it was not. The surface on top of this agent, the hour C12 shipped, was the stub-reply chat window we had built two weeks earlier to test the SSE plumbing. Bordered bubbles. A fixed scroll panel. A tool-call strip in a box beside the messages. The backend could do real things; the surface made those things look like messages from a friend who happened to know your repo. We had built a delivery worker and hidden it inside an iMessage clone.

## Flat chat and a dishonest typewriter

The first visible iteration landed the same day. `feat(console/chat): flatten chat surface + typewriter streaming` (`36953d7`) did two things.

It dropped the bubbles, the bordered scroll panel, and the tool-call strip, replacing them with a plain top-to-bottom transcript and thin role labels. And it introduced a character-by-character typewriter: deltas landed in a buffer, a render head walked through the buffer at an adaptive speed — faster when the buffer ran ahead, slower near the tail for a "natural typing feel" — and the user watched the reply type itself out.

It felt alive. It finally looked like a tool instead of a conversation. It was also dishonest.

The model does not actually type. Tokens arrive at batch rates — twenty, forty, sometimes a hundred at a time — and the typewriter was slowing them down to look pensive. A user watching the character march was watching us pretend the machine was thinking harder than it was. We had added latency on the user's side to fake latency on the model's side.

> A typewriter on a batched stream is a fake. It looks thoughtful; it is theatre.

There is a case for character-by-character on a stream that actually emits one token at a time. Ours did not. We were buffering deltas and rationing them out.

The typewriter had a mechanical problem too. Markdown was applied only at the end, when the final `assistant_message` event landed; until then the user was reading plaintext. At the end of every turn the paragraph flipped from plain to formatted — headings appeared, bold took effect, code blocks acquired their gutter — as if the reply were re-calligraphing itself one second before you could look away. Two different reading surfaces in one turn. You can get used to that; you should not have to.

## Word fade and widgets

The next commit threw the typewriter out. `feat(console,backend): navigator UX polish — word fade, cards, ship-choice/ship-todo widgets` (`7add8da`) replaced character-by-character with word-by-word.

The mechanic is small. A `findNextWordBoundary` function walks the accumulated delta buffer; a per-message reveal map tracks how many characters have been committed to the visible string so far; a short interval commits whole words. Each newly-committed word gets a `.chat-word` class whose CSS keyframe fades it in over a hundred-odd milliseconds. The effect is a paragraph that appears word by word, each word already in its final typographic position.

Two constraints came with it.

Markdown applies from the first delta. Because the word-reveal layer sits on top of `react-markdown`, the paragraph is already a markdown tree by the time words appear in it. No more plain-to-formatted flip at the end. A heading is a heading from the first word.

Only messages born in this session animate. An `animatedIdsRef` is seeded on the first delta of a new message; messages loaded from the server on page reload are not in the set and render statically. You stop replaying the last reply's fade every time you refresh the tab — the sort of thing that sounds trivial until you sit next to someone who watches it happen twice. The React key is also kept stable across the `assistant_message` finalize, so the client id does not rotate mid-reveal and trigger a re-mount.

And then the widgets.

Two typed widgets got added to the markdown renderer the agent already produced: `ship-choice` and `ship-todo`. A `ship-choice` is a clickable multiple-choice card; the label of the clicked option is sent back as the next user message, so the user can pick "open a Linear ticket" without typing those five words. A `ship-todo` is a task-list card with `done`, `in_progress`, and `pending` statuses — the right surface when the agent needs to tell you about five parallel pieces of work instead of writing the same information as prose.

The system prompt was updated the same day: the agent is told when to prefer each widget over plain text. A choice between four or fewer next steps should be a `ship-choice`, not a bulleted list ending in "let me know which." A list of concurrent tasks with statuses should be a `ship-todo`, not a paragraph.

The thinking bubble became a `ThinkingCard` — grey gradient text animating through itself — and tool calls became styled `ToolCallCard`s with shimmer, pulse, ok, and error states, plus a short summary of the arguments instead of the raw JSON dump we had been showing.

That was the version we almost shipped. It was closer. It was still not right.

## Four fixes in a row that were actually the design

The next four commits arrived across Apr 21 looking like bug fixes. They were filed as bug fixes. The subjects all start with `fix(console)`. If you scanned the log you would call them polish.

Each of them was a design decision that only showed up once the rest of the surface was still enough to expose it. Taken in order they are the shape of the Navigator.

**`2108833` — stop the jitter, actually pin the user message to the top.** The word-fade version had a bug you could not see on a short reply and could not ignore on a long one: every delta was replaying the fade on every already-visible word. The whole paragraph flickered faster than you could track.

The cause was exactly the kind of thing that makes React performance bugs demoralising. `ChatMarkdown` was handing `react-markdown` a freshly-allocated `components` object on every render, and a freshly-allocated `remarkPlugins` array besides. `react-markdown` treats each component entry as a distinct element type; when the reference changes, the reconciler tears down the subtree and re-mounts it. Re-mounting means re-running the CSS keyframe, on every already-rendered word, for every delta. The fix was to move the component map and the plugin list to module-level constants — define the object once, at import time, and hand the same reference every render. A handful of lines, and the fade only ran on genuinely new word spans.

The same commit fixed something we had not understood was a design decision until it was wrong. On submit, the scroller had no runway below the fresh user message; the reply had not arrived yet, so the content height did not exceed the viewport, so `scrollTo` silently no-op'd and the message stayed at the bottom of the viewport. As the reply streamed in below it, the prompt got pushed further up the screen, drifting until it was off the top. The fix was to reserve a bottom spacer equal to the scroller's visible height while a turn is in flight, then smooth-scroll the user message to the top using `getBoundingClientRect` math — independent of the nearest positioned ancestor, which had been silently eating earlier attempts. The spacer collapses to zero on `end`, `error`, or new-conversation.

That pin-to-top is not a scroll behaviour. It is a frame. The user's prompt is the top of the page for the duration of the turn; the reply renders beneath it, never above it; the viewport does not jump. You can read the turn as a single document from the moment you hit enter.

**`fb3658d` — flatten thinking and tool activity, keep the trail.** The `ThinkingCard` went away. A bordered card for "waiting" is exactly the kind of visual weight that makes you feel the waiting. In its place: a single grey shimmer line, inline between the user prompt and the incoming reply.

Tool calls flattened too. Out went the styled `ToolCallCard`s; in came single-row entries — a status dot, the tool name, a short summary of the arguments, separated by a middle dot. Running rows shimmer. Done rows go muted. Errors go rose with the message inline. A tool-call list in the middle of a turn is a log, not a gallery; it should look like a log.

And the tool trail stopped clearing on `end` or `error`. Under the old behaviour, you would watch `search_repo_kb` run, see the result arrive, see `get_repo_file` run, see the result arrive, and then, at the moment the reply started streaming — the whole trail vanished. Which was wrong. The trail is part of the turn; it tells the user what the reply is based on. It should stay visible until the user starts a new turn. Now it does. It only gets wiped when the next message is sent, or a new conversation is started.

Activity moved too. It used to render below the streaming reply; it now renders above it, between the user prompt and the reply. Which is chronologically correct — the tools were called before the reply was written. The old order, reply then tools under it, was geometrically tidy and narratively backwards.

**`e28fab3` — drop the Thinking placeholder entirely.** The grey shimmer line from the previous commit lasted one day. The "Thinking…" string was still empty theatre even as a single line: between submit and the first delta, the line appeared for a couple hundred milliseconds and was then replaced by a tool row or by the first word of the reply. Either way, it was a flicker. We removed it. Now, between submit and the first delta, we render the tool trail if any tool was called, or nothing at all. The bottom spacer still holds the prompt near the top of the viewport, so the page still feels like something is happening. It just does not tell you so in words.

**`63464c3` — one self-replacing status line instead of a stack.** The last move was the one that made the turn feel like a turn. Instead of stacking rows — thinking, tool one, tool two, reply — a single line between the user prompt and the incoming reply rewrites itself as the turn progresses. Thinking, then `Calling search_repo_kb…`, then Thinking, then `Calling create_ticket…`, then the line disappears when the reply starts streaming.

One log, one position. If a tool errors, the line flashes a rose variant until the next tool supersedes it or the reply arrives. Once `end` fires it disappears entirely; there is no lingering status text under a finished reply. The commit also deleted `ToolCallTrail`, `ToolCallRow`, `StatusDot`, `toolArgSummary`, and `truncate` — the helpers we had built for the stacked version, now dead code.

Four commits, each with `fix` in the subject line, each of them a design that only showed up once the rest of the surface was still enough to expose it.

{{chart: blog-navigator-turn | One turn of the agent, before and after. The left column is C12 — bordered bubbles, a thinking card, a stack of tool-call cards that appear and vanish, the viewport jumping four or five times as content re-mounts. The right column is Navigator — the user prompt pinned at the top, one self-replacing status line under it, the reply streaming in below. Same agent, same tools, two different products. }}

## What these six commits bought

Read the turn, top to bottom, in the shipped Navigator.

You type a prompt and hit enter. The prompt smooth-scrolls to the top of the viewport and stays there for the rest of the turn. A bottom spacer holds the runway open so nothing jumps. Under the prompt, a single grey line appears: `Calling search_repo_kb…`. It rewrites itself a second later: `Calling get_repo_file…`. It disappears. A paragraph begins below it, markdown already applied, words fading in one at a time. The tool trail is still visible above the reply as a compact log — two flat rows showing what the agent searched and fetched. The viewport has not jumped. Nothing has re-mounted. The prompt you typed is still at the top of the screen.

That sequence is the product. It is the same `AgentClient` from Monday morning — the same seven tools, the same pgvector memory, the same SSE protocol. What changed in two days is the frame around it, and the frame is what the user calls the agent.

> The UI is part of the agent. You do not get to build a good agent and a bad surface around it; the surface becomes what the user believes the agent is.

The renames matter too. We stopped calling it chat. Chat was a commitment to being a messaging app; Navigator is a commitment to being a surface you read. The two widgets — `ship-choice` and `ship-todo` — are the same commitment in a different register: the agent is not trying to sustain a conversation, it is producing artifacts in typed shapes the surface knows how to render. When the right answer is "pick one of these four next steps," the right output is a card with four buttons, not a paragraph ending in "let me know."

The commit log for these two days reads like polish: a rename, some UI work, four bug fixes. In aggregate it is a rewrite of the surface, done without touching the substrate. Each of those commits individually looks cheap. Taken together they are the cost of getting the frame right, and the frame is what a first-time user will remember a week later.

We are going to keep the Navigator label. We are going to keep asking, on any new surface, what the word in the nav is promising. If it is promising the wrong thing, the word comes out first and the design decisions follow.

Fewer bubbles. Flatter rows. No jumps.
