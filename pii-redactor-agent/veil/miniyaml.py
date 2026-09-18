"""A small YAML subset parser.

VEIL's engine is standard-library-only by design, which rules out PyYAML. Rather
than pretend to support all of YAML, this module parses a **documented subset**
and raises `ConfigError` with a line number the moment it meets anything else.
A config that silently mis-parses is far more dangerous than one that refuses.

Supported
---------
- comments (``#`` to end of line) and blank lines
- nested mappings, indented with spaces
- block sequences (``- item``), including sequences of mappings
- inline sequences (``[a, b, c]``)
- single- or double-quoted strings, and bare scalars
- integers, floats, booleans (``true``/``false``), null (``null``/``~``)

Not supported (raises ConfigError)
----------------------------------
- tab indentation
- anchors, aliases and merge keys (``&``, ``*``, ``<<``)
- multi-line block scalars (``|``, ``>``)
- multi-document streams (``---``)
- flow mappings (``{a: 1}``) other than in an inline sequence of scalars
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from .errors import ConfigError

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting quoted sections."""
    out: List[str] = []
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            elif char == "\\" and index + 1 < len(line):
                out.append(line[index + 1])
                index += 1
        elif char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "#":
            break
        else:
            out.append(char)
        index += 1
    return "".join(out).rstrip()


def _parse_scalar(token: str, line_number: int) -> Any:
    text = token.strip()
    if text == "":
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("null", "~", "none"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    if text.startswith("&") or text.startswith("*"):
        raise ConfigError(
            f"YAML anchors and aliases are not supported (line {line_number}): {text!r}"
        )
    if text in ("|", ">", "|-", ">-", "|+", ">+"):
        raise ConfigError(
            f"multi-line block scalars are not supported (line {line_number})"
        )
    if text.startswith("{") and text.endswith("}"):
        raise ConfigError(
            f"flow mappings are not supported (line {line_number}): {text!r}"
        )
    return text


def _parse_inline_sequence(token: str, line_number: int) -> List[Any]:
    inner = token.strip()[1:-1].strip()
    if not inner:
        return []
    items: List[Any] = []
    current: List[str] = []
    quote = ""
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char == ",":
            items.append(_parse_scalar("".join(current), line_number))
            current = []
        else:
            current.append(char)
    items.append(_parse_scalar("".join(current), line_number))
    return [item for item in items if item is not None or item == 0 or item is False]


def _split_key(line: str, line_number: int) -> Tuple[str, str]:
    """Split ``key: value`` on the first colon outside quotes."""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == ":":
            key = line[:index].strip()
            if not key:
                raise ConfigError(f"missing key before ':' on line {line_number}")
            return key, line[index + 1:].strip()
    raise ConfigError(f"expected 'key: value' but found no ':' on line {line_number}: {line!r}")


def _tokenise(source: str) -> List[Tuple[int, int, str]]:
    """Return (line_number, indent, content) for every meaningful line."""
    tokens: List[Tuple[int, int, str]] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise ConfigError(
                f"tabs are not allowed for indentation (line {number}); use spaces"
            )
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if stripped.strip() in ("---", "..."):
            raise ConfigError(
                f"multi-document YAML is not supported (line {number})"
            )
        indent = len(stripped) - len(stripped.lstrip(" "))
        tokens.append((number, indent, stripped.strip()))
    return tokens


def parse(source: str) -> Any:
    """Parse the supported YAML subset into Python data."""
    if not isinstance(source, str):
        raise ConfigError("configuration source must be a string")

    tokens = _tokenise(source)
    if not tokens:
        return {}

    value, consumed = _parse_block(tokens, 0, tokens[0][1])
    if consumed != len(tokens):
        line_number = tokens[consumed][0]
        raise ConfigError(
            f"unexpected indentation or content on line {line_number}: {tokens[consumed][2]!r}"
        )
    return value


def _parse_block(tokens: List[Tuple[int, int, str]], index: int, indent: int) -> Tuple[Any, int]:
    if index >= len(tokens):
        return {}, index
    if tokens[index][2].startswith("- "):
        return _parse_sequence(tokens, index, indent)
    if tokens[index][2] == "-":
        return _parse_sequence(tokens, index, indent)
    return _parse_mapping(tokens, index, indent)


def _parse_sequence(tokens: List[Tuple[int, int, str]], index: int,
                    indent: int) -> Tuple[List[Any], int]:
    items: List[Any] = []
    while index < len(tokens):
        number, level, content = tokens[index]
        if level < indent or not (content == "-" or content.startswith("- ")):
            break
        if level > indent:
            raise ConfigError(f"unexpected indent inside sequence on line {number}")

        body = content[1:].strip()
        index += 1
        if not body:
            child, index = _parse_block(tokens, index, _child_indent(tokens, index, indent))
            items.append(child)
            continue

        if ":" in body and not body.startswith(("[", "'", '"')):
            # Sequence entry that opens a mapping: "- key: value" plus any
            # deeper indented keys that belong to the same entry.
            key, rest = _split_key(body, number)
            entry: dict = {}
            if rest:
                entry[key] = (
                    _parse_inline_sequence(rest, number)
                    if rest.startswith("[")
                    else _parse_scalar(rest, number)
                )
            else:
                child_indent = _child_indent(tokens, index, indent)
                if index < len(tokens) and tokens[index][1] > level:
                    child, index = _parse_block(tokens, index, child_indent)
                    entry[key] = child
                else:
                    entry[key] = None

            if index < len(tokens) and tokens[index][1] > level:
                child_indent = _child_indent(tokens, index, level)
                extra, index = _parse_mapping(tokens, index, child_indent)
                if not isinstance(extra, dict):
                    raise ConfigError(
                        f"expected mapping continuation on line {tokens[index - 1][0]}"
                    )
                entry.update(extra)
            items.append(entry)
            continue

        if body.startswith("["):
            if not body.endswith("]"):
                raise ConfigError(f"unterminated inline sequence on line {number}")
            items.append(_parse_inline_sequence(body, number))
        else:
            items.append(_parse_scalar(body, number))
    return items, index


def _parse_mapping(tokens: List[Tuple[int, int, str]], index: int,
                   indent: int) -> Tuple[dict, int]:
    result: dict = {}
    while index < len(tokens):
        number, level, content = tokens[index]
        if level < indent:
            break
        if level > indent:
            raise ConfigError(
                f"unexpected indent on line {number} (expected {indent} spaces, found {level})"
            )
        if content == "-" or content.startswith("- "):
            break

        key, rest = _split_key(content, number)
        index += 1
        if rest:
            if rest.startswith("["):
                if not rest.endswith("]"):
                    raise ConfigError(f"unterminated inline sequence on line {number}")
                result[key] = _parse_inline_sequence(rest, number)
            else:
                result[key] = _parse_scalar(rest, number)
            continue

        if index < len(tokens) and tokens[index][1] > indent:
            child, index = _parse_block(tokens, index, tokens[index][1])
            result[key] = child
        elif index < len(tokens) and tokens[index][1] == indent and \
                (tokens[index][2] == "-" or tokens[index][2].startswith("- ")):
            child, index = _parse_sequence(tokens, index, indent)
            result[key] = child
        else:
            result[key] = None
    return result, index


def _child_indent(tokens: List[Tuple[int, int, str]], index: int, parent: int) -> int:
    if index < len(tokens) and tokens[index][1] > parent:
        return tokens[index][1]
    return parent + 2


def load(path: str) -> Any:
    """Read and parse a file from disk."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return parse(handle.read())
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
