from dataclasses import fields, replace
from decimal import Decimal

import pytest

from hyqf.frontend import ast
from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import format_ast
from hyqf.diagnostics.errors import SemanticModelError
from hyqf.semantics.model import CallableId, FlowExit, SymbolId, frozen_map
from hyqf.semantics.qops import (
    Fresh,
    OperationSignature,
    QopRegistry,
    ResourceFlowContract,
    Successor,
    ValuePath,
    standard_registry,
)
from hyqf.semantics.typecheck import TypeChecker
from hyqf.semantics.types import FunctionType, QUBIT, QUMODE, TUPLE
from hyqf.semantics.effects import Effect
from tests.helpers import FIXTURES


SEMANTIC_FIXTURES = FIXTURES / "semantic"


def walk(node: ast.Node):
    yield node
    for item in fields(node):
        value = getattr(node, item.name)
        if isinstance(value, ast.Node):
            yield from walk(value)
        elif isinstance(value, (list, tuple)):
            for element in value:
                if isinstance(element, ast.Node):
                    yield from walk(element)


def load(name: str) -> ast.Program:
    path = SEMANTIC_FIXTURES / name
    return parse_source(path.read_text(encoding="utf-8"), file=path.name)


def test_node_ids_are_unique_deterministic_and_visible_in_pretty_output():
    first = load("model_pipeline.hyqf")
    second = load("model_pipeline.hyqf")
    first_ids = [node.node_id.value for node in walk(first)]
    second_ids = [node.node_id.value for node in walk(second)]

    assert first_ids == list(range(len(first_ids)))
    assert second_ids == first_ids
    rendered = format_ast(first, show_node_ids=True)
    assert "Program [node=0]" in rendered
    assert f"CallExpr [node={first.declarations[0].body.statements[0].then_block.statements[0].value.node_id.value}]" in rendered


def test_numeric_literals_preserve_lexemes_and_exact_values():
    path = FIXTURES / "parser" / "valid" / "literals_comments.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    floating = program.declarations[2].value
    imaginary = program.declarations[3].value

    assert floating.lexeme == "1e-5"
    assert floating.value == Decimal("0.00001")
    assert imaginary.lexeme == "2.5j"
    assert imaginary.value.real == Decimal(0)
    assert imaginary.value.imaginary == Decimal("2.5")


def test_checked_module_exposes_authoritative_expression_and_call_facts():
    program = load("model_pipeline.hyqf")
    checked = TypeChecker().check(program)
    function = program.declarations[0]
    call = function.body.statements[0].then_block.statements[0].value

    assert checked.node_types[call.node_id] == QUBIT
    assert isinstance(checked.node_types[call.callee.node_id], FunctionType)
    assert checked.call_targets[call.node_id].qualified_name == "std::H"
    assert checked.call_targets[call.node_id] in checked.semantic_model.callable_signatures
    assert call.callee.node_id in checked.resolved_symbols
    assert call.arguments[0].node_id in checked.semantic_model.binding_versions
    expression_ids = {node.node_id for node in walk(program) if isinstance(node, ast.Expr)}
    assert expression_ids <= checked.node_types.keys()
    with pytest.raises(TypeError):
        checked.node_types[call.node_id] = QUBIT


def test_flow_summary_tracks_all_path_return_and_unreachable_statements():
    program = load("all_paths_return.hyqf")
    checked = TypeChecker().check(program)
    function = program.declarations[0]
    if_statement, unreachable = function.body.statements

    assert checked.semantic_model.flow_summaries[if_statement.node_id].exits == frozenset({FlowExit.RETURN})
    assert checked.semantic_model.flow_summaries[unreachable.node_id].exits == frozenset({FlowExit.UNREACHABLE})
    assert checked.semantic_model.flow_summaries[function.body.node_id].exits == frozenset({FlowExit.RETURN})


def test_ownership_facts_record_moves_transfers_and_branch_join():
    program = load("model_pipeline.hyqf")
    checked = TypeChecker().check(program)
    function = program.declarations[0]
    if_statement = function.body.statements[0]
    call = if_statement.then_block.statements[0].value
    argument = call.arguments[0]
    transfer = checked.ownership.transfers[call.node_id]
    join = checked.ownership.branch_joins[if_statement.node_id][0]

    assert checked.ownership.uses[argument.node_id].resources == transfer.inputs
    assert transfer.successors == ((transfer.inputs[0], transfer.outputs[0]),)
    assert transfer.terminal_inputs == ()
    assert join.name == "q"
    assert join.incoming_resources[0] == transfer.outputs
    assert join.incoming_resources[1] == transfer.inputs
    assert join.output_resources not in join.incoming_resources


def test_static_array_shape_and_element_events_survive_ownership_checking():
    program = load("static_array.hyqf")
    checked = TypeChecker().check(program)
    shapes = list(checked.semantic_model.array_shapes.values())
    events = list(checked.ownership.array_events.values())

    assert [shape.extent for shape in shapes] == [2, 2]
    assert all(len(shape.element_resources) == 2 for shape in shapes)
    assert [(event.kind, event.index) for event in events] == [("take", 0), ("take", 1)]


def test_dynamic_quantum_array_shape_is_explicitly_unknown():
    program = load("dynamic_array.hyqf")
    checked = TypeChecker().check(program)

    assert [shape.extent for shape in checked.semantic_model.array_shapes.values()] == [None]


def test_single_element_update_records_complete_array_lineage():
    path = FIXTURES / "ownership" / "valid" / "single_array_element_update.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    target = program.declarations[0].body.statements[1].pattern
    update = checked.semantic_model.array_updates[target.node_id]

    assert update.indices == (0,)
    assert update.input_binding != update.output_binding
    assert update.output_elements[0] == update.replacement_resources[0]
    assert update.output_elements[1] == update.input_elements[1]
    assert checked.semantic_model.binding_versions[target.node_id] == update.output_binding


def test_tuple_element_update_is_one_atomic_array_update_fact():
    path = FIXTURES / "ownership" / "valid" / "tuple_array_element_update.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    targets = program.declarations[0].body.statements[1].pattern.elements
    first = checked.semantic_model.array_updates[targets[0].node_id]
    second = checked.semantic_model.array_updates[targets[1].node_id]

    assert first == second
    assert first.target_nodes == (targets[0].node_id, targets[1].node_id)
    assert first.indices == (0, 1)
    assert first.output_elements == first.replacement_resources
    assert checked.resolved_symbols[targets[0].node_id] == checked.resolved_symbols[targets[1].node_id]


def test_array_update_can_share_a_tuple_pattern_with_classical_measurement_result():
    path = FIXTURES / "ownership" / "valid" / "measurement_array_element_update.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    pattern = program.declarations[0].body.statements[1].pattern
    array_target, bit_target = pattern.elements

    assert array_target.node_id in checked.semantic_model.array_updates
    assert array_target.node_id in checked.semantic_model.binding_versions
    assert bit_target.node_id in checked.semantic_model.binding_versions


def test_terminal_qop_call_records_consumed_resource_without_successor():
    path = FIXTURES / "ownership" / "valid" / "terminal_consume.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    call = program.declarations[1].body.statements[0].expression
    transfer = checked.ownership.transfers[call.node_id]

    assert transfer.outputs == ()
    assert transfer.successors == ()
    assert transfer.terminal_inputs == transfer.inputs


def test_nested_qop_calls_form_an_unbroken_resource_transfer_chain():
    program = load("nested_qop_calls.hyqf")
    checked = TypeChecker().check(program)
    outer = program.declarations[0].body.statements[0].value
    inner = outer.arguments[0]
    inner_transfer = checked.ownership.transfers[inner.node_id]
    outer_transfer = checked.ownership.transfers[outer.node_id]

    assert inner_transfer.outputs == outer_transfer.inputs
    assert inner_transfer.successors[0][1] == outer_transfer.successors[0][0]


def test_standard_qops_have_stable_symbolic_ids_and_resource_flow():
    registry = standard_registry()
    h = registry.get("H")
    cx = registry.get("CX")
    measure = registry.get("measure_z")

    assert h is not None and h.callable_id.qualified_name == "std::H"
    assert h.symbolic_semantics_id == "hyqf.std.h.v1"
    assert h.resource_flow.quantum_outputs == ((ValuePath(()), Successor(ValuePath((0,)))),)
    assert cx is not None
    assert cx.resource_flow.quantum_outputs == (
        (ValuePath((0,)), Successor(ValuePath((0,)))),
        (ValuePath((1,)), Successor(ValuePath((1,)))),
    )
    assert measure is not None
    assert measure.resource_flow.quantum_outputs == ((ValuePath((0,)), Successor(ValuePath((0,)))),)


def test_standard_classical_call_target_uses_registered_identity():
    path = FIXTURES / "typing" / "valid" / "classical_casts.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    calls = [node for node in walk(program) if isinstance(node, ast.CallExpr)]
    int_call = next(call for call in calls if call.callee.name == "int")

    assert checked.call_targets[int_call.node_id].qualified_name == "std::int"
    assert checked.call_targets[int_call.node_id] in checked.semantic_model.callable_signatures


def test_qop_registry_rejects_duplicate_predecessors_and_undeclared_fresh_outputs():
    duplicate = ResourceFlowContract(
        (ValuePath((0,)), ValuePath((1,))),
        (
            (ValuePath((0,)), Successor(ValuePath((0,)))),
            (ValuePath((1,)), Successor(ValuePath((0,)))),
        ),
    )
    with pytest.raises(ValueError, match="same predecessor"):
        OperationSignature(
            "Bad",
            (QUBIT, QUBIT),
            TUPLE(QUBIT, QUBIT),
            frozenset({Effect.UNITARY}),
            resource_flow=duplicate,
        )

    fresh = ResourceFlowContract((), ((ValuePath(()), Fresh("qubit")),))
    with pytest.raises(ValueError, match="allocation effect"):
        OperationSignature("BadAlloc", (), QUBIT, frozenset({Effect.PURE}), resource_flow=fresh)

    registry = QopRegistry()
    signature = OperationSignature(
        "Identity",
        (QUBIT,),
        QUBIT,
        frozenset({Effect.UNITARY}),
        flow_policy="positional",
    )
    registry.register(signature)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(signature)


def test_qop_registry_requires_explicit_resource_conversion_semantics():
    conversion = ResourceFlowContract(
        (ValuePath((0,)),),
        ((ValuePath(()), Successor(ValuePath((0,)))),),
    )
    with pytest.raises(ValueError, match="resource-kind conversion"):
        OperationSignature(
            "Convert",
            (QUBIT,),
            QUMODE,
            frozenset({Effect.PURE}),
            resource_flow=conversion,
        )

    signature = OperationSignature(
        "TrustedConvert",
        (QUBIT,),
        QUMODE,
        frozenset({Effect.PURE}),
        resource_flow=conversion,
        resource_conversion_id="hyqf.test.qubit_to_qumode.v1",
    )
    assert signature.resource_conversion_id == "hyqf.test.qubit_to_qumode.v1"


def test_quantum_registry_signature_requires_an_explicit_flow_policy_or_contract():
    with pytest.raises(ValueError, match="explicit resource_flow"):
        OperationSignature("Implicit", (QUBIT,), QUBIT, frozenset({Effect.UNITARY}))


def test_source_qop_records_the_language_defined_positional_policy():
    path = FIXTURES / "typing" / "valid" / "source_qop_declaration.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)
    declaration = program.declarations[0]
    signature = checked.semantic_model.callable_signatures[CallableId(f"source::{declaration.name}")]

    assert signature.flow_policy == "positional"


def test_symbol_catalog_resolves_every_parameter_local_and_global_symbol_id():
    program = load("model_pipeline.hyqf")
    checked = TypeChecker().check(program)
    model = checked.semantic_model

    assert set(model.global_symbols) < set(model.symbols)
    assert set(model.resolved_symbols.values()) <= set(model.symbols)
    assert {symbol.kind for symbol in model.symbols.values()} >= {"qfunc", "param", "let", "qop"}


def test_qfunc_resource_summary_preserves_swapped_result_lineage_at_call_sites():
    program = load("qfunc_swapped_results.hyqf")
    checked = TypeChecker().check(program)
    swap, caller = program.declarations
    call = caller.body.statements[0].value
    summary = checked.semantic_model.function_summaries[CallableId("source::swap")]
    caller_summary = checked.semantic_model.function_summaries[CallableId("source::caller")]
    transfer = checked.ownership.transfers[call.node_id]

    assert summary.complete and caller_summary.complete
    assert summary.resource_flow.quantum_outputs == (
        (ValuePath((0,)), Successor(ValuePath((1,)))),
        (ValuePath((1,)), Successor(ValuePath((0,)))),
    )
    assert transfer.authoritative
    assert transfer.successors == (
        (transfer.inputs[1], transfer.outputs[0]),
        (transfer.inputs[0], transfer.outputs[1]),
    )
    assert caller_summary.resource_flow == summary.resource_flow


def test_qfunc_summaries_distinguish_fresh_and_terminal_resources():
    program = load("qfunc_fresh_and_terminal.hyqf")
    checked = TypeChecker().check(program)
    make = checked.semantic_model.function_summaries[CallableId("source::make")]
    consume = checked.semantic_model.function_summaries[CallableId("source::consume")]

    assert make.complete
    assert isinstance(make.resource_flow.quantum_outputs[0][1], Fresh)
    assert consume.complete
    assert consume.resource_flow.quantum_outputs == ()
    assert consume.terminal_inputs == (ValuePath((0,)),)


def test_recursive_qfunc_resource_summary_is_explicitly_non_authoritative():
    program = load("recursive_qfunc.hyqf")
    checked = TypeChecker().check(program)
    function = program.declarations[0]
    call = function.body.statements[0].value
    summary = checked.semantic_model.function_summaries[CallableId("source::recur")]

    assert not summary.complete
    assert summary.resource_flow is None
    assert summary.terminal_inputs == ()
    assert "recursive" in summary.reason
    assert not checked.ownership.transfers[call.node_id].authoritative


def test_static_array_return_summary_reports_shape_dependent_boundary():
    program = load("static_array_return.hyqf")
    checked = TypeChecker().check(program)
    summary = checked.semantic_model.function_summaries[CallableId("source::make_pair")]

    assert not summary.complete
    assert summary.resource_flow is None
    assert "shape" in summary.reason


def test_classical_calls_do_not_make_resource_summaries_incomplete():
    path = FIXTURES / "typing" / "valid" / "classical_calls_classical.hyqf"
    program = parse_source(path.read_text(encoding="utf-8"), file=path.name)
    checked = TypeChecker().check(program)

    assert all(summary.complete for summary in checked.semantic_model.function_summaries.values())
    assert all(transfer.authoritative for transfer in checked.ownership.transfers.values())


def test_semantic_model_validation_rejects_a_dangling_symbol_reference():
    program = load("model_pipeline.hyqf")
    model = TypeChecker().check(program).semantic_model
    node_id = next(iter(model.resolved_symbols))
    damaged = replace(
        model,
        resolved_symbols=frozen_map({**model.resolved_symbols, node_id: SymbolId(999_999)}),
    )

    with pytest.raises(SemanticModelError, match="absent from symbol catalog"):
        damaged.validate()


def test_semantic_model_validation_rejects_an_unclassified_transfer_output():
    program = load("nested_qop_calls.hyqf")
    model = TypeChecker().check(program).semantic_model
    node_id, transfer = next(iter(model.ownership.transfers.items()))
    damaged_transfer = replace(
        transfer,
        successors=(),
        terminal_inputs=transfer.inputs,
        fresh_outputs=(),
    )
    damaged_ownership = replace(
        model.ownership,
        transfers=frozen_map({**model.ownership.transfers, node_id: damaged_transfer}),
    )
    damaged = replace(model, ownership=damaged_ownership)

    with pytest.raises(SemanticModelError, match="does not classify every output"):
        damaged.validate()


def test_explicit_non_positional_qop_contract_controls_call_lineage():
    contract = ResourceFlowContract(
        (ValuePath((0,)), ValuePath((1,))),
        (
            (ValuePath((0,)), Successor(ValuePath((1,)))),
            (ValuePath((1,)), Successor(ValuePath((0,)))),
        ),
    )
    registry = QopRegistry()
    registry.register(
        OperationSignature(
            "Swap",
            (QUBIT, QUBIT),
            TUPLE(QUBIT, QUBIT),
            frozenset({Effect.UNITARY}),
            resource_flow=contract,
        )
    )
    program = load("external_swap_qop.hyqf")
    checked = TypeChecker(registry).check(program)
    call = program.declarations[0].body.statements[0].value
    transfer = checked.ownership.transfers[call.node_id]

    assert transfer.authoritative
    assert transfer.successors == (
        (transfer.inputs[1], transfer.outputs[0]),
        (transfer.inputs[0], transfer.outputs[1]),
    )


def test_qfunc_summary_preserves_registered_fresh_allocation_class():
    contract = ResourceFlowContract(
        (),
        ((ValuePath(()), Fresh("logical-zero")),),
    )
    registry = QopRegistry()
    registry.register(
        OperationSignature(
            "PrepareZero",
            (),
            QUBIT,
            frozenset({Effect.ALLOCATION}),
            resource_flow=contract,
        )
    )
    program = load("external_fresh_qop.hyqf")
    checked = TypeChecker(registry).check(program)
    summary = checked.semantic_model.function_summaries[CallableId("source::prepare")]

    assert summary.resource_flow.quantum_outputs == (
        (ValuePath(()), Fresh("logical-zero")),
    )
