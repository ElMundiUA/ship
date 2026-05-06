"""On-demand symbol extraction with tree-sitter (ELS-72).

The ``repo_symbols`` agent tool parses one file at a time using
GitHub-App-fetched contents (no on-disk checkout, no preindex). The
shape of each emitted row is fixed across languages so the agent
gets the same structure whether it asked about a Python module or
a Go file:

    {
      "file": "<repo-relative path>",
      "symbol": "<identifier>",
      "kind": "function" | "class" | "method" | "interface" | "type" |
              "struct" | "enum" | "var" | "const",
      "line": 1-based int,
      "signature": "<one-line signature>",
    }

Why tree-sitter and not the stdlib ``ast`` for Python?

* One traversal shape and one symbol-row schema covers all three
  pilot languages, so the tool's output stays uniform.
* tree-sitter parsers tolerate syntax errors gracefully — the agent
  often points the tool at a file that's mid-edit; a strict parser
  would explode where tree-sitter recovers and still surfaces the
  reachable symbols.

Caching: parsers and languages are loaded once at process scope
(both are thread-safe and cheap to share). Per-call we build a fresh
``tree_sitter.Parser`` instance? No — Parser is also reusable; we
re-use it as ``Parser.set_language`` is a no-op once set. We don't
cache per-file *parses* in this module — that's the job of the
optional ``RepoCodeSymbol`` table the ticket marks as v1+.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


logger = logging.getLogger(__name__)


# Languages this module knows how to extract symbols from. Add more by
# (1) wiring up a tree-sitter language pack name, (2) registering an
# extractor below, (3) extending ``LANGUAGE_BY_EXTENSION``.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("python", "typescript", "tsx", "go")


# File extension → tree-sitter language name. ``.tsx`` parses through
# the ``tsx`` grammar (a superset that tolerates JSX), not vanilla
# ``typescript`` — keeps React component files from blowing up.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}


@dataclass(frozen=True, slots=True)
class Symbol:
    """One extracted symbol. Matches the agent-facing JSON row shape."""

    file: str
    symbol: str
    kind: str
    line: int
    signature: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "kind": self.kind,
            "line": self.line,
            "signature": self.signature,
        }


def language_for_path(path: str) -> str | None:
    """Return the tree-sitter language name for ``path``, or ``None``
    when the file extension isn't one this module parses.

    The ``.spec.ts`` / ``.test.tsx`` etc. compound suffixes still match
    on the *last* component (``.ts`` / ``.tsx``) — the matcher walks
    extensions right-to-left and bails at the first hit.
    """
    lower = path.lower()
    for ext, lang in LANGUAGE_BY_EXTENSION.items():
        if lower.endswith(ext):
            return lang
    return None


@lru_cache(maxsize=8)
def _parser(language: str):
    """Return a process-scoped parser for ``language``.

    ``tree_sitter_language_pack`` lazy-loads grammar wheels — the
    import cost is paid on first call per language and cached for
    the lifetime of the process. ``lru_cache`` size 8 covers the four
    pilot grammars with headroom; bumps don't cost anything.
    """
    from tree_sitter_language_pack import get_parser

    return get_parser(language)


def extract_symbols(*, file: str, content: str) -> list[Symbol]:
    """Parse ``content`` and return the symbols it declares.

    ``file`` is the repo-relative path; it's only used to (a) infer
    the language and (b) stamp it on the returned rows. Returns
    ``[]`` for unsupported extensions, empty content, or unparseable
    files (rather than raising — the agent tool wraps multiple files
    and one broken file shouldn't fail the whole call).
    """
    if not content:
        return []
    language = language_for_path(file)
    if language is None:
        return []
    try:
        parser = _parser(language)
        source = content.encode("utf-8")
        tree = parser.parse(source)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "symbol_parser: parse failed file=%s lang=%s err=%s",
            file,
            language,
            exc,
        )
        return []

    extractor = _EXTRACTORS.get(language)
    if extractor is None:
        return []
    try:
        return extractor(file=file, source=source, root=tree.root_node)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "symbol_parser: extractor failed file=%s lang=%s err=%s",
            file,
            language,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Per-language extractors
# ---------------------------------------------------------------------------


def _node_text(source: bytes, node: Any) -> str:
    """Decode the byte slice ``[node.start_byte, node.end_byte)``."""
    return source[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _first_line(text: str, *, max_chars: int = 240) -> str:
    """Collapse multi-line declarations into one signature-shaped line.

    Function signatures spanning multiple lines (typed Python defs,
    Go function with grouped params) get squished to single-line so
    the agent's row stays compact. Caps at ``max_chars`` so a giant
    parameter list doesn't blow the response budget.
    """
    flat = " ".join(text.split())
    if len(flat) > max_chars:
        flat = flat[: max_chars - 1].rstrip() + "…"
    return flat


def _line_of(node: Any) -> int:
    """1-based line number of ``node``'s start point."""
    return int(node.start_point[0]) + 1


def _python_signature(source: bytes, node: Any) -> str:
    """Python: take everything from ``def``/``class`` to the colon."""
    text = _node_text(source, node)
    head, _, _ = text.partition(":")
    return _first_line(head + ":")


def _extract_python(*, file: str, source: bytes, root: Any) -> list[Symbol]:
    """Walk the AST and emit definitions.

    Recognised: top-level `def`/`async def` (function), classes,
    methods inside a class body, module-level `var = ...` /
    `CONST = ...` / `TYPE: <ann> = ...`. We stop at the first level
    of class nesting — symbols inside nested functions are usually
    closures the operator doesn't need exposed.
    """
    out: list[Symbol] = []
    stack: list[tuple[Any, str | None]] = [(root, None)]
    while stack:
        node, parent_kind = stack.pop()
        kind_name = node.type
        if kind_name in ("function_definition",):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            sig = _python_signature(source, node)
            kind = "method" if parent_kind == "class" else "function"
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind=kind,
                    line=_line_of(node),
                    signature=sig,
                )
            )
            # Don't descend into nested defs — too noisy.
            continue
        if kind_name == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            sig = _python_signature(source, node)
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="class",
                    line=_line_of(node),
                    signature=sig,
                )
            )
            # Walk one level deeper to grab methods on this class.
            body_node = node.child_by_field_name("body")
            if body_node is not None:
                for child in body_node.children:
                    stack.append((child, "class"))
            continue
        if (
            kind_name in ("assignment", "expression_statement")
            and parent_kind is None
        ):
            # Module-level constants. We pick up bare ``NAME = ...`` /
            # ``NAME: ann = ...`` only; tuple/star unpacks would clutter
            # the output without helping.
            target = None
            if kind_name == "assignment":
                target = node.child_by_field_name("left")
            else:
                child = node.child(0)
                if child is not None and child.type == "assignment":
                    target = child.child_by_field_name("left")
            if target is not None and target.type in (
                "identifier",
                "type_alias_statement",
            ):
                name = _node_text(source, target)
                if name and name.isidentifier():
                    out.append(
                        Symbol(
                            file=file,
                            symbol=name,
                            kind="const" if name.isupper() else "var",
                            line=_line_of(node),
                            signature=_first_line(_node_text(source, node)),
                        )
                    )
            continue
        for child in node.children:
            stack.append((child, parent_kind))
    return out


def _ts_signature(source: bytes, node: Any) -> str:
    """TypeScript: signature is the head up to the first `{` (body open)."""
    text = _node_text(source, node)
    head, _, _ = text.partition("{")
    head = head.rstrip()
    if not head.endswith(("{", ":", ";")):
        head += " {"
    return _first_line(head)


def _extract_typescript(*, file: str, source: bytes, root: Any) -> list[Symbol]:
    """TS / TSX: function/class/interface/type/enum + top-level const/let/var."""
    out: list[Symbol] = []
    cursor = [(root, None)]
    while cursor:
        node, parent_kind = cursor.pop()
        kind_name = node.type
        if kind_name in (
            "function_declaration",
            "generator_function_declaration",
        ):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="function",
                    line=_line_of(node),
                    signature=_ts_signature(source, node),
                )
            )
            continue
        if kind_name in ("class_declaration", "abstract_class_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="class",
                    line=_line_of(node),
                    signature=_ts_signature(source, node),
                )
            )
            body_node = node.child_by_field_name("body")
            if body_node is not None:
                for child in body_node.children:
                    cursor.append((child, "class"))
            continue
        if kind_name == "method_definition" and parent_kind == "class":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="method",
                    line=_line_of(node),
                    signature=_ts_signature(source, node),
                )
            )
            continue
        if kind_name in ("interface_declaration",):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="interface",
                    line=_line_of(node),
                    signature=_ts_signature(source, node),
                )
            )
            continue
        if kind_name == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="type",
                    line=_line_of(node),
                    signature=_first_line(_node_text(source, node)),
                )
            )
            continue
        if kind_name == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="enum",
                    line=_line_of(node),
                    signature=_ts_signature(source, node),
                )
            )
            continue
        if (
            kind_name == "lexical_declaration"
            or kind_name == "variable_declaration"
        ) and parent_kind is None:
            # ``const x = …``, ``let x = …``, ``var x = …`` at top level.
            for declarator in node.children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                if name_node is None or name_node.type != "identifier":
                    continue
                value_node = declarator.child_by_field_name("value")
                arrow = (
                    value_node is not None
                    and value_node.type == "arrow_function"
                )
                kind = "function" if arrow else "var"
                out.append(
                    Symbol(
                        file=file,
                        symbol=_node_text(source, name_node),
                        kind=kind,
                        line=_line_of(node),
                        signature=_ts_signature(source, node),
                    )
                )
            continue
        for child in node.children:
            cursor.append((child, parent_kind))
    return out


def _go_signature(source: bytes, node: Any) -> str:
    text = _node_text(source, node)
    head, _, _ = text.partition("{")
    head = head.rstrip()
    if not head.endswith(("{", ";")):
        head += " {"
    return _first_line(head)


def _extract_go(*, file: str, source: bytes, root: Any) -> list[Symbol]:
    """Go: func/method declarations + type spec (struct/interface/alias)."""
    out: list[Symbol] = []
    for node in _walk(root):
        kind_name = node.type
        if kind_name == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="function",
                    line=_line_of(node),
                    signature=_go_signature(source, node),
                )
            )
        elif kind_name == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            out.append(
                Symbol(
                    file=file,
                    symbol=_node_text(source, name_node),
                    kind="method",
                    line=_line_of(node),
                    signature=_go_signature(source, node),
                )
            )
        elif kind_name == "type_declaration":
            # Two shapes the Go grammar uses:
            #   type Foo struct { ... } / type Foo interface { ... }
            #     → ``type_spec`` child with ``name`` + ``type`` fields.
            #   type ID = int
            #     → ``type_alias`` child where the first identifier is
            #       the new name and field lookup returns null (the
            #       grammar doesn't surface a ``name`` field).
            for spec in node.children:
                if spec.type == "type_spec":
                    name_node = spec.child_by_field_name("name")
                    type_node = spec.child_by_field_name("type")
                    if name_node is None:
                        continue
                    kind = "type"
                    if type_node is not None:
                        if type_node.type == "struct_type":
                            kind = "struct"
                        elif type_node.type == "interface_type":
                            kind = "interface"
                    out.append(
                        Symbol(
                            file=file,
                            symbol=_node_text(source, name_node),
                            kind=kind,
                            line=_line_of(spec),
                            signature=_first_line(_node_text(source, spec)),
                        )
                    )
                elif spec.type == "type_alias":
                    name_node = next(
                        (
                            c
                            for c in spec.children
                            if c.type == "type_identifier"
                        ),
                        None,
                    )
                    if name_node is None:
                        continue
                    out.append(
                        Symbol(
                            file=file,
                            symbol=_node_text(source, name_node),
                            kind="type",
                            line=_line_of(spec),
                            signature=_first_line(_node_text(source, spec)),
                        )
                    )
        elif kind_name in ("const_declaration", "var_declaration"):
            for spec in node.children:
                if spec.type not in ("const_spec", "var_spec"):
                    continue
                name_node = spec.child_by_field_name("name")
                if name_node is None:
                    continue
                out.append(
                    Symbol(
                        file=file,
                        symbol=_node_text(source, name_node),
                        kind="const" if kind_name == "const_declaration" else "var",
                        line=_line_of(spec),
                        signature=_first_line(_node_text(source, spec)),
                    )
                )
    return out


def _walk(root: Any):
    """Iterate the AST in DFS order (top-level first); used by extractors
    that don't care about parent context.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        # Push children in reverse so iteration is left-to-right.
        for i in range(node.child_count - 1, -1, -1):
            stack.append(node.children[i])


_EXTRACTORS = {
    "python": _extract_python,
    "typescript": _extract_typescript,
    "tsx": _extract_typescript,  # same grammar shape modulo JSX
    "go": _extract_go,
}


__all__ = [
    "LANGUAGE_BY_EXTENSION",
    "SUPPORTED_LANGUAGES",
    "Symbol",
    "extract_symbols",
    "language_for_path",
]
