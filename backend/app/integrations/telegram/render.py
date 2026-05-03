"""Telegram-flavoured rendering for Navigator markdown.

Telegram's chat surface accepts a small HTML subset (`parse_mode=HTML`) —
``<b>`` / ``<i>`` / ``<u>`` / ``<s>`` / ``<code>`` / ``<pre>`` / ``<a>`` /
``<blockquote>`` and that's it. Headers don't exist; nested lists don't
render as lists; tables render as plain text. The bot still has to deal
with full GFM-flavoured markdown coming out of Navigator (Console can
render it natively), so this module is the lossy bridge.

Three jobs:

1. ``extract_directives`` pulls ``ship-choice`` / ``ship-todo`` fenced
   blocks out of the markdown stream. Those carry interactive intent
   (buttons, plan checklists) that needs to render through Telegram's
   ``InlineKeyboardMarkup`` instead of inline text.
2. ``markdown_to_telegram_html`` collapses headers to bold, lists to
   ``• item`` lines, inline/fenced code to ``<code>`` / ``<pre>``,
   and HTML-escapes everything else. Tolerant of partial markdown so
   it works on streaming buffers (open ``**`` mid-stream stays as a
   literal asterisk until the closing pair arrives).
3. ``split_markdown_into_chunks`` breaks the running buffer at safe
   block boundaries so the rendered HTML for each chunk fits inside
   Telegram's 4096-byte single-message cap. Earlier chunks are
   stable (greedy packing), so once a chunk is committed it doesn't
   re-flow on later deltas — the bot can ``send_message`` once and
   only ``edit_message_text`` the trailing chunk.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass
from typing import Iterable

# Telegram caps a single message at 4096 chars (UTF-16 code units, but
# we approximate with chars). Stay a bit under to leave room for the
# HTML overhead the converter introduces (``<b>...</b>`` etc.).
TELEGRAM_MESSAGE_LIMIT: int = 3800


# ---------------------------------------------------------------------------
# Directive extraction
# ---------------------------------------------------------------------------


_DIRECTIVE_NAMES = ("ship-choice", "ship-todo")
_DIRECTIVE_FENCE_RE = re.compile(
    r"^```(" + "|".join(_DIRECTIVE_NAMES) + r")\s*\n(.*?)```",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class Directive:
    """One ship-choice / ship-todo card extracted from the stream."""

    kind: str  # "ship-choice" | "ship-todo"
    payload: dict | None  # parsed JSON; None if the JSON didn't parse yet
    raw: str  # original fence body (used during streaming for placeholders)


def extract_directives(md: str) -> tuple[str, list[Directive]]:
    """Strip directive fences from ``md`` and return them separately.

    Tolerates partial JSON inside a fence — partial fences without a
    closing triple-backtick (still streaming) are left in the markdown
    so they render as a normal code block until the closing arrives.
    """
    directives: list[Directive] = []

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        body = match.group(2).strip()
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = None
        directives.append(Directive(kind=kind, payload=payload, raw=body))
        # Replace with a sentinel paragraph so the surrounding text
        # keeps its block structure and nothing gets glued together.
        return ""

    cleaned = _DIRECTIVE_FENCE_RE.sub(_replace, md)
    # Collapse runs of blank lines that the strip leaves behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, directives


# ---------------------------------------------------------------------------
# Markdown → Telegram HTML
# ---------------------------------------------------------------------------


# Stash code blocks before any other processing so their contents survive
# untouched (no inline-bold parsing inside ``foo **bar** baz``).
_FENCE_RE = re.compile(r"```(?:[\w+-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+?)\]\(([^)\s]+)\)")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_HR_RE = re.compile(r"^[\s]*(?:-{3,}|_{3,}|\*{3,})[\s]*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)[-*+]\s+(.+?)$", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^([ \t]*)(\d+)\.\s+(.+?)$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"((?:^>\s?.*\n?)+)", re.MULTILINE)


def markdown_to_telegram_html(md: str) -> str:
    """Convert Navigator-flavoured markdown into Telegram-safe HTML.

    Tolerant of partial markdown — open ``**`` with no closing pair is
    left as a literal asterisk. Streams therefore render incrementally
    without ever producing invalid HTML mid-edit.
    """
    if not md:
        return ""

    # 1. Stash code blocks (fenced + inline) so further regex passes
    #    don't mangle their contents.
    placeholders: list[str] = []

    def _stash_pre(match: re.Match[str]) -> str:
        body = match.group(1)
        placeholders.append(f"<pre>{_html.escape(body)}</pre>")
        return f"\x00P{len(placeholders) - 1}\x00"

    def _stash_code(match: re.Match[str]) -> str:
        body = match.group(1)
        placeholders.append(f"<code>{_html.escape(body)}</code>")
        return f"\x00P{len(placeholders) - 1}\x00"

    text = _FENCE_RE.sub(_stash_pre, md)
    text = _INLINE_CODE_RE.sub(_stash_code, text)

    # 2. Headers → bold (Telegram has no headers). One blank line
    #    after to keep visual separation.
    text = _HEADER_RE.sub(lambda m: f"<b>{m.group(2)}</b>", text)

    # 3. Horizontal rule → an em-dash separator.
    text = _HR_RE.sub("———", text)

    # 4. Bullet / numbered lists → "• " prefix lines (no nested-list
    #    rendering — Telegram doesn't support them visually).
    text = _BULLET_RE.sub(lambda m: f"{m.group(1)}• {m.group(2)}", text)
    text = _NUMBERED_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}. {m.group(3)}", text
    )

    # 5. Blockquotes → ``<blockquote>`` (group consecutive ``> `` lines).
    def _quote(match: re.Match[str]) -> str:
        body = match.group(1)
        stripped = "\n".join(
            re.sub(r"^>\s?", "", line) for line in body.splitlines()
        )
        return f"<blockquote>{stripped}</blockquote>\n"

    text = _BLOCKQUOTE_RE.sub(_quote, text)

    # 6. Inline emphasis. Bold first (``**...**``) then italic
    #    (``*...*``) so the italic regex doesn't accidentally chew on
    #    half of a bold pair.
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)

    # 7. Links → ``<a href>``. The URL is inserted raw — step 8's
    #    bulk ``html.escape`` will rewrite any ``&`` to ``&amp;`` (and
    #    ``<``/``>`` to entities) exactly once, then the unescape pass
    #    in step 9 walks the captured href back into a proper anchor.
    #    Pre-escaping here would double up on ``&`` and emit
    #    ``href="...&amp;amp;..."``.
    text = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )

    # 8. HTML-escape everything that's still plain text. The chunks
    #    we already produced contain ``<`` / ``>`` we want to keep,
    #    so escape only OUTSIDE of those — easier path: escape, then
    #    un-escape the tags we know we emitted.
    text = _html.escape(text, quote=False)
    text = _UNESCAPE_TAGS_RE.sub(_unescape_tag, text)

    # 9. Restore code-block placeholders.
    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return placeholders[idx]

    text = re.sub(r"\x00P(\d+)\x00", _restore, text)
    return text


# After step 8 the regex below pulls our intentional emitted tags back
# out of ``&lt;b&gt;`` form. Anything that *wasn't* emitted by us stays
# escaped — so a stray ``<script>`` from the model becomes literal text.
# ``_html.escape`` was called with ``quote=False`` so ``"`` survives
# untouched in the escaped buffer; the anchor pattern matches that
# directly instead of the ``&quot;`` form.
_KNOWN_TAGS = ("b", "i", "u", "s", "code", "pre", "blockquote", "br")
_UNESCAPE_TAGS_RE = re.compile(
    r'&lt;(/?)('
    + "|".join(_KNOWN_TAGS)
    + r')&gt;'
    r'|&lt;a href="([^"]*)"&gt;'
    r'|&lt;/a&gt;'
)


def _unescape_tag(match: re.Match[str]) -> str:
    closing, name, href = match.group(1), match.group(2), match.group(3)
    if href is not None:
        return f'<a href="{href}">'
    if name is None:
        # Closing </a> — the only branch left after the others fail.
        return "</a>"
    return f"<{closing}{name}>"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def split_markdown_into_chunks(
    md: str, *, max_chars: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """Greedy split of ``md`` so each chunk's rendered HTML fits Telegram's cap.

    The split happens on the markdown side (not the HTML side) at the
    most semantically safe boundary available — paragraph break, then
    single newline, then space. Chunks are stable: appending more
    markdown after this call leaves earlier chunks bit-identical, so
    the bot can freeze them with a single ``send_message`` and only
    edit the trailing chunk on subsequent deltas.

    The size budget is the *rendered HTML* length so the converter's
    overhead (e.g. ``<b>...</b>``) is accounted for. We approximate by
    rendering each candidate chunk and shrinking until it fits.
    """
    if not md.strip():
        return []
    chunks: list[str] = []
    remaining = md
    while remaining:
        rendered = markdown_to_telegram_html(remaining)
        if len(rendered) <= max_chars:
            chunks.append(remaining)
            break
        # Binary-ish search for the largest cut whose rendered HTML
        # fits. Cheap because most cuts hit on the first try at a
        # paragraph break near max_chars.
        cut = _find_safe_cut(remaining, max_chars=max_chars)
        if cut <= 0:
            # Pathological: a single token wider than the budget.
            # Hard-cut at max_chars on the markdown side and accept
            # that the rendered HTML may exceed the cap by a few
            # bytes (Telegram will still accept it most of the time).
            cut = max_chars
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n ")
    return chunks


def _find_safe_cut(md: str, *, max_chars: int) -> int:
    """Return an index in ``md`` such that ``md[:idx]`` renders ≤ max_chars.

    Cuts that would land inside an unclosed ``` fenced block are
    rejected — splitting a code block produces broken syntax in both
    halves (the prefix renders as a fence with no content, the suffix
    as bare lines that lost their fence header). The fence-state check
    is a parity count of ``` markers in the prefix.
    """

    def _outside_fence(idx: int) -> bool:
        return md.count("```", 0, idx) % 2 == 0

    floor = int(max_chars * 0.5)
    # Try paragraph breaks first, then any newline, then space. For
    # each separator, walk backwards skipping splits inside fences.
    for sep in ("\n\n", "\n", " "):
        end = max_chars
        while end > floor:
            idx = md.rfind(sep, 0, end)
            if idx <= floor:
                break
            if _outside_fence(idx) and len(
                markdown_to_telegram_html(md[:idx])
            ) <= max_chars:
                return idx
            end = idx
    # No clean separator — take the largest prefix ending outside a
    # fence whose rendering fits. Walk backwards in 200-char steps.
    for end in range(max_chars, 0, -200):
        if _outside_fence(end) and len(
            markdown_to_telegram_html(md[:end])
        ) <= max_chars:
            return end
    return 0


# ---------------------------------------------------------------------------
# Choice / todo presentation helpers (text fallback when buttons aren't
# rendered yet — Stage 1 leaves the choice text inline so the user at
# least sees the options; Stage 2 will replace this with InlineKeyboard).
# ---------------------------------------------------------------------------


def directive_fallback_text(directive: Directive) -> str:
    """Render a directive as plain markdown when interactive UI isn't wired."""
    if directive.kind == "ship-choice":
        if not directive.payload:
            return "_(подгружаю варианты…)_"
        prompt = str(directive.payload.get("prompt") or "").strip()
        options = directive.payload.get("options") or []
        labels: list[str] = []
        for opt in options:
            if isinstance(opt, str):
                labels.append(opt)
            elif isinstance(opt, dict) and isinstance(opt.get("label"), str):
                labels.append(opt["label"])
        body_lines: list[str] = []
        if prompt:
            body_lines.append(f"**{prompt}**")
        for label in labels:
            body_lines.append(f"• {label}")
        return "\n".join(body_lines) if body_lines else "_(нет вариантов)_"
    if directive.kind == "ship-todo":
        if not directive.payload:
            return "_(готовлю план…)_"
        title = str(directive.payload.get("title") or "").strip()
        items = directive.payload.get("items") or []
        body_lines: list[str] = []
        if title:
            body_lines.append(f"**{title}**")
        for item in items:
            if isinstance(item, str):
                body_lines.append(f"⬜ {item}")
                continue
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            status = str(item.get("status") or "pending").strip()
            icon = {"done": "✅", "in_progress": "⏳", "pending": "⬜"}.get(
                status, "⬜"
            )
            body_lines.append(f"{icon} {text}")
        return "\n".join(body_lines) if body_lines else "_(пустой план)_"
    return ""


def render_with_directives_inline(md: str) -> tuple[str, list[Directive]]:
    """Strip directives from ``md`` and append their fallback rendering.

    Returns a tuple ``(rendered_markdown, directives)``. The rendered
    markdown can be passed straight to :func:`markdown_to_telegram_html`;
    callers that wire interactive UI (Stage 2 inline keyboards) can
    inspect ``directives`` to attach a ``reply_markup`` instead.
    """
    cleaned, directives = extract_directives(md)
    if directives:
        suffix_lines: list[str] = []
        for d in directives:
            text = directive_fallback_text(d)
            if text:
                suffix_lines.append(text)
        if suffix_lines:
            cleaned = (cleaned + "\n\n" + "\n\n".join(suffix_lines)).strip()
    return cleaned, directives


__all__ = [
    "TELEGRAM_MESSAGE_LIMIT",
    "Directive",
    "extract_directives",
    "markdown_to_telegram_html",
    "split_markdown_into_chunks",
    "directive_fallback_text",
    "render_with_directives_inline",
]
