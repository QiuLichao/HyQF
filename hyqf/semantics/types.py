from __future__ import annotations

from dataclasses import dataclass


class Type:
    def contains_quantum(self) -> bool:
        return False


@dataclass(frozen=True)
class PrimitiveType(Type):
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class QuantumType(Type):
    name: str

    def contains_quantum(self) -> bool:
        return True

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class QuditType(Type):
    dimension: int

    def contains_quantum(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"Qudit<{self.dimension}>"


@dataclass(frozen=True)
class ArrayType(Type):
    element: Type

    def contains_quantum(self) -> bool:
        return self.element.contains_quantum()

    def __str__(self) -> str:
        return f"Array<{self.element}>"


@dataclass(frozen=True)
class TupleType(Type):
    elements: tuple[Type, ...]

    def contains_quantum(self) -> bool:
        return any(t.contains_quantum() for t in self.elements)

    def __str__(self) -> str:
        return f"({', '.join(str(t) for t in self.elements)})"


@dataclass(frozen=True)
class FunctionType(Type):
    params: tuple[Type, ...]
    returns: Type
    is_quantum: bool

    def contains_quantum(self) -> bool:
        return self.is_quantum or any(t.contains_quantum() for t in self.params) or self.returns.contains_quantum()

    def __str__(self) -> str:
        prefix = "qfn" if self.is_quantum else "fn"
        return f"{prefix}({', '.join(str(t) for t in self.params)}) -> {self.returns}"


BOOL = PrimitiveType("Bool")
BIT = PrimitiveType("Bit")
INT = PrimitiveType("Int")
UINT = PrimitiveType("UInt")
FLOAT = PrimitiveType("Float")
COMPLEX = PrimitiveType("Complex")
VOID = PrimitiveType("Void")
QUBIT = QuantumType("Qubit")
QUMODE = QuantumType("Qumode")

CLASSICAL_TYPES = {
    "Bool": BOOL,
    "Bit": BIT,
    "Int": INT,
    "UInt": UINT,
    "Float": FLOAT,
    "Complex": COMPLEX,
    "Void": VOID,
}
QUANTUM_TYPES = {"Qubit": QUBIT, "Qumode": QUMODE}


def ARRAY(element: Type) -> ArrayType:
    return ArrayType(element)


def TUPLE(*elements: Type) -> TupleType:
    return TupleType(tuple(elements))


def is_numeric(t: Type) -> bool:
    return t in {INT, UINT, FLOAT, COMPLEX, BIT}


def promote_numeric(left: Type, right: Type) -> Type | None:
    if not is_numeric(left) or not is_numeric(right):
        return None
    if COMPLEX in {left, right}:
        return COMPLEX
    if FLOAT in {left, right}:
        return FLOAT
    if UINT in {left, right} and INT not in {left, right}:
        return UINT
    return INT


def ensure_supported_type(t: Type) -> None:
    from hyqf.diagnostics.errors import HyQFTypeError

    if isinstance(t, QuditType):
        raise HyQFTypeError("Qudit type syntax is reserved; Qudit semantics are deferred")
    if isinstance(t, ArrayType):
        ensure_supported_type(t.element)
    if isinstance(t, TupleType):
        for element in t.elements:
            ensure_supported_type(element)
    if isinstance(t, FunctionType):
        for param in t.params:
            ensure_supported_type(param)
        ensure_supported_type(t.returns)
