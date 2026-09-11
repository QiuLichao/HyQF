from __future__ import annotations

from dataclasses import dataclass

from hyqf.diagnostics.errors import NameResolutionError
from hyqf.frontend.spans import SourceSpan
from hyqf.semantics.effects import Effect
from hyqf.semantics.types import Type


@dataclass(frozen=True)
class Symbol:
    name: str
    type: Type
    kind: str
    span: SourceSpan | None = None
    effects: frozenset[Effect] = frozenset({Effect.PURE})


class SymbolTable:
    def __init__(self, parent: "SymbolTable | None" = None):
        self.parent = parent
        self._symbols: dict[str, Symbol] = {}

    def child(self) -> "SymbolTable":
        return SymbolTable(self)

    def define(self, symbol: Symbol) -> None:
        if symbol.name in self._symbols:
            raise NameResolutionError(f"symbol '{symbol.name}' is already defined in this scope", symbol.span)
        self._symbols[symbol.name] = symbol

    def rebind_local(self, symbol: Symbol) -> None:
        self._symbols[symbol.name] = symbol

    def resolve(self, name: str, span: SourceSpan | None = None) -> Symbol:
        if name in self._symbols:
            return self._symbols[name]
        if self.parent:
            return self.parent.resolve(name, span)
        raise NameResolutionError(f"unknown symbol '{name}'", span)

    def assign(self, symbol: Symbol) -> None:
        if symbol.name in self._symbols:
            self._symbols[symbol.name] = symbol
            return
        if self.parent:
            self.parent.assign(symbol)
            return
        raise NameResolutionError(f"unknown symbol '{symbol.name}'", symbol.span)
