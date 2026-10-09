from __future__ import annotations

from dataclasses import dataclass

from hyqf.semantics.effects import Effect
from hyqf.semantics.model import CallableId
from hyqf.semantics.types import (
    BIT, BOOL, COMPLEX, FLOAT, INT, QUBIT, QUMODE, UINT, ArrayType,
    FunctionType, QuditType, QuantumType, TUPLE, TupleType, Type,
)


@dataclass(frozen=True, order=True)
class ValuePath:
    parts: tuple[int, ...]

    def __str__(self) -> str:
        return ".".join(str(part) for part in self.parts) or "root"


class ResultOrigin:
    pass


@dataclass(frozen=True)
class Successor(ResultOrigin):
    input_path: ValuePath


@dataclass(frozen=True)
class Fresh(ResultOrigin):
    allocation_class: str


@dataclass(frozen=True)
class ResourceFlowContract:
    quantum_inputs: tuple[ValuePath, ...]
    quantum_outputs: tuple[tuple[ValuePath, ResultOrigin], ...]

    def origin_for(self, output: ValuePath) -> ResultOrigin | None:
        return dict(self.quantum_outputs).get(output)


@dataclass(frozen=True)
class OperationSignature:
    name: str
    params: tuple[Type, ...]
    returns: Type
    effects: frozenset[Effect]
    kind: str = "qop"
    qualified_name: str | None = None
    version: str = "1.0"
    resource_flow: ResourceFlowContract | None = None
    symbolic_semantics_id: str | None = None
    flow_policy: str | None = None
    resource_conversion_id: str | None = None

    def __post_init__(self) -> None:
        if self.qualified_name is None:
            object.__setattr__(self, "qualified_name", f"user::{self.name}")
        inputs = parameter_quantum_paths(self.params)
        outputs = result_quantum_paths(self.returns)
        if self.resource_flow is None and not inputs and not outputs:
            object.__setattr__(self, "resource_flow", ResourceFlowContract((), ()))
        elif self.resource_flow is None and self.flow_policy == "positional":
            object.__setattr__(self, "resource_flow", positional_resource_flow(self.params, self.returns, self.effects))
        elif self.resource_flow is None:
            raise ValueError(
                "quantum operation requires an explicit resource_flow or flow_policy='positional'"
            )
        if self.flow_policy not in {None, "positional"}:
            raise ValueError(f"unknown qop resource-flow policy '{self.flow_policy}'")
        validate_resource_flow(self)

    @property
    def function_type(self) -> FunctionType:
        return FunctionType(self.params, self.returns, self.kind in {"qop", "qfunc"})

    @property
    def callable_id(self) -> CallableId:
        assert self.qualified_name is not None
        return CallableId(self.qualified_name, self.version)


class QopRegistry:
    def __init__(self):
        self._signatures: dict[str, OperationSignature] = {}
        self._qualified: dict[CallableId, OperationSignature] = {}

    def register(self, signature: OperationSignature) -> None:
        if signature.name in self._signatures:
            raise ValueError(f"qop '{signature.name}' is already registered")
        if signature.callable_id in self._qualified:
            raise ValueError(f"qop '{signature.callable_id}' is already registered")
        self._signatures[signature.name] = signature
        self._qualified[signature.callable_id] = signature

    def get(self, name: str) -> OperationSignature | None:
        return self._signatures.get(name)

    def get_by_id(self, callable_id: CallableId) -> OperationSignature | None:
        return self._qualified.get(callable_id)

    def signatures(self) -> dict[str, OperationSignature]:
        return dict(self._signatures)


def quantum_paths(value_type: Type, prefix: tuple[int, ...] = ()) -> tuple[ValuePath, ...]:
    if isinstance(value_type, (QuantumType, QuditType)):
        return (ValuePath(prefix),)
    if isinstance(value_type, ArrayType) and value_type.contains_quantum():
        return (ValuePath(prefix),)
    if isinstance(value_type, TupleType):
        return tuple(
            path
            for index, element in enumerate(value_type.elements)
            for path in quantum_paths(element, (*prefix, index))
        )
    return ()


def parameter_quantum_paths(params: tuple[Type, ...]) -> tuple[ValuePath, ...]:
    return tuple(
        path for index, parameter in enumerate(params)
        for path in quantum_paths(parameter, (index,))
    )


def result_quantum_paths(returns: Type) -> tuple[ValuePath, ...]:
    return quantum_paths(returns)


def positional_resource_flow(
    params: tuple[Type, ...], returns: Type, effects: frozenset[Effect]
) -> ResourceFlowContract:
    inputs = parameter_quantum_paths(params)
    outputs = result_quantum_paths(returns)
    origins: list[tuple[ValuePath, ResultOrigin]] = []
    for index, output in enumerate(outputs):
        if index < len(inputs):
            origins.append((output, Successor(inputs[index])))
        elif Effect.ALLOCATION in effects:
            origins.append((output, Fresh("quantum")))
        else:
            raise ValueError("qop produces a quantum resource without an input predecessor or allocation effect")
    return ResourceFlowContract(inputs, tuple(origins))


infer_resource_flow = positional_resource_flow


def validate_resource_flow(signature: OperationSignature) -> None:
    contract = signature.resource_flow
    assert contract is not None
    valid_inputs = {
        path for index, parameter in enumerate(signature.params)
        for path in quantum_paths(parameter, (index,))
    }
    valid_outputs = set(result_quantum_paths(signature.returns))
    if set(contract.quantum_inputs) != valid_inputs:
        raise ValueError("resource-flow quantum inputs do not match the operation signature")
    declared_outputs = [path for path, _ in contract.quantum_outputs]
    if set(declared_outputs) != valid_outputs or len(declared_outputs) != len(valid_outputs):
        raise ValueError("resource-flow quantum outputs do not match the operation signature")

    predecessors: list[ValuePath] = []
    for output_path, origin in contract.quantum_outputs:
        if isinstance(origin, Successor):
            if origin.input_path not in valid_inputs:
                raise ValueError("resource-flow successor references an invalid input path")
            input_type = _parameter_type_at_path(signature.params, origin.input_path)
            output_type = _type_at_path(signature.returns, output_path.parts)
            if input_type != output_type and signature.resource_conversion_id is None:
                raise ValueError(
                    "resource-kind conversion requires an explicit resource_conversion_id"
                )
            predecessors.append(origin.input_path)
        elif isinstance(origin, Fresh):
            if Effect.ALLOCATION not in signature.effects:
                raise ValueError("fresh quantum output requires the allocation effect")
        else:
            raise ValueError("unknown resource-flow result origin")
    if len(predecessors) != len(set(predecessors)):
        raise ValueError("two quantum outputs may not claim the same predecessor")
    if Effect.UNITARY in signature.effects and Effect.MEASUREMENT not in signature.effects:
        if set(predecessors) != valid_inputs or len(valid_outputs) != len(valid_inputs):
            raise ValueError("unitary qop resource flow must be bijective")


def _parameter_type_at_path(params: tuple[Type, ...], path: ValuePath) -> Type:
    if not path.parts or path.parts[0] >= len(params):
        raise ValueError("resource-flow input path has no parameter root")
    return _type_at_path(params[path.parts[0]], path.parts[1:])


def _type_at_path(value_type: Type, parts: tuple[int, ...]) -> Type:
    current = value_type
    for part in parts:
        if not isinstance(current, TupleType) or part >= len(current.elements):
            raise ValueError("resource-flow path does not match its aggregate type")
        current = current.elements[part]
    return current


def _standard(name: str, params: tuple[Type, ...], returns: Type, effects: frozenset[Effect]) -> OperationSignature:
    return OperationSignature(
        name, params, returns, effects,
        qualified_name=f"std::{name}",
        symbolic_semantics_id=f"hyqf.std.{name.lower()}.v1",
        flow_policy="positional",
    )


def standard_registry() -> QopRegistry:
    registry = QopRegistry()
    for sig in [
        _standard("H", (QUBIT,), QUBIT, frozenset({Effect.UNITARY})),
        _standard("X", (QUBIT,), QUBIT, frozenset({Effect.UNITARY})),
        _standard("RZ", (FLOAT, QUBIT), QUBIT, frozenset({Effect.UNITARY})),
        _standard("CX", (QUBIT, QUBIT), TUPLE(QUBIT, QUBIT), frozenset({Effect.UNITARY})),
        _standard("D", (COMPLEX, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        _standard("R", (FLOAT, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        _standard("S", (COMPLEX, QUMODE), QUMODE, frozenset({Effect.UNITARY})),
        _standard("BS", (FLOAT, FLOAT, QUMODE, QUMODE), TUPLE(QUMODE, QUMODE), frozenset({Effect.UNITARY})),
        _standard("CD", (COMPLEX, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        _standard("CR", (FLOAT, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        _standard("JC", (FLOAT, FLOAT, QUBIT, QUMODE), TUPLE(QUBIT, QUMODE), frozenset({Effect.UNITARY})),
        _standard("measure_z", (QUBIT,), TUPLE(QUBIT, BIT), frozenset({Effect.MEASUREMENT})),
        _standard("measure_n", (QUMODE,), TUPLE(QUMODE, UINT), frozenset({Effect.MEASUREMENT})),
        _standard("measure_x", (QUMODE,), TUPLE(QUMODE, FLOAT), frozenset({Effect.MEASUREMENT})),
    ]:
        registry.register(sig)
    return registry


def standard_classical_functions() -> dict[str, OperationSignature]:
    return {
        "bool": OperationSignature("bool", (BIT,), BOOL, frozenset({Effect.PURE}), "func", "std::bool"),
        "int": OperationSignature("int", (BIT,), INT, frozenset({Effect.PURE}), "func", "std::int"),
        "float": OperationSignature("float", (INT,), FLOAT, frozenset({Effect.PURE}), "func", "std::float"),
    }
