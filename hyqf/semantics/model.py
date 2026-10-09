from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TYPE_CHECKING

from hyqf.frontend.spans import SourceSpan
from hyqf.semantics.effects import Effect
from hyqf.semantics.types import Type

if TYPE_CHECKING:
    from hyqf.frontend.ast import NodeId, Program
    from hyqf.semantics.qops import OperationSignature, ResourceFlowContract, ValuePath
    from hyqf.semantics.symbols import Symbol


@dataclass(frozen=True, order=True)
class SymbolId:
    value: int


@dataclass(frozen=True, order=True)
class BindingId:
    value: int


@dataclass(frozen=True, order=True)
class ResourceId:
    value: int


@dataclass(frozen=True, order=True)
class CallableId:
    qualified_name: str
    version: str = "1.0"

    def __str__(self) -> str:
        return f"{self.qualified_name}@{self.version}"


class FlowExit(str, Enum):
    FALLTHROUGH = "fallthrough"
    RETURN = "return"
    BREAK = "break"
    CONTINUE = "continue"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class FlowSummary:
    exits: frozenset[FlowExit]

    @property
    def falls_through(self) -> bool:
        return FlowExit.FALLTHROUGH in self.exits


@dataclass(frozen=True)
class BindingFact:
    binding_id: BindingId
    name: str
    type: Type
    declaration: NodeId | None
    span: SourceSpan
    initial_resources: tuple[ResourceId, ...]
    synthetic: bool = False


@dataclass(frozen=True)
class ResourceUse:
    node_id: NodeId
    binding_id: BindingId
    resources: tuple[ResourceId, ...]
    kind: str
    index: int | None = None


@dataclass(frozen=True)
class ResourceTransfer:
    node_id: NodeId
    inputs: tuple[ResourceId, ...]
    outputs: tuple[ResourceId, ...]
    successors: tuple[tuple[ResourceId, ResourceId], ...]
    terminal_inputs: tuple[ResourceId, ...]
    fresh_outputs: tuple[ResourceId, ...] = ()
    authoritative: bool = True


@dataclass(frozen=True)
class BranchJoin:
    node_id: NodeId
    name: str
    incoming_bindings: tuple[BindingId, BindingId]
    incoming_resources: tuple[tuple[ResourceId, ...], tuple[ResourceId, ...]]
    output_binding: BindingId
    output_resources: tuple[ResourceId, ...]
    kind: str


@dataclass(frozen=True)
class ArrayShape:
    extent: int | None
    element_resources: tuple[tuple[ResourceId, ...], ...] = ()

    @property
    def is_static(self) -> bool:
        return self.extent is not None


@dataclass(frozen=True)
class ArrayOwnershipEvent:
    node_id: NodeId
    binding_id: BindingId
    kind: str
    index: int | None = None


@dataclass(frozen=True)
class ArrayUpdateFact:
    target_nodes: tuple[NodeId, ...]
    input_binding: BindingId
    output_binding: BindingId
    indices: tuple[int, ...]
    input_elements: tuple[tuple[ResourceId, ...], ...]
    output_elements: tuple[tuple[ResourceId, ...], ...]
    replacement_resources: tuple[tuple[ResourceId, ...], ...]


@dataclass(frozen=True)
class FunctionResourceSummary:
    callable_id: CallableId
    resource_flow: "ResourceFlowContract | None"
    terminal_inputs: tuple["ValuePath", ...]
    complete: bool
    reason: str | None = None


@dataclass(frozen=True)
class OwnershipFacts:
    bindings: Mapping[BindingId, BindingFact]
    declaration_bindings: Mapping[NodeId, BindingId]
    use_bindings: Mapping[NodeId, BindingId]
    uses: Mapping[NodeId, ResourceUse]
    transfers: Mapping[NodeId, ResourceTransfer]
    branch_joins: Mapping[NodeId, tuple[BranchJoin, ...]]
    array_shapes: Mapping[BindingId, ArrayShape]
    array_events: Mapping[NodeId, ArrayOwnershipEvent]
    array_updates: Mapping[NodeId, ArrayUpdateFact]
    exits: Mapping[NodeId, tuple[ResourceId, ...]]
    function_summaries: Mapping[CallableId, FunctionResourceSummary]


@dataclass(frozen=True)
class SemanticModel:
    program: Program
    global_symbols: Mapping[SymbolId, "Symbol"]
    symbols: Mapping[SymbolId, "Symbol"]
    node_types: Mapping[NodeId, Type]
    resolved_symbols: Mapping[NodeId, SymbolId]
    binding_versions: Mapping[NodeId, BindingId]
    call_targets: Mapping[NodeId, CallableId]
    callable_signatures: Mapping[CallableId, "OperationSignature"]
    function_summaries: Mapping[CallableId, FunctionResourceSummary]
    function_effects: Mapping[str, frozenset[Effect]]
    flow_summaries: Mapping[NodeId, FlowSummary]
    ownership: OwnershipFacts
    array_shapes: Mapping[BindingId, ArrayShape]
    array_updates: Mapping[NodeId, ArrayUpdateFact]
    branch_joins: Mapping[NodeId, tuple[BranchJoin, ...]]

    def validate(self) -> None:
        from hyqf.semantics.validation import validate_semantic_model

        validate_semantic_model(self)


def frozen_map(values: Mapping) -> Mapping:
    return MappingProxyType(dict(values))
