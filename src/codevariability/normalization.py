"""Reproducible preprocessing for textual and code-token analyses."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pygments import lex
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment, Operator, Text
from pygments.util import ClassNotFound

TEXT_NORMALIZATION = "unicode_words_casefold_v1"
CODE_TOKENIZATION = "pygments_lexemes_with_generic_fallback_v2"

_WORD = re.compile(r"(?u)\b\w+\b")
_GENERIC_CODE_TOKEN = re.compile(
    r"""(?x)
    (?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    |(?:[^\W\d]\w*)
    |(?:===|!==|>>>|<<=|>>=|\*\*=|//=|\?\?=|\*\*|//|==|!=|<=|>=|&&|\|\||\?\?|\?\.|=>|->|::|\.\.\.|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|:=|<<|>>)
    |(?:[^\s\w])
    """,
    flags=re.UNICODE,
)
_COMPOUND_OPERATORS = {
    "===",
    "!==",
    ">>>",
    "<<=",
    ">>=",
    "**",
    "//",
    "==",
    "!=",
    "<=",
    ">=",
    "**=",
    "//=",
    "??=",
    "&&",
    "||",
    "??",
    "?.",
    "=>",
    "->",
    "::",
    "...",
    "++",
    "--",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    ":=",
    "<<",
    ">>",
}


@dataclass(frozen=True, slots=True)
class CodeSegment:
    """A code segment and its optional Markdown language identifier."""

    code: str
    language: str | None = None
    start_line: int = 1


def normalize_text(text: str) -> str:
    """Apply only language-independent textual normalization."""

    normalized = (
        unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    )
    return " ".join(normalized.casefold().split())


def text_tokens(text: str) -> list[str]:
    """Return case-folded Unicode words from the complete response."""

    return _WORD.findall(normalize_text(text))


def fenced_code_segments(text: str) -> tuple[CodeSegment, ...]:
    """Scan closed backtick/tilde fences once, preserving code and line numbers.

    Only a complete delimiter line closes a fence. Unterminated fences are
    rejected, so malformed Markdown cannot silently become an empty program.
    """
    from .exceptions import AnalysisError

    result: list[CodeSegment] = []
    delimiter: str | None = None
    width = 0
    language: str | None = None
    start_line = 1
    code: list[str] = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.strip(" \t\r\n")
        if delimiter is not None:
            if (
                stripped
                and stripped[0] == delimiter
                and len(stripped) >= width
                and not stripped.strip(delimiter)
            ):
                result.append(CodeSegment("".join(code), language, start_line))
                delimiter = None
                code = []
            else:
                code.append(line)
            continue
        candidate = line.lstrip(" \t")
        if not candidate or candidate[0] not in {"`", "~"}:
            continue
        marker = candidate[0]
        count = len(candidate) - len(candidate.lstrip(marker))
        if count < 3:
            continue
        info = candidate[count:].strip()
        if marker == "`" and "`" in info:
            continue
        delimiter, width = marker, count
        language = info.split(maxsplit=1)[0] if info else None
        start_line = number + 1
    if delimiter is not None:
        raise AnalysisError(f"Fence Markdown sem fechamento (linha {start_line - 1}).")
    return tuple(result)


def code_segments(text: str) -> tuple[CodeSegment, ...]:
    """Extract closed fences, or retain the complete unmodified source."""
    segments = fenced_code_segments(text)
    return segments or (CodeSegment(text),)


def _lexer(filename: str, language: str | None):
    try:
        if language:
            return get_lexer_by_name(language, stripnl=False, ensurenl=False)
        return get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


def _generic_tokens(code: str) -> list[str]:
    # Resolve closing quotes once, backwards. Repeated failed searches from
    # escaped quotes in an unterminated string must not rescan every suffix.
    # next_end[q] is the first legal closing q from the next character;
    # after_next[q] handles the two-character escape branch of the old grammar.
    quotes = ('"', "'", '`')
    next_end = [-1, -1, -1]
    after_next = next_end
    endings: dict[int, int] = {}
    for index in range(len(code) - 1, -1, -1):
        character = code[index]
        current = []
        for position, quote in enumerate(quotes):
            if character == quote:
                endings[index] = next_end[position]
                current.append(index)
            elif character == "\\":
                current.append(after_next[position] if index + 1 < len(code) and code[index + 1] != "\n" else -1)
            else:
                current.append(next_end[position])
        after_next, next_end = next_end, current
    tokens = []
    index = 0
    while index < len(code):
        end = endings.get(index, -1)
        if end >= 0:
            tokens.append(code[index:end + 1])
            index = end + 1
        else:
            match = _GENERIC_CODE_TOKEN.match(code, index)
            if match is None:
                index += 1
            else:
                tokens.append(match.group(0))
                index = match.end()
    return tokens


def _pygments_tokens(code: str, lexer) -> list[str]:
    result: list[str] = []
    adjacent_operator = False
    for token_type, value in lex(code, lexer):
        if token_type in Comment or not value:
            adjacent_operator = False
            continue
        if value.isspace():
            adjacent_operator = False
            continue
        if token_type in Operator:
            candidate = f"{result[-1]}{value}" if adjacent_operator else value
            if adjacent_operator and candidate in _COMPOUND_OPERATORS:
                result[-1] = candidate
            else:
                result.append(value)
            adjacent_operator = True
            continue
        adjacent_operator = False
        if token_type in Text:
            result.extend(_generic_tokens(value))
        else:
            result.append(value.replace("\r\n", "\n").replace("\r", "\n"))
    return result


def code_tokens(text: str, filename: str) -> list[str]:
    """Return ordered code lexemes, excluding comments and whitespace.

    Pygments supplies language lexers when the filename or Markdown fence has
    a known language. Unknown languages use the documented generic tokenizer;
    this fallback is deterministic but is not presented as a compiler lexer.
    Fragment boundary markers prevent matches from crossing code blocks.
    """

    segments = (
        fenced_code_segments(text)
        if Path(filename).suffix.lower() in {".md", ".markdown"}
        else (CodeSegment(text),)
    )
    result: list[str] = []
    for segment in segments:
        lexer = _lexer(filename, segment.language)
        tokens = (
            _pygments_tokens(segment.code, lexer)
            if lexer
            else _generic_tokens(segment.code)
        )
        if not tokens:
            continue
        if result:
            result.append("<CODE_FRAGMENT_BOUNDARY>")
        result.extend(tokens)
    return result


def uses_python_code(text: str, filename: str) -> bool:
    """Route .py source or explicitly Python Markdown fences to the parser."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".py":
        return True
    if suffix not in {".md", ".markdown"}:
        return False
    segments = fenced_code_segments(text)
    return bool(segments) and all(
        (segment.language or "").casefold() in {"py", "python"} for segment in segments
    )
