"""Tree-sitter symbol extractor (ELS-72).

Pin the row shape and per-language coverage so a grammar bump or a
parser regression doesn't silently drop a kind of symbol the agent
relies on.
"""

from __future__ import annotations

import pytest

from backend.app.services.agent.symbol_parser import (
    LANGUAGE_BY_EXTENSION,
    SUPPORTED_LANGUAGES,
    Symbol,
    extract_symbols,
    language_for_path,
)


def test_language_for_path_known_extensions() -> None:
    assert language_for_path("foo.py") == "python"
    assert language_for_path("BACKEND/main.py") == "python"
    assert language_for_path("src/api.ts") == "typescript"
    assert language_for_path("components/Button.tsx") == "tsx"
    assert language_for_path("cmd/server.go") == "go"
    # Compound suffixes still resolve via the trailing extension.
    assert language_for_path("foo.spec.ts") == "typescript"
    assert language_for_path("page.test.tsx") == "tsx"


def test_language_for_path_unknown_extensions() -> None:
    assert language_for_path("README.md") is None
    assert language_for_path("Makefile") is None
    assert language_for_path("config.yaml") is None


def test_supported_languages_match_extension_map() -> None:
    """Pin the supported set so a future grammar add lands cleanly."""
    assert set(SUPPORTED_LANGUAGES) == {
        "python",
        "typescript",
        "tsx",
        "go",
    }
    assert set(LANGUAGE_BY_EXTENSION.values()) == set(SUPPORTED_LANGUAGES)


def test_extract_python_class_method_function_const() -> None:
    src = """
import os

VERSION = "1.0"
default_timeout = 30


def top_level(x: int) -> str:
    return str(x)


async def async_helper() -> None:
    pass


class Repo:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return "hi " + self.name
"""
    syms = {(s.symbol, s.kind) for s in extract_symbols(file="x.py", content=src)}
    assert ("VERSION", "const") in syms
    assert ("default_timeout", "var") in syms
    assert ("top_level", "function") in syms
    assert ("Repo", "class") in syms
    assert ("__init__", "method") in syms
    assert ("greet", "method") in syms


def test_extract_python_async_function_recognised() -> None:
    src = "async def fetch_one(url: str) -> bytes:\n    return b''\n"
    syms = extract_symbols(file="x.py", content=src)
    # tree-sitter-python parses ``async def`` as a function_definition
    # with an ``async`` modifier — extractor must still pick it up.
    names = [s.symbol for s in syms]
    assert "fetch_one" in names


def test_extract_python_signature_keeps_one_line() -> None:
    src = (
        "def long_signature(\n"
        "    a: int,\n"
        "    b: int,\n"
        "    c: int,\n"
        ") -> int:\n"
        "    return a + b + c\n"
    )
    syms = extract_symbols(file="x.py", content=src)
    sig = syms[0].signature
    assert "\n" not in sig
    assert sig.startswith("def long_signature(")
    assert sig.endswith(":")


def test_extract_typescript_function_class_interface_type() -> None:
    src = """
export interface User { id: string; name: string; }
export type Status = "ok" | "fail";

export class Repo<T> {
  private name: string;
  constructor(n: string) { this.name = n; }
  greet(): string { return "hi " + this.name; }
}

export function loadAll(): Promise<User[]> { return Promise.resolve([]); }
const arrow = (x: number) => x * 2;
const data = { x: 1 };
enum Color { Red, Green, Blue }
"""
    rows = extract_symbols(file="x.ts", content=src)
    by = {(s.symbol, s.kind) for s in rows}
    assert ("User", "interface") in by
    assert ("Status", "type") in by
    assert ("Repo", "class") in by
    assert ("greet", "method") in by
    assert ("loadAll", "function") in by
    assert ("arrow", "function") in by  # arrow = function-shape const
    assert ("data", "var") in by
    assert ("Color", "enum") in by


def test_extract_tsx_file_is_parsed_as_tsx_grammar() -> None:
    """A React component file with JSX must not break parsing."""
    src = """
import * as React from "react";

export interface Props { title: string; }

export function Page(props: Props) {
  return <div className="page"><h1>{props.title}</h1></div>;
}
"""
    rows = extract_symbols(file="components/Page.tsx", content=src)
    names = {s.symbol for s in rows}
    assert "Props" in names
    assert "Page" in names


def test_extract_go_func_method_struct_interface() -> None:
    src = """
package main

type Repo struct {
\tName string
}

type Cloner interface {
\tClone(url string) error
}

type ID = int

func New(name string) *Repo {
\treturn &Repo{Name: name}
}

func (r *Repo) Greet() string {
\treturn "hi " + r.Name
}

const Version = "0.1"
var debug = false
"""
    rows = extract_symbols(file="x.go", content=src)
    by = {(s.symbol, s.kind) for s in rows}
    assert ("Repo", "struct") in by
    assert ("Cloner", "interface") in by
    assert ("ID", "type") in by
    assert ("New", "function") in by
    assert ("Greet", "method") in by
    assert ("Version", "const") in by
    assert ("debug", "var") in by


def test_extract_unsupported_extension_returns_empty() -> None:
    """Markdown / YAML / random filenames don't blow up — they return [].

    The agent tool wraps multiple files; a passthrough-on-unsupported
    keeps the call going for the rest of the batch.
    """
    assert extract_symbols(file="README.md", content="# heading\n") == []
    assert extract_symbols(file="ci.yml", content="name: x\n") == []
    assert extract_symbols(file="empty.py", content="") == []


def test_extract_handles_syntax_errors_gracefully() -> None:
    """Tree-sitter recovers from broken code; we still return reachable syms."""
    src = "def foo(:\n    pass\n\ndef bar():\n    pass\n"
    rows = extract_symbols(file="broken.py", content=src)
    names = {s.symbol for s in rows}
    # ``foo`` is too broken to extract a name; ``bar`` should survive.
    assert "bar" in names


def test_symbol_as_dict_shape_is_stable() -> None:
    """The agent contract pins these five keys — don't drift."""
    sym = Symbol(
        file="x.py",
        symbol="foo",
        kind="function",
        line=12,
        signature="def foo():",
    )
    assert sym.as_dict() == {
        "file": "x.py",
        "symbol": "foo",
        "kind": "function",
        "line": 12,
        "signature": "def foo():",
    }
