from __future__ import annotations

from dataclasses import dataclass

from hyqf.semantics.effects import Effect
from hyqf.semantics.types import BIT, BOOL, COMPLEX, FLOAT, INT, QUBIT, QUMODE, UINT, FunctionType, TUPLE, Type


@dataclass(frozen=True)
class OperationSignature:
    name: str
    params: tuple[Type, ...]
    returns: Type
    effects: frozenset[Effect]
    kind: str = "qop"

    @property
    def function_type(self) -> FunctionType:
        return FunctionType(self.params, self.returns, self.kind in {"qop", "qfunc"})


class QopRegistry:
    def __init__(self):
        self._signatures: dict[str, OperationSignature] = {}

    def register(self, signature: OperationSignature) -> None:
        self._signatures[signature.name] = signature

    def get(self, name: str) -> OperationSignature | None:
        return self._signatures.get(name)

    def signatures(self) -> dict[str, OperationSignature]:
        return dict(self._signatures)


def standard_registry() -> QopRegistry:
    registry = QopRegistry()
    for sig in [
        OperationSignature("H", (QUBIT,), QUBIT, frozenset({Effect.UNITARY})),
        OperationSignature("X", (QUBIT,), QUBIT, frozenset({Effect.UNITARY})),
        OperationSignature("RZ", (FLOAT, QUBIT), QUBIT, frozenset({Effect.UNITARY})),
        OperationSignature("CX", (QUBIT, QUBIT), TUPLE(QUBIT, QUBIT), frozenset({Effect.UNITARY})),
        OperationSignature("D", (COMPLEX, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        OperationSignature("R", (FLOAT, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        OperationSignature("S", (COMPLEX, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        OperationSignature("BS", (FLOAT, FLOAT, QUMODE, QUMODE), TUPLE(QUMODE, QUMODE), frozenset({Effect.UNITARY})),
        OperationSignature("CD", (COMPLEX, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        OperationSignature("CR", (FLOAT, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        OperationSignature("JC", (FLOAT, FLOAT, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        OperationSignature("measure_z", (QUBIT,), TUPLE(QUBIT, BIT), frozenset({Effect.MEASUREMENT})),
        OperationSignature("measure_n", (QUMODE,), TUPLE(QUMODE, UINT), frozenset({Effect.MEASUREMENT})),
        OperationSignature("measure_x", (QUMODE,), TUPLE(QUMODE, FLOAT), frozenset({Effect.MEASUREMENT})),
    ]:
        registry.register(sig)
    return registry


def standard_classical_functions() -> dict[str, OperationSignature]:
    return {
        "bool": OperationSignature("bool", (BIT,), BOOL, frozenset({Effect.PURE}), "func"),
        "int": OperationSignature("int", (BIT,), INT, frozenset({Effect.PURE}), "func"),
        "float": OperationSignature("float", (INT,), FLOAT, frozenset({Effect.PURE}), "func"),
    }
