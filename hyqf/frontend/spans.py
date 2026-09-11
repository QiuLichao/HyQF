from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int

    def merge(self, other: "SourceSpan") -> "SourceSpan":
        return SourceSpan(self.file, self.line, self.column, other.end_line, other.end_column)

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}-{self.end_line}:{self.end_column}"


def synthetic_span(file: str = "<synthetic>") -> SourceSpan:
    return SourceSpan(file, 1, 1, 1, 1)
