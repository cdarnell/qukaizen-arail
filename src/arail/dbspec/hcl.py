"""A strict parser for the HCL subset the ARAIL spec tree uses.

Why hand-rolled rather than ``python-hcl2``: this runs at build time on spec
files we author ourselves, and ARAIL is a blueprint that other people install
on their own machines — often airgapped. Adding a runtime dependency (and its
lark transitive) to parse four files we control is a poor trade.

The safety property that makes this acceptable is strictness: anything outside
the supported subset is a hard error naming the file and line, never a silent
misparse. Notably unsupported, and rejected explicitly: interpolation
(``${...}``), heredocs, functions, arithmetic, conditionals, and bare
identifier references. If the spec ever needs those, take the dependency
rather than growing this file.

Supported:
    // line comment      # line comment      /* block comment */
    block "label" "label2" { ... }
    attribute = "string" | 123 | 1.5 | true | false | null
    attribute = [v, v, v]
    attribute = { key = v, key = v }

Blocks nest by label:  ``table "worlds" { ... }``  parses to
``{"table": {"worlds": {...}}}``. An unlabelled block parses to
``{"defaults": {...}}``. Duplicate block labels and duplicate attribute keys
are errors, because in a spec file a silent overwrite is a bug.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

__all__ = ["parse", "HCLError"]


class HCLError(ValueError):
    """A spec file could not be parsed. Always names source and line."""

    def __init__(self, message: str, source: str, line: int) -> None:
        super().__init__(f"{source}:{line}: {message}")
        self.source = source
        self.line = line


_PUNCT = {"{", "}", "[", "]", "=", ",", "(", ")"}


class _Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value: Any, line: int) -> None:
        self.kind = kind  # ident | string | number | bool | null | punct | eof
        self.value = value
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.kind} {self.value!r} line={self.line}>"


def _tokenize(text: str, source: str) -> List[_Token]:
    tokens: List[_Token] = []
    i = 0
    line = 1
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue

        # Comments
        if text.startswith("//", i) or ch == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise HCLError("unterminated block comment", source, line)
            line += text.count("\n", i, j)
            i = j + 2
            continue

        # Strings
        if ch == '"':
            i += 1
            buf: List[str] = []
            start_line = line
            while True:
                if i >= n:
                    raise HCLError("unterminated string", source, start_line)
                c = text[i]
                if c == "\\":
                    if i + 1 >= n:
                        raise HCLError("unterminated escape", source, line)
                    nxt = text[i + 1]
                    mapping = {"n": "\n", "t": "\t", "r": "\r",
                               '"': '"', "\\": "\\"}
                    if nxt not in mapping:
                        raise HCLError(
                            f"unsupported escape \\{nxt}", source, line)
                    buf.append(mapping[nxt])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                if c == "\n":
                    raise HCLError(
                        "newline in string (heredocs are not supported)",
                        source, line)
                if c == "$" and text.startswith("${", i):
                    raise HCLError(
                        "interpolation ${...} is not supported in the spec "
                        "subset", source, line)
                buf.append(c)
                i += 1
            tokens.append(_Token("string", "".join(buf), start_line))
            continue

        # Heredoc — reject explicitly rather than misparse
        if text.startswith("<<", i):
            raise HCLError("heredocs are not supported in the spec subset",
                           source, line)

        # Numbers
        if ch.isdigit() or (ch == "-" and i + 1 < n and text[i + 1].isdigit()):
            j = i + 1
            while j < n and (text[j].isdigit() or text[j] in ".eE+-"):
                # Stop at '-' / '+' unless it is an exponent sign
                if text[j] in "+-" and text[j - 1] not in "eE":
                    break
                j += 1
            raw = text[i:j]
            try:
                value: Any = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    raise HCLError(f"bad number {raw!r}", source, line) from None
            tokens.append(_Token("number", value, line))
            i = j
            continue

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_-"):
                j += 1
            word = text[i:j]
            if word == "true":
                tokens.append(_Token("bool", True, line))
            elif word == "false":
                tokens.append(_Token("bool", False, line))
            elif word == "null":
                tokens.append(_Token("null", None, line))
            else:
                tokens.append(_Token("ident", word, line))
            i = j
            continue

        if ch in _PUNCT:
            tokens.append(_Token("punct", ch, line))
            i += 1
            continue

        raise HCLError(f"unexpected character {ch!r}", source, line)

    tokens.append(_Token("eof", None, line))
    return tokens


class _Parser:
    def __init__(self, tokens: List[_Token], source: str) -> None:
        self.tokens = tokens
        self.source = source
        self.pos = 0

    # -- token helpers -------------------------------------------------
    @property
    def cur(self) -> _Token:
        return self.tokens[self.pos]

    def advance(self) -> _Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_punct(self, ch: str) -> _Token:
        tok = self.cur
        if tok.kind != "punct" or tok.value != ch:
            raise HCLError(f"expected {ch!r}, got {self._describe(tok)}",
                           self.source, tok.line)
        return self.advance()

    @staticmethod
    def _describe(tok: _Token) -> str:
        if tok.kind == "eof":
            return "end of file"
        return f"{tok.kind} {tok.value!r}"

    def _skip_commas(self) -> None:
        while self.cur.kind == "punct" and self.cur.value == ",":
            self.advance()

    # -- grammar -------------------------------------------------------
    def parse_body(self, *, top_level: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        while True:
            self._skip_commas()
            tok = self.cur
            if tok.kind == "eof":
                if not top_level:
                    raise HCLError("unexpected end of file inside block",
                                   self.source, tok.line)
                return body
            if tok.kind == "punct" and tok.value == "}":
                if top_level:
                    raise HCLError("unexpected '}'", self.source, tok.line)
                return body
            if tok.kind != "ident":
                raise HCLError(
                    f"expected an attribute or block name, got "
                    f"{self._describe(tok)}", self.source, tok.line)

            name = self.advance().value

            nxt = self.cur
            if nxt.kind == "punct" and nxt.value == "=":
                self.advance()
                value = self.parse_value()
                if name in body:
                    raise HCLError(f"duplicate attribute {name!r}",
                                   self.source, nxt.line)
                body[name] = value
                continue

            # Block: zero or more string labels, then '{'
            labels: List[str] = []
            while self.cur.kind == "string":
                labels.append(self.advance().value)
            if not (self.cur.kind == "punct" and self.cur.value == "{"):
                raise HCLError(
                    f"expected '=' or a block body after {name!r}, got "
                    f"{self._describe(self.cur)}", self.source, self.cur.line)
            open_line = self.cur.line
            self.advance()
            inner = self.parse_body(top_level=False)
            self.expect_punct("}")
            self._insert_block(body, name, labels, inner, open_line)
        # unreachable

    def _insert_block(self, body: Dict[str, Any], name: str,
                      labels: List[str], inner: Dict[str, Any],
                      line: int) -> None:
        if not labels:
            if name in body:
                raise HCLError(f"duplicate block {name!r}", self.source, line)
            body[name] = inner
            return
        if len(labels) > 2:
            raise HCLError(
                f"block {name!r} has {len(labels)} labels; at most 2 are "
                "supported", self.source, line)
        bucket = body.setdefault(name, {})
        if not isinstance(bucket, dict):
            raise HCLError(
                f"{name!r} is used both as an attribute and as a block",
                self.source, line)
        if len(labels) == 1:
            if labels[0] in bucket:
                raise HCLError(
                    f"duplicate block {name} {labels[0]!r}", self.source, line)
            bucket[labels[0]] = inner
            return
        outer, innermost = labels
        nested = bucket.setdefault(outer, {})
        if innermost in nested:
            raise HCLError(
                f"duplicate block {name} {outer!r} {innermost!r}",
                self.source, line)
        nested[innermost] = inner

    def parse_value(self) -> Any:
        tok = self.cur
        if tok.kind in ("string", "number", "bool", "null"):
            self.advance()
            return tok.value
        if tok.kind == "punct" and tok.value == "[":
            self.advance()
            items: List[Any] = []
            while True:
                self._skip_commas()
                if self.cur.kind == "punct" and self.cur.value == "]":
                    self.advance()
                    return items
                if self.cur.kind == "eof":
                    raise HCLError("unterminated list", self.source, tok.line)
                items.append(self.parse_value())
        if tok.kind == "punct" and tok.value == "{":
            self.advance()
            obj = self.parse_body(top_level=False)
            self.expect_punct("}")
            return obj
        if tok.kind == "ident":
            raise HCLError(
                f"bare identifier {tok.value!r} is not a supported value; "
                "quote it, or use true/false/null",
                self.source, tok.line)
        raise HCLError(f"expected a value, got {self._describe(tok)}",
                       self.source, tok.line)


def parse(text: str, *, source: str = "<spec>") -> Dict[str, Any]:
    """Parse an HCL-subset document into nested dicts.

    Raises :class:`HCLError` — never returns a partial or guessed parse.
    """
    parser = _Parser(_tokenize(text, source), source)
    return parser.parse_body(top_level=True)
