from __future__ import annotations

from dataclasses import dataclass

from hyqf.diagnostics.errors import ParseError
from hyqf.frontend.spans import SourceSpan


KEYWORDS = {
    "module",
    "import",
    "as",
    "func",
    "qfunc",
    "qop",
    "return",
    "let",
    "var",
    "const",
    "symbol",
    "where",
    "if",
    "else",
    "while",
    "for",
    "in",
    "break",
    "continue",
    "new",
    "adjoint",
    "controlled",
    "compose",
    "repeat",
    "verify",
    "equiv",
    "modulo",
    "exact",
    "global_phase",
    "true",
    "false",
    "Bool",
    "Bit",
    "Int",
    "UInt",
    "Float",
    "Complex",
    "Void",
    "Qubit",
    "Qumode",
    "Qudit",
    "Array",
    "fn",
    "qfn",
    "effects",
    "unitary",
    "measurement",
    "allocation",
    "control",
    "pure",
    "yield",
}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    span: SourceSpan


class Lexer:
    MULTI = ("->", "==", "!=", "<=", ">=", "&&", "||", "**")
    SINGLE = set("{}()[];:,.<>+-*/%=&|^!")

    def __init__(self, source: str, file: str = "<string>"):
        self.source = source
        self.file = file
        self.i = 0
        self.line = 1
        self.column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while not self._at_end():
            c = self._peek()
            if c.isspace():
                self._advance()
                continue
            if c == "/" and self._peek(1) == "/":
                self._skip_line_comment()
                continue
            if c == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            start = self._mark()
            if c.isalpha() or c == "_":
                value = self._read_identifier()
                kind = "KW" if value in KEYWORDS else "IDENT"
                tokens.append(Token(kind, value, self._span_from(start)))
                continue
            if c.isdigit():
                tokens.append(self._read_number(start))
                continue
            matched = False
            for op in self.MULTI:
                if self.source.startswith(op, self.i):
                    self._advance(len(op))
                    tokens.append(Token("SYM", op, self._span_from(start)))
                    matched = True
                    break
            if matched:
                continue
            if c in self.SINGLE:
                self._advance()
                tokens.append(Token("SYM", c, self._span_from(start)))
                continue
            raise ParseError(f"unexpected character '{c}'", SourceSpan(self.file, self.line, self.column, self.line, self.column + 1))
        tokens.append(Token("EOF", "", SourceSpan(self.file, self.line, self.column, self.line, self.column)))
        return tokens

    def _read_identifier(self) -> str:
        start = self.i
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        return self.source[start:self.i]

    def _read_number(self, start: tuple[int, int, int]) -> Token:
        begin = self.i
        while not self._at_end() and self._peek().isdigit():
            self._advance()
        kind = "INT"
        if not self._at_end() and self._peek() == ".":
            kind = "FLOAT"
            self._advance()
            while not self._at_end() and self._peek().isdigit():
                self._advance()
        if not self._at_end() and self._peek() in {"e", "E"}:
            kind = "FLOAT"
            self._advance()
            if not self._at_end() and self._peek() in {"+", "-"}:
                self._advance()
            if self._at_end() or not self._peek().isdigit():
                raise ParseError("malformed exponent", self._span_from(start))
            while not self._at_end() and self._peek().isdigit():
                self._advance()
        if not self._at_end() and self._peek() == "j":
            kind = "COMPLEX"
            self._advance()
        return Token(kind, self.source[begin:self.i], self._span_from(start))

    def _skip_line_comment(self) -> None:
        while not self._at_end() and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        start = self._mark()
        self._advance(2)
        while not self._at_end():
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance(2)
                return
            self._advance()
        raise ParseError("unterminated block comment", self._span_from(start))

    def _mark(self) -> tuple[int, int, int]:
        return (self.i, self.line, self.column)

    def _span_from(self, start: tuple[int, int, int]) -> SourceSpan:
        return SourceSpan(self.file, start[1], start[2], self.line, self.column)

    def _peek(self, offset: int = 0) -> str:
        idx = self.i + offset
        return "" if idx >= len(self.source) else self.source[idx]

    def _advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self._at_end():
                return
            c = self.source[self.i]
            self.i += 1
            if c == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1

    def _at_end(self) -> bool:
        return self.i >= len(self.source)
