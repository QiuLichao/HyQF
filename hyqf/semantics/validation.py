from __future__ import annotations

from dataclasses import fields

from hyqf.diagnostics.errors import SemanticModelError
from hyqf.frontend import ast
from hyqf.semantics.model import CallableId, SemanticModel
from hyqf.semantics.qops import Successor, parameter_quantum_paths, result_quantum_paths
from hyqf.semantics.types import FunctionType


def _fail(message: str) -> None:
    raise SemanticModelError(message)


def _node_ids(root: ast.Node) -> set[ast.NodeId]:
    result: set[ast.NodeId] = set()

    def visit(node: ast.Node) -> None:
        if node.node_id == ast.UNASSIGNED_NODE_ID:
            _fail(f"{type(node).__name__} has no NodeId")
        if node.node_id in result:
            _fail(f"duplicate NodeId {node.node_id.value}")
        result.add(node.node_id)
        for field in fields(node):
            if field.name in {"span", "node_id"}:
                continue
            value = getattr(node, field.name)
            if isinstance(value, ast.Node):
                visit(value)
            elif isinstance(value, (list, tuple)):
                for element in value:
                    if isinstance(element, ast.Node):
                        visit(element)

    visit(root)
    return result


def validate_semantic_model(model: SemanticModel) -> None:
    """Reject semantic facts that cannot be consumed safely by lowering."""

    nodes = _node_ids(model.program)
    node_maps = {
        "node_types": model.node_types,
        "resolved_symbols": model.resolved_symbols,
        "binding_versions": model.binding_versions,
        "call_targets": model.call_targets,
        "flow_summaries": model.flow_summaries,
    }
    for name, mapping in node_maps.items():
        unknown = set(mapping) - nodes
        if unknown:
            _fail(f"{name} contains unknown NodeId {min(item.value for item in unknown)}")

    missing_symbols = set(model.resolved_symbols.values()) - set(model.symbols)
    if missing_symbols:
        _fail(f"resolved symbol {min(item.value for item in missing_symbols)} is absent from symbol catalog")
    for symbol_id, symbol in model.symbols.items():
        if symbol.symbol_id != symbol_id:
            _fail(f"symbol catalog key {symbol_id.value} does not match stored SymbolId")
    for symbol_id, symbol in model.global_symbols.items():
        if model.symbols.get(symbol_id) != symbol:
            _fail(f"global symbol {symbol_id.value} is absent or differs in symbol catalog")

    ownership = model.ownership
    ownership_node_maps = {
        "ownership uses": ownership.uses,
        "ownership transfers": ownership.transfers,
        "ownership branch joins": ownership.branch_joins,
        "ownership array events": ownership.array_events,
        "ownership array updates": ownership.array_updates,
        "ownership exits": ownership.exits,
    }
    for name, mapping in ownership_node_maps.items():
        unknown = set(mapping) - nodes
        if unknown:
            _fail(f"{name} contains unknown NodeId {min(item.value for item in unknown)}")

    missing_bindings = set(model.binding_versions.values()) - set(ownership.bindings)
    if missing_bindings:
        _fail(f"binding version {min(item.value for item in missing_bindings)} is undefined")
    if dict(model.function_summaries) != dict(ownership.function_summaries):
        _fail("semantic and ownership function summaries differ")
    if dict(model.array_shapes) != dict(ownership.array_shapes):
        _fail("semantic and ownership array shapes differ")
    if dict(model.array_updates) != dict(ownership.array_updates):
        _fail("semantic and ownership array updates differ")
    if dict(model.branch_joins) != dict(ownership.branch_joins):
        _fail("semantic and ownership branch joins differ")
    referenced_bindings = (
        set(ownership.declaration_bindings.values())
        | set(ownership.use_bindings.values())
        | {use.binding_id for use in ownership.uses.values()}
        | set(ownership.array_shapes)
    )
    if not referenced_bindings <= set(ownership.bindings):
        _fail("ownership facts reference an unknown binding")

    known_callables = set(model.callable_signatures) | set(model.function_summaries)
    missing_callables = set(model.call_targets.values()) - known_callables
    if missing_callables:
        _fail(f"call target {min(map(str, missing_callables))} has no callable definition or summary")
    for callable_id, signature in model.callable_signatures.items():
        if callable_id != signature.callable_id:
            _fail(f"callable signature key {callable_id} does not match its callable ID")

    source_function_names = {
        declaration.name
        for declaration in model.program.declarations
        if isinstance(declaration, ast.FuncDecl)
    }
    source_functions = {
        CallableId(f"source::{symbol.name}"): symbol
        for symbol in model.global_symbols.values()
        if symbol.name in source_function_names and symbol.kind in {"func", "qfunc"}
    }
    if set(model.function_summaries) != set(source_functions):
        _fail("function summaries do not exactly cover source functions")
    expected_effect_names = {symbol.name for symbol in source_functions.values()}
    if set(model.function_effects) != expected_effect_names:
        _fail("function effects do not exactly cover source functions")
    for symbol in source_functions.values():
        if model.function_effects[symbol.name] != symbol.effects:
            _fail(f"function effect summary for {symbol.name} differs from its symbol")

    for transfer in ownership.transfers.values():
        inputs = set(transfer.inputs)
        outputs = set(transfer.outputs)
        sources = [source for source, _ in transfer.successors]
        targets = [target for _, target in transfer.successors]
        if not set(sources) <= inputs or not set(transfer.terminal_inputs) <= inputs:
            _fail(f"transfer at NodeId {transfer.node_id.value} references an unknown input")
        if not set(targets) <= outputs or not set(transfer.fresh_outputs) <= outputs:
            _fail(f"transfer at NodeId {transfer.node_id.value} references an unknown output")
        if len(sources) != len(set(sources)) or len(targets) != len(set(targets)):
            _fail(f"transfer at NodeId {transfer.node_id.value} is not linear")
        if set(sources) & set(transfer.terminal_inputs):
            _fail(f"transfer at NodeId {transfer.node_id.value} both continues and terminates an input")
        if set(sources) | set(transfer.terminal_inputs) != inputs:
            _fail(f"transfer at NodeId {transfer.node_id.value} does not classify every input")
        if set(targets) & set(transfer.fresh_outputs):
            _fail(f"transfer at NodeId {transfer.node_id.value} gives an output two origins")
        if set(targets) | set(transfer.fresh_outputs) != outputs:
            _fail(f"transfer at NodeId {transfer.node_id.value} does not classify every output")

    for callable_id, summary in model.function_summaries.items():
        if callable_id != summary.callable_id:
            _fail(f"function summary key {callable_id} does not match its callable ID")
        if summary.complete != (summary.resource_flow is not None):
            _fail(f"function summary {callable_id} has inconsistent completeness")
        if summary.complete and summary.reason is not None:
            _fail(f"complete function summary {callable_id} carries a failure reason")
        if not summary.complete and summary.reason is None:
            _fail(f"incomplete function summary {callable_id} has no reason")
        if summary.complete:
            assert summary.resource_flow is not None
            symbol = source_functions[callable_id]
            if not isinstance(symbol.type, FunctionType):
                _fail(f"function symbol {callable_id} has a non-function type")
            expected_inputs = parameter_quantum_paths(symbol.type.params)
            expected_outputs = result_quantum_paths(symbol.type.returns)
            if set(summary.resource_flow.quantum_inputs) != set(expected_inputs):
                _fail(f"function summary {callable_id} has incorrect input paths")
            actual_outputs = [path for path, _ in summary.resource_flow.quantum_outputs]
            if set(actual_outputs) != set(expected_outputs) or len(actual_outputs) != len(expected_outputs):
                _fail(f"function summary {callable_id} has incorrect output paths")
            inputs = set(summary.resource_flow.quantum_inputs)
            output_paths = [path for path, _ in summary.resource_flow.quantum_outputs]
            if len(output_paths) != len(set(output_paths)):
                _fail(f"function summary {callable_id} repeats an output path")
            predecessor_paths = [
                origin.input_path
                for _, origin in summary.resource_flow.quantum_outputs
                if isinstance(origin, Successor)
            ]
            if not set(predecessor_paths) <= inputs:
                _fail(f"function summary {callable_id} references an unknown input path")
            if len(predecessor_paths) != len(set(predecessor_paths)):
                _fail(f"function summary {callable_id} is not linear")
            if set(predecessor_paths) | set(summary.terminal_inputs) != inputs:
                _fail(f"function summary {callable_id} does not classify every input path")
            if set(predecessor_paths) & set(summary.terminal_inputs):
                _fail(f"function summary {callable_id} both continues and terminates an input path")

    for update in ownership.array_updates.values():
        if update.input_binding not in ownership.bindings or update.output_binding not in ownership.bindings:
            _fail("array update references an unknown binding")
        if len(update.indices) != len(set(update.indices)):
            _fail("array update contains duplicate indices")
        if len(update.output_elements) != len(update.input_elements):
            _fail("array update changes the array extent")

    for joins in ownership.branch_joins.values():
        for join in joins:
            binding_ids = {*join.incoming_bindings, join.output_binding}
            if not binding_ids <= set(ownership.bindings):
                _fail("branch join references an unknown binding")
