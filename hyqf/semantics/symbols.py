from __future__ import annotations

from dataclasses import dataclass, replace

from hyqf.diagnostics.errors import NameResolutionError
from hyqf.frontend.spans import SourceSpan
from hyqf.semantics.effects import Effect
from hyqf.semantics.types import Type
from hyqf.semantics.model import SymbolId


@dataclass(frozen=True)
class Symbol:
    name: str
    type: Type
    kind: str
    span: SourceSpan | None = None
    effects: frozenset[Effect] = frozenset({Effect.PURE})
    symbol_id: SymbolId | None = None


class SymbolTable:
    def __init__(
        self,
        parent: "SymbolTable | None" = None,
        allocator: list[int] | None = None,
        catalog: dict[SymbolId, Symbol] | None = None,
    ):
        self.parent = parent
        self._symbols: dict[str, Symbol] = {}
        self._allocator = allocator if allocator is not None else [0]
        self._catalog = catalog if catalog is not None else {}

    def child(self) -> "SymbolTable":
        return SymbolTable(self, self._allocator, self._catalog)

    def _identified(self, symbol: Symbol, existing: Symbol | None = None) -> Symbol:
        symbol_id = symbol.symbol_id or (existing.symbol_id if existing else None)
        if symbol_id is None:
            symbol_id = SymbolId(self._allocator[0])
            self._allocator[0] += 1
        identified = replace(symbol, symbol_id=symbol_id)
        self._catalog[symbol_id] = identified
        return identified

    def define(self, symbol: Symbol) -> None:
        if symbol.name in self._symbols:
            raise NameResolutionError(f"symbol '{symbol.name}' is already defined in this scope", symbol.span)
        self._symbols[symbol.name] = self._identified(symbol)

    def rebind_local(self, symbol: Symbol) -> None:
        self._symbols[symbol.name] = self._identified(symbol)

    def resolve(self, name: str, span: SourceSpan | None = None) -> Symbol:
        if name in self._symbols:
            return self._symbols[name]
        if self.parent:
            return self.parent.resolve(name, span)
        raise NameResolutionError(f"unknown symbol '{name}'", span)

    def assign(self, symbol: Symbol) -> None:
        if symbol.name in self._symbols:
            self._symbols[symbol.name] = self._identified(symbol, self._symbols[symbol.name])
            return
        if self.parent:
            self.parent.assign(symbol)
            return
        raise NameResolutionError(f"unknown symbol '{symbol.name}'", symbol.span)

    def snapshot(self) -> dict[SymbolId, Symbol]:
        return {
            symbol.symbol_id: symbol
            for symbol in self._symbols.values()
            if symbol.symbol_id is not None
        }

    def catalog_snapshot(self) -> dict[SymbolId, Symbol]:
        return dict(self._catalog)
