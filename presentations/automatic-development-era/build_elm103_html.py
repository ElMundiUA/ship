#!/usr/bin/env python3
"""Convert elm103-work-item.txt description + comments to HTML fragment for the deck."""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
raw = (ROOT / "elm103-work-item.txt").read_text(encoding="utf-8")

title_m = re.search(r"<title>(.*?)</title>", raw, re.DOTALL)
issue_title_html = html.escape(title_m.group(1).strip()) if title_m else "ELM-103"

desc_m = re.search(r"<description>(.*?)</description>", raw, re.DOTALL)
if not desc_m:
    raise SystemExit("no description")
desc = desc_m.group(1).strip()

orig_m = re.search(
    r"\n?---\s*\n\*\*Original report \(screenshot\):\*\*\s*\[image\]\((https://[^)]+)\)\s*$",
    desc,
    re.DOTALL,
)
orig_url = orig_m.group(1) if orig_m else None
if orig_m:
    desc = desc[: orig_m.start()].rstrip()

# strip leading ## from first block if present
blocks = re.split(r"\n(?=## )", desc)


def inline_md(s: str) -> str:
    codes: list[str] = []

    def code_ph(m):
        codes.append(html.escape(m.group(1)))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", code_ph, s)
    s = html.escape(s)
    for i, c in enumerate(codes):
        s = s.replace(f"\x00{i}\x00", f"<code>{c}</code>")
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: "<strong>" + m.group(1) + "</strong>", s)
    return s


def format_comment_body(body: str) -> str:
    body = body.strip()
    marker_m = re.search(r"\[GitHub SDLC:(\w+)\]\s*$", body, re.MULTILINE)
    marker = marker_m.group(1) if marker_m else None
    if marker_m:
        body = body[: marker_m.start()].strip()
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("* "):
            items = []
            while i < len(lines) and lines[i].startswith("* "):
                items.append("<li>" + inline_md(lines[i][2:].strip()) + "</li>")
                i += 1
            out.append("<ul class=\"elm-ul elm-ul-tight\">" + "".join(items) + "</ul>")
            continue
        if line.strip():
            out.append("<p class=\"elm-p\">" + inline_md(line) + "</p>")
        i += 1
    html = "\n".join(out)
    if marker:
        html += (
            f'<p class="elm-marker-wrap"><span class="elm-marker">[GitHub SDLC:{marker}]</span></p>'
        )
    return html


def render_block(block: str) -> str:
    lines = block.strip().split("\n")
    if not lines:
        return ""
    first = lines[0]
    if first.startswith("## "):
        title = inline_md(first[3:].strip())
        body_lines = lines[1:]
    else:
        title = None
        body_lines = lines

    parts: list[str] = []
    if title:
        parts.append(f'<h2 class="elm-sec">{title}</h2>')

    i = 0
    while i < len(body_lines):
        line = body_lines[i].rstrip()
        if not line:
            i += 1
            continue
        if re.match(r"^\d+\.\s", line):
            items = []
            while i < len(body_lines) and re.match(r"^\d+\.\s", body_lines[i]):
                items.append("<li>" + inline_md(re.sub(r"^\d+\.\s*", "", body_lines[i])) + "</li>")
                i += 1
            parts.append("<ol class='elm-ol'>" + "".join(items) + "</ol>")
            continue
        if line.startswith("* ") or line.startswith("- "):
            items = []
            while i < len(body_lines) and (body_lines[i].startswith("* ") or body_lines[i].startswith("- ")):
                t = body_lines[i][2:].strip()
                items.append("<li>" + inline_md(t) + "</li>")
                i += 1
            parts.append("<ul class='elm-ul'>" + "".join(items) + "</ul>")
            continue
        if line.startswith("**") and line.endswith("**") and body_lines[i + 1 : i + 2] == []:
            parts.append("<p class='elm-p-strong'>" + inline_md(line) + "</p>")
            i += 1
            continue
        if line == "---":
            parts.append("<hr class='elm-hr' />")
            i += 1
            continue
        if re.match(r"^\*\*[^*]+\*\*$", line.strip()) and "(" in line:
            parts.append("<p class='elm-p elm-subhead'>" + inline_md(line.strip()) + "</p>")
            i += 1
            continue
        # paragraph
        para = [line]
        i += 1
        while i < len(body_lines) and body_lines[i].strip() and not body_lines[i].startswith(
            ("## ", "* ", "- ", "---")
        ) and not re.match(r"^\d+\.\s", body_lines[i]):
            if body_lines[i].startswith("**") and ":**" in body_lines[i]:
                break
            para.append(body_lines[i].rstrip())
            i += 1
        parts.append("<p class='elm-p'>" + inline_md(" ".join(para)) + "</p>")

    return "\n".join(parts)


article_html = "\n".join(render_block(b) for b in blocks if b.strip())
if orig_url:
    article_html += (
        '<p class="elm-p elm-screenshot"><strong>Original report (screenshot):</strong> '
        f'<a class="elm-a" href="{html.escape(orig_url, quote=True)}" target="_blank" rel="noopener">'
        "відкрити зображення в Linear</a></p>"
    )

# comments
comments_html = []
for cm in re.finditer(
    r"<comment author=\"([^\"]+)\" created-at=\"([^\"]+)\">\s*(.*?)\s*</comment>",
    raw,
    re.DOTALL,
):
    author, created, body = cm.group(1), cm.group(2), cm.group(3).strip()
    role = "ba" if "BA spec" in body or "[GitHub SDLC:ba]" in body else "intake"
    cls = "elm-comment" + (" elm-comment-ba" if role == "ba" else " elm-comment-intake")
    inner = format_comment_body(body)
    comments_html.append(
        f'<aside class="{cls}"><div class="elm-comment-meta">{html.escape(author)} · {html.escape(created)}</div>'
        f'<div class="elm-comment-body">{inner}</div></aside>'
    )

out = f"""<div class="elm-render-pane">
<header class="elm-doc-hdr">
<p class="elm-work-line">Work on Linear issue <strong>ELM-103</strong></p>
<h3 class="elm-issue-title-h">{issue_title_html}</h3>
<div class="elm-pillrow">
<span class="elm-pill">Team: Elmundi</span>
<span class="elm-pill elm-bug">Bug</span>
<span class="elm-pill elm-ready">ready:developer</span>
<span class="elm-pill">stage:developer</span>
<span class="elm-pill">Improvement</span>
<span class="elm-pill elm-proj">ElMundi pre-release</span>
</div>
</header>
<article class="elm-doc">
{article_html}
</article>
<section class="elm-comments" aria-label="SDLC коментарі">
<h2 class="elm-sec elm-sec-comments">Коментарі в треді</h2>
{"".join(comments_html)}
</section>
</div>"""

(ROOT / "elm103-rendered-fragment.html").write_text(out, encoding="utf-8")
print("Wrote", ROOT / "elm103-rendered-fragment.html", "chars", len(out))
