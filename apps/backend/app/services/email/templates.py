"""Render the small handful of transactional emails Ship sends today.

Two messages live here:

- :func:`render_invite_email` — "you've been invited to <workspace>"
  with a one-shot accept URL. Used by ``POST /v1/workspaces/{ws}/invites``
  and the resend endpoint.
- :func:`render_navigator_summary_email` — the navigator's
  ``send_email_to_self`` tool calls this to email the signed-in user
  a Markdown summary of the current chat (rendered to HTML).

Templates intentionally live as Python strings (Jinja2 ``Template``
instances) rather than separate files: the visual surface is tiny,
keeping them next to the render helpers makes the contract obvious,
and there is nothing to ship in the runtime image but ``.py`` files.
If/when we add many more, split into a real ``templates/`` folder
with a ``FileSystemLoader``.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime

from jinja2 import Environment, StrictUndefined, select_autoescape


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Subject + dual-body pair, ready to hand to :class:`EmailMessage`."""

    subject: str
    html: str
    text: str


_env = Environment(
    autoescape=select_autoescape(enabled_extensions=("html",)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------------------------------------------------------------------------
# Invite — "you've been invited to {workspace} on Ship"
# ---------------------------------------------------------------------------


_INVITE_HTML = _env.from_string(
    """\
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#0b0d12;color:#eef1f5;font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0d12;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#11141b;border:1px solid #1d2230;border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:28px 32px 8px 32px;">
                <div style="font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#7e8aa3;">Ship · invitation</div>
                <h1 style="margin:8px 0 0 0;font-size:22px;line-height:1.3;color:#ffffff;">
                  You're invited to join {{ workspace_name }}
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 32px 0 32px;font-size:14px;line-height:1.55;color:#cbd2df;">
                {% if inviter_email %}
                <p style="margin:12px 0;">
                  <strong style="color:#ffffff;">{{ inviter_email }}</strong>
                  invited you to join <strong style="color:#ffffff;">{{ workspace_name }}</strong>
                  on Ship as <strong style="color:#7be4c1;">{{ role }}</strong>.
                </p>
                {% else %}
                <p style="margin:12px 0;">
                  You've been invited to join
                  <strong style="color:#ffffff;">{{ workspace_name }}</strong>
                  on Ship as <strong style="color:#7be4c1;">{{ role }}</strong>.
                </p>
                {% endif %}
                <p style="margin:12px 0;color:#9aa3b8;">
                  This invite is for <strong style="color:#cbd2df;">{{ recipient_email }}</strong>
                  and expires on {{ expires_at_human }}.
                </p>
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:24px 32px 8px 32px;">
                <a href="{{ accept_url }}"
                   style="display:inline-block;padding:12px 24px;background:#7be4c1;color:#0b0d12;text-decoration:none;font-weight:700;border-radius:999px;font-size:14px;">
                  Accept invitation
                </a>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 32px 24px 32px;font-size:12px;color:#7e8aa3;line-height:1.6;">
                <p style="margin:12px 0;">
                  If the button does not work, copy this URL into your browser:
                </p>
                <p style="margin:0;word-break:break-all;font-family:'SF Mono',Menlo,Consolas,monospace;color:#9aa3b8;">
                  {{ accept_url }}
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 28px 32px;font-size:11px;color:#566179;border-top:1px solid #1d2230;">
                You're receiving this because someone with admin access added
                you to a Ship workspace. If you weren't expecting this you
                can ignore the message — the invite expires automatically.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
)


_INVITE_TEXT = _env.from_string(
    """\
You're invited to join {{ workspace_name }} on Ship.

{% if inviter_email %}{{ inviter_email }} invited you{% else %}You've been invited{% endif %} as {{ role }}.
This invite is for {{ recipient_email }} and expires on {{ expires_at_human }}.

Accept the invitation:
{{ accept_url }}

If you weren't expecting this, you can ignore the message — the invite
expires automatically.

— Ship
"""
)


def render_invite_email(
    *,
    workspace_name: str,
    role: str,
    recipient_email: str,
    accept_url: str,
    expires_at: datetime,
    inviter_email: str | None,
) -> RenderedEmail:
    """Render the bulk-invite message for one recipient."""
    expires_at_human = expires_at.strftime("%B %d, %Y at %H:%M UTC")
    ctx = {
        "workspace_name": workspace_name,
        "role": role,
        "recipient_email": recipient_email,
        "accept_url": accept_url,
        "expires_at_human": expires_at_human,
        "inviter_email": inviter_email,
    }
    subject = f"You're invited to join {workspace_name} on Ship"
    return RenderedEmail(
        subject=subject,
        html=_INVITE_HTML.render(**ctx),
        text=_INVITE_TEXT.render(**ctx),
    )


# ---------------------------------------------------------------------------
# Navigator summary — "I'm emailing you the conversation"
# ---------------------------------------------------------------------------


_NAVIGATOR_HTML = _env.from_string(
    """\
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#0b0d12;color:#eef1f5;font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0d12;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#11141b;border:1px solid #1d2230;border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:24px 32px 4px 32px;">
                <div style="font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#7e8aa3;">Ship · navigator</div>
                <h1 style="margin:8px 0 0 0;font-size:20px;line-height:1.35;color:#ffffff;">
                  {{ subject }}
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 8px 32px;font-size:14px;line-height:1.6;color:#cbd2df;">
                {{ body_html|safe }}
              </td>
            </tr>
            {% if conversation_url %}
            <tr>
              <td style="padding:8px 32px 24px 32px;font-size:12px;color:#9aa3b8;border-top:1px solid #1d2230;">
                <p style="margin:16px 0 8px 0;">Continue this conversation in Ship:</p>
                <p style="margin:0;">
                  <a href="{{ conversation_url }}"
                     style="color:#7be4c1;text-decoration:none;font-weight:600;">
                    Open the navigator →
                  </a>
                </p>
              </td>
            </tr>
            {% endif %}
            <tr>
              <td style="padding:14px 32px 24px 32px;font-size:11px;color:#566179;border-top:1px solid #1d2230;">
                You requested this email through the Ship navigator chat.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
)


_NAVIGATOR_TEXT = _env.from_string(
    """\
{{ subject }}

{{ body_text }}
{% if conversation_url %}
---
Continue this conversation in Ship:
{{ conversation_url }}
{% endif %}

— Ship navigator
"""
)


def render_navigator_summary_email(
    *,
    subject: str,
    body_markdown: str,
    conversation_url: str | None,
) -> RenderedEmail:
    """Render the navigator-initiated "email me a summary" message."""
    body_html = _markdown_to_safe_html(body_markdown)
    ctx = {
        "subject": subject,
        "body_html": body_html,
        "body_text": body_markdown.strip(),
        "conversation_url": conversation_url,
    }
    return RenderedEmail(
        subject=subject,
        html=_NAVIGATOR_HTML.render(**ctx),
        text=_NAVIGATOR_TEXT.render(**ctx),
    )


# ---------------------------------------------------------------------------
# Tiny Markdown subset — keeps the dependency graph small.
# ---------------------------------------------------------------------------


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CODE_FENCE_RE = re.compile(r"^```(\w*)$")


def _markdown_to_safe_html(text: str) -> str:
    r"""Render a small, hand-picked Markdown subset to safe HTML.

    We deliberately avoid pulling a full Markdown engine into the
    backend just for transactional email — the LLM emits well-formed
    paragraphs, lists, fenced code blocks, and inline emphasis, so a
    targeted parser keeps the dependency graph small and the output
    predictable. Anything not matched falls through as escaped text
    inside ``<p>`` tags.

    Supported subset (per line, top-down):

    - ``# … ###### …`` headings
    - ``- `` / ``* `` unordered lists
    - ``1. `` / ``2. `` ordered lists
    - ``> …`` blockquote
    - ``\`\`\`lang`` fenced code blocks
    - inline ``**bold**``, ``*italic*``, ```` `code` ````, and
      ``[label](https://…)`` links.
    """
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    in_ul = False
    in_ol = False
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_buf:
            return
        joined = " ".join(p.strip() for p in paragraph_buf if p.strip())
        if joined:
            out.append(f"<p>{_inline(joined)}</p>")
        paragraph_buf.clear()

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in lines:
        if in_code:
            if raw.strip() == "```":
                out.append("</code></pre>")
                in_code = False
            else:
                out.append(html_lib.escape(raw))
                out.append("\n")
            continue

        fence = _CODE_FENCE_RE.match(raw.strip())
        if fence:
            flush_paragraph()
            close_lists()
            lang = fence.group(1) or ""
            cls = f' class="language-{html_lib.escape(lang)}"' if lang else ""
            out.append(
                "<pre style=\"background:#0b0d12;color:#cbd2df;border-radius:8px;"
                "padding:12px;overflow:auto;font-size:12px;font-family:'SF Mono',Menlo,monospace;\">"
                f"<code{cls}>"
            )
            in_code = True
            continue

        stripped = raw.strip()
        if not stripped:
            flush_paragraph()
            close_lists()
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = len(heading.group(1))
            text_in = heading.group(2)
            out.append(
                f'<h{level} style="margin:18px 0 8px 0;color:#ffffff;font-size:{20 - level * 2}px;">'
                f"{_inline(text_in)}</h{level}>"
            )
            continue

        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            if not in_ul:
                close_lists()
                out.append('<ul style="margin:8px 0 8px 20px;padding:0;">')
                in_ul = True
            out.append(f"<li>{_inline(stripped[2:].strip())}</li>")
            continue

        ol_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if ol_match:
            flush_paragraph()
            if not in_ol:
                close_lists()
                out.append('<ol style="margin:8px 0 8px 20px;padding:0;">')
                in_ol = True
            out.append(f"<li>{_inline(ol_match.group(2).strip())}</li>")
            continue

        if stripped.startswith("> "):
            flush_paragraph()
            close_lists()
            out.append(
                '<blockquote style="margin:8px 0;padding:6px 12px;'
                'border-left:3px solid #1d2230;color:#9aa3b8;">'
                f"{_inline(stripped[2:].strip())}</blockquote>"
            )
            continue

        paragraph_buf.append(stripped)

    flush_paragraph()
    close_lists()
    if in_code:
        out.append("</code></pre>")

    return "".join(out)


def _inline(text: str) -> str:
    """Apply inline markdown after escaping."""
    escaped = html_lib.escape(text)

    def link_sub(match: re.Match[str]) -> str:
        label = match.group(1)
        # ``href`` was already passed through ``html.escape`` together
        # with the rest of ``text`` before the regex ran, so quote
        # characters can't break out of the attribute. The leading
        # ``https?://`` filter on the source regex blocks
        # ``javascript:`` / ``data:`` schemes.
        href = match.group(2)
        return (
            f'<a href="{href}" style="color:#7be4c1;text-decoration:underline;">'
            f"{label}</a>"
        )

    escaped = _LINK_RE.sub(link_sub, escaped)
    escaped = _INLINE_CODE_RE.sub(
        lambda m: (
            "<code style=\"background:#0b0d12;color:#cbd2df;padding:1px 4px;"
            "border-radius:4px;font-family:'SF Mono',Menlo,monospace;font-size:0.92em;\">"
            f"{m.group(1)}</code>"
        ),
        escaped,
    )
    escaped = _BOLD_RE.sub(
        lambda m: f"<strong>{m.group(1)}</strong>", escaped
    )
    escaped = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    return escaped


# ---------------------------------------------------------------------------
# Engine notification (ELS-222 — notify() EmailChannel)
# ---------------------------------------------------------------------------

_NOTIFICATION_HTML = _env.from_string(
    """\
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#0b0d12;color:#eef1f5;font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0d12;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#11141b;border:1px solid #1d2230;border-radius:16px;overflow:hidden;">
            <tr>
              <td style="padding:24px 32px 4px 32px;">
                <div style="font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#7e8aa3;">Ship · {{ level }}</div>
                <h1 style="margin:8px 0 0 0;font-size:20px;line-height:1.35;color:#ffffff;">
                  {{ subject }}
                </h1>
                {% if ticket_ref %}
                <div style="margin-top:6px;font-size:12px;color:#9aa3b8;">{{ ticket_ref }}</div>
                {% endif %}
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 24px 32px;font-size:14px;line-height:1.6;color:#cbd2df;">
                {{ body_html|safe }}
              </td>
            </tr>
          </table>
          <div style="max-width:640px;margin-top:12px;font-size:11px;color:#5e6880;">
            Sent by the Ship engine. Routing is configured per workspace
            (notifications.channels).
          </div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
)

_NOTIFICATION_TEXT = _env.from_string(
    """\
[Ship {{ level }}] {{ subject }}
{% if ticket_ref %}{{ ticket_ref }}
{% endif %}
{{ body_text }}
"""
)


def render_notification_email(
    *,
    subject: str,
    body_markdown: str,
    level: str,
    ticket_ref: str | None = None,
) -> RenderedEmail:
    """Render an engine ``notify()`` emission for the email channel."""
    body_html = _markdown_to_safe_html(body_markdown)
    ctx = {
        "subject": subject,
        "body_html": body_html,
        "body_text": body_markdown.strip(),
        "level": level.upper(),
        "ticket_ref": ticket_ref,
    }
    return RenderedEmail(
        subject=f"[Ship {level.upper()}] {subject}",
        html=_NOTIFICATION_HTML.render(**ctx),
        text=_NOTIFICATION_TEXT.render(**ctx),
    )
