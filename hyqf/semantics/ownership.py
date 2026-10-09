from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping

from hyqf.diagnostics.errors import ControlFlowOwnershipError, HyQFTypeError, LinearUseError, ResourceLeakError
from hyqf.frontend import ast
from hyqf.frontend.spans import SourceSpan
from hyqf.semantics.model import (
    ArrayOwnershipEvent,
    ArrayShape,
    ArrayUpdateFact,
    BindingFact,
    BindingId,
    BranchJoin,
    CallableId,
    FunctionResourceSummary,
    OwnershipFacts,
    ResourceId,
    ResourceTransfer,
    ResourceUse,
    frozen_map,
)
from hyqf.semantics.qops import (
    Fresh,
    QopRegistry,
    ResourceFlowContract,
    Successor,
    ValuePath,
    parameter_quantum_paths,
    result_quantum_paths,
)
from hyqf.semantics.symbols import SymbolTable
from hyqf.semantics.types import (
    ARRAY,
    BIT,
    BOOL,
    COMPLEX,
    FLOAT,
    INT,
    UINT,
    ArrayType,
    FunctionType,
    QuantumType,
    QuditType,
    TupleType,
    Type,
    VOID,
    promote_numeric,
)


@dataclass(frozen=True)
class ResourceValue:
    resource_id: ResourceId
    type: Type
    origin: SourceSpan


@dataclass(frozen=True)
class CompositeValue:
    kind: str
    elements: tuple[OwnedValue, ...]


@dataclass
class ArrayValue:
    elements: list[OwnedValue]


OwnedValue = ResourceValue | CompositeValue | ArrayValue | None


@dataclass
class OwnershipBinding:
    binding_id: BindingId
    name: str
    type: Type
    value: OwnedValue
    span: SourceSpan
    consumed_at: SourceSpan | None = None

    @property
    def is_live(self) -> bool:
        return self.value is not None


class OwnershipEnvironment:
    def __init__(self) -> None:
        self.frames: list[dict[str, OwnershipBinding]] = [{}]

    def clone(self) -> "OwnershipEnvironment":
        clone = OwnershipEnvironment()
        clone.frames = deepcopy(self.frames)
        return clone

    def replace_with(self, other: "OwnershipEnvironment") -> None:
        self.frames = deepcopy(other.frames)

    def push(self) -> None:
        self.frames.append({})

    def pop(self) -> dict[str, OwnershipBinding]:
        return self.frames.pop()

    def resolve(self, name: str) -> tuple[int, OwnershipBinding] | None:
        for index in range(len(self.frames) - 1, -1, -1):
            binding = self.frames[index].get(name)
            if binding is not None:
                return index, binding
        return None


class OwnershipChecker:
    """Validate linear use of quantum values after type checking succeeds."""

    def __init__(
        self,
        global_scope: SymbolTable,
        registry: QopRegistry | None = None,
        function_summaries: Mapping[CallableId, FunctionResourceSummary] | None = None,
        recursive_functions: frozenset[str] = frozenset(),
    ):
        self.global_scope = global_scope
        self.registry = registry
        self._known_function_summaries = dict(function_summaries or {})
        self._recursive_functions = recursive_functions
        self._next_binding_id = 0
        self._next_resource_id = 0
        self._bindings: dict[BindingId, BindingFact] = {}
        self._declaration_bindings = {}
        self._use_bindings = {}
        self._uses = {}
        self._transfers = {}
        self._branch_joins: dict[ast.NodeId, list[BranchJoin]] = {}
        self._array_shapes = {}
        self._array_events = {}
        self._array_updates = {}
        self._exits = {}
        self._function_summaries: dict[CallableId, FunctionResourceSummary] = {}
        self._resource_predecessors: dict[ResourceId, tuple[ResourceId, ...]] = {}
        self._resource_types: dict[ResourceId, Type] = {}
        self._resource_fresh_classes: dict[ResourceId, str] = {}
        self._current_input_paths: dict[ResourceId, ValuePath] = {}
        self._current_return_nodes: list[ast.NodeId] = []
        self._current_summary_complete = True
        self._current_summary_reason: str | None = None

    def check(self, program: ast.Program) -> OwnershipFacts:
        for decl in program.declarations:
            if isinstance(decl, ast.FuncDecl):
                self._check_function(decl)
        return OwnershipFacts(
            bindings=frozen_map(self._bindings),
            declaration_bindings=frozen_map(self._declaration_bindings),
            use_bindings=frozen_map(self._use_bindings),
            uses=frozen_map(self._uses),
            transfers=frozen_map(self._transfers),
            branch_joins=frozen_map({node: tuple(joins) for node, joins in self._branch_joins.items()}),
            array_shapes=frozen_map(self._array_shapes),
            array_events=frozen_map(self._array_events),
            array_updates=frozen_map(self._array_updates),
            exits=frozen_map(self._exits),
            function_summaries=frozen_map(self._function_summaries),
        )

    def _check_function(self, decl: ast.FuncDecl) -> None:
        env = OwnershipEnvironment()
        self._current_input_paths = {}
        self._current_return_nodes = []
        self._current_summary_complete = decl.name not in self._recursive_functions
        self._current_summary_reason = (
            "recursive resource flow requires Phase D control-flow IR"
            if decl.name in self._recursive_functions
            else None
        )
        for index, parameter in enumerate(decl.params):
            value = self._fresh_value(parameter.annotation, parameter.span)
            resources = self._resource_ids(value)
            paths = parameter_quantum_paths((parameter.annotation,))
            paths = tuple(ValuePath((index, *path.parts[1:])) for path in paths)
            if len(resources) != len(paths):
                self._mark_summary_incomplete("parameter resource shape is not statically representable")
            else:
                self._current_input_paths.update(zip(resources, paths))
            self._define_new(
                env,
                parameter.name,
                parameter.annotation,
                value,
                parameter.span,
                parameter.node_id,
            )

        falls_through = self._check_block(decl.body, env, create_scope=False)
        if falls_through:
            self._ensure_no_live_resources(env, decl.span, f"function '{decl.name}' exits with a live quantum resource")
        self._summarize_function(decl)

    def _check_block(self, block: ast.Block, env: OwnershipEnvironment, *, create_scope: bool = True) -> bool:
        if create_scope:
            env.push()

        falls_through = True
        for statement in block.statements:
            if not falls_through:
                break
            falls_through = self._check_statement(statement, env)

        if create_scope:
            local = env.frames[-1]
            if falls_through:
                self._ensure_bindings_consumed(
                    local.values(), block.span, "quantum value does not escape its block"
                )
            env.pop()
        return falls_through

    def _check_statement(self, stmt: ast.Stmt, env: OwnershipEnvironment) -> bool:
        if isinstance(stmt, ast.LetStmt):
            value_type, value = self._eval_expr(stmt.value, env)
            self._bind_pattern(stmt.pattern, stmt.annotation or value_type, value, env)
            return True

        if isinstance(stmt, ast.VarStmt):
            value_type, value = self._eval_expr(stmt.value, env)
            self._ensure_value_not_dropped(value, stmt.span, stmt.name)
            self._define_new(env, stmt.name, stmt.annotation or value_type, None, stmt.span, stmt.node_id)
            return True

        if isinstance(stmt, ast.AssignmentStmt):
            _, value = self._eval_expr(stmt.value, env)
            self._ensure_value_not_dropped(value, stmt.span, "assignment")
            if isinstance(stmt.target, ast.IdentifierExpr):
                resolved = env.resolve(stmt.target.name)
                if resolved is not None:
                    self._use_bindings[stmt.target.node_id] = resolved[1].binding_id
            return True

        if isinstance(stmt, ast.ExprStmt):
            _, value = self._eval_expr(stmt.expression, env)
            self._ensure_value_not_dropped(value, stmt.span, "expression result")
            return True

        if isinstance(stmt, ast.IfStmt):
            _, condition_value = self._eval_expr(stmt.condition, env)
            self._ensure_value_not_dropped(condition_value, stmt.condition.span, "if condition")
            return self._check_if(stmt, env)

        if isinstance(stmt, ast.WhileStmt):
            _, condition_value = self._eval_expr(stmt.condition, env)
            self._ensure_value_not_dropped(condition_value, stmt.condition.span, "while condition")
            return self._check_while(stmt, env)

        if isinstance(stmt, ast.ReturnStmt):
            value: OwnedValue = None
            if stmt.value is not None:
                _, value = self._eval_expr(stmt.value, env)
            self._exits[stmt.node_id] = self._resource_ids(value)
            self._current_return_nodes.append(stmt.node_id)
            self._ensure_no_live_resources(env, stmt.span, "return leaves a live quantum resource")
            # Moving a value into return transfers it out of the function, so it is
            # intentionally not treated as a dropped temporary.
            _ = value
            return False

        if isinstance(stmt, (ast.BreakStmt, ast.ContinueStmt)):
            if self._has_live_resources(env):
                raise ControlFlowOwnershipError(
                    "break/continue with live quantum resources requires Phase E loop exits",
                    stmt.span,
                )
            self._exits[stmt.node_id] = ()
            return False

        if isinstance(stmt, ast.ForStmt):
            return True

        return True

    def _check_if(self, stmt: ast.IfStmt, env: OwnershipEnvironment) -> bool:
        base = env.clone()
        then_env = base.clone()
        else_env = base.clone()

        then_falls = self._check_block(stmt.then_block, then_env)
        else_falls = True
        if stmt.else_block is not None:
            else_falls = self._check_block(stmt.else_block, else_env)

        if then_falls and else_falls:
            self._merge_environments(env, base, then_env, else_env, stmt.span, stmt.node_id, "if")
            return True
        if then_falls:
            env.replace_with(then_env)
            return True
        if else_falls:
            env.replace_with(else_env)
            return True
        return False

    def _check_while(self, stmt: ast.WhileStmt, env: OwnershipEnvironment) -> bool:
        entry = env.clone()
        body_env = entry.clone()
        body_falls = self._check_block(stmt.body, body_env)
        if body_falls:
            # The entry environment is the zero-iteration path. A transformed
            # resource on the body path becomes a loop-carried successor.
            self._merge_environments(env, entry, entry, body_env, stmt.span, stmt.node_id, "while")
        else:
            env.replace_with(entry)
        return True

    def _merge_environments(
        self,
        target: OwnershipEnvironment,
        base: OwnershipEnvironment,
        left: OwnershipEnvironment,
        right: OwnershipEnvironment,
        span: SourceSpan,
        node_id: ast.NodeId,
        kind: str,
    ) -> None:
        merged = base.clone()
        for frame_index, base_frame in enumerate(base.frames):
            for name, base_binding in base_frame.items():
                if not self._is_owned_type(base_binding.type):
                    continue
                left_binding = left.frames[frame_index][name]
                right_binding = right.frames[frame_index][name]
                if left_binding.is_live != right_binding.is_live:
                    raise ControlFlowOwnershipError(
                        f"quantum resource '{name}' is not live on every control-flow path",
                        span,
                        name,
                    )
                if not left_binding.is_live:
                    output = OwnershipBinding(
                        self._new_binding_id(),
                        name,
                        base_binding.type,
                        None,
                        base_binding.span,
                        left_binding.consumed_at or right_binding.consumed_at,
                    )
                    merged.frames[frame_index][name] = output
                    self._record_binding(output, None, synthetic=True)
                    self._record_join(node_id, kind, name, left_binding, right_binding, output)
                    continue

                if not self._is_complete_value(left_binding.value) or not self._is_complete_value(right_binding.value):
                    raise ControlFlowOwnershipError(
                        f"quantum array '{name}' is only partially owned on a control-flow path",
                        span,
                        name,
                    )

                if self._value_key(left_binding.value) == self._value_key(right_binding.value):
                    merged.frames[frame_index][name] = deepcopy(left_binding)
                    self._record_join(node_id, kind, name, left_binding, right_binding, left_binding)
                    continue

                output = OwnershipBinding(
                    self._new_binding_id(),
                    name,
                    base_binding.type,
                    self._fresh_merged_value(
                        base_binding.type,
                        left_binding.value,
                        right_binding.value,
                        span,
                        name,
                    ),
                    span,
                )
                merged.frames[frame_index][name] = output
                self._record_binding(output, None, synthetic=True)
                self._record_join(node_id, kind, name, left_binding, right_binding, output)
        target.replace_with(merged)

    def _eval_expr(self, expr: ast.Expr, env: OwnershipEnvironment) -> tuple[Type, OwnedValue]:
        if isinstance(expr, ast.LiteralExpr):
            return {"Bool": BOOL, "Int": INT, "Float": FLOAT, "Complex": COMPLEX}[expr.literal_kind], None

        if isinstance(expr, ast.IdentifierExpr):
            local = env.resolve(expr.name)
            if local is not None:
                _, binding = local
                self._use_bindings[expr.node_id] = binding.binding_id
                if not self._is_owned_type(binding.type):
                    return binding.type, None
                if binding.value is None:
                    first = f" (first consumed at {binding.consumed_at})" if binding.consumed_at else ""
                    raise LinearUseError(
                        f"quantum value '{expr.name}' was already consumed{first}",
                        expr.span,
                        expr.name,
                    )
                if not self._is_complete_value(binding.value):
                    raise LinearUseError(
                        f"quantum array '{expr.name}' is partially consumed and cannot be used as a whole",
                        expr.span,
                        expr.name,
                    )
                value = binding.value
                resources = self._resource_ids(value)
                self._uses[expr.node_id] = ResourceUse(
                    expr.node_id, binding.binding_id, resources, "move"
                )
                binding.value = None
                binding.consumed_at = expr.span
                return binding.type, value
            symbol = self.global_scope.resolve(expr.name, expr.span)
            return symbol.type, None

        if isinstance(expr, ast.AllocationExpr):
            if expr.size is not None:
                self._eval_expr(expr.size, env)
                result_type = ARRAY(expr.quantum_type)
                if isinstance(expr.size, ast.LiteralExpr) and expr.size.literal_kind == "Int":
                    elements = [self._fresh_value(expr.quantum_type, expr.span) for _ in range(expr.size.value)]
                    return result_type, ArrayValue(elements)
            else:
                result_type = expr.quantum_type
            return result_type, self._fresh_value(result_type, expr.span)

        if isinstance(expr, ast.TupleExpr):
            results = [self._eval_expr(element, env) for element in expr.elements]
            result_type = TupleType(tuple(item[0] for item in results))
            values = tuple(item[1] for item in results)
            value = CompositeValue("tuple", values) if any(item is not None for item in values) else None
            return result_type, value

        if isinstance(expr, ast.ArrayExpr):
            results = [self._eval_expr(element, env) for element in expr.elements]
            result_type = ArrayType(results[0][0])
            values = tuple(item[1] for item in results)
            value = ArrayValue(list(values)) if any(item is not None for item in values) else None
            return result_type, value

        if isinstance(expr, ast.IndexExpr):
            base_type = self._peek_type(expr.base, env)
            if isinstance(base_type, ArrayType) and self._is_owned_type(base_type):
                return self._take_static_array_element(expr, base_type, env)
            self._eval_expr(expr.base, env)
            self._eval_expr(expr.index, env)
            return base_type.element, None

        if isinstance(expr, ast.UnaryExpr):
            operand_type, value = self._eval_expr(expr.operand, env)
            self._ensure_value_not_dropped(value, expr.span, "unary operand")
            return BOOL if expr.operator == "!" else operand_type, None

        if isinstance(expr, ast.BinaryExpr):
            left_type, left_value = self._eval_expr(expr.left, env)
            right_type, right_value = self._eval_expr(expr.right, env)
            self._ensure_value_not_dropped(left_value, expr.left.span, "binary operand")
            self._ensure_value_not_dropped(right_value, expr.right.span, "binary operand")
            if expr.operator in {"&&", "||", "==", "!=", "<", "<=", ">", ">="}:
                return BOOL, None
            if expr.operator in {"&", "|", "^"}:
                return BIT if left_type == BIT else INT, None
            promoted = promote_numeric(left_type, right_type) or left_type
            if expr.operator == "/" and promoted in {INT, UINT}:
                return FLOAT, None
            return promoted, None

        if isinstance(expr, ast.CallExpr):
            function_type = self._peek_type(expr.callee, env)
            assert isinstance(function_type, FunctionType)
            self._check_duplicate_call_arguments(expr.arguments, env)
            evaluated = [self._eval_expr(argument, env) for argument in expr.arguments]
            inputs = tuple(
                resource for _, argument_value in evaluated
                for resource in self._resource_ids(argument_value)
            )
            value = self._fresh_value(function_type.returns, expr.span)
            outputs = self._resource_ids(value)
            self._record_transfer(expr, inputs, outputs)
            return function_type.returns, value

        return self._peek_type(expr, env), None

    def _peek_type(self, expr: ast.Expr, env: OwnershipEnvironment) -> Type:
        if isinstance(expr, ast.IdentifierExpr):
            local = env.resolve(expr.name)
            if local is not None:
                return local[1].type
            return self.global_scope.resolve(expr.name, expr.span).type
        if isinstance(expr, ast.LiteralExpr):
            return {"Bool": BOOL, "Int": INT, "Float": FLOAT, "Complex": COMPLEX}[expr.literal_kind]
        if isinstance(expr, ast.AllocationExpr):
            return ARRAY(expr.quantum_type) if expr.size is not None else expr.quantum_type
        if isinstance(expr, ast.TupleExpr):
            return TupleType(tuple(self._peek_type(element, env) for element in expr.elements))
        if isinstance(expr, ast.ArrayExpr):
            return ArrayType(self._peek_type(expr.elements[0], env))
        if isinstance(expr, ast.IndexExpr):
            base = self._peek_type(expr.base, env)
            assert isinstance(base, ArrayType)
            return base.element
        if isinstance(expr, ast.UnaryExpr):
            return BOOL if expr.operator == "!" else self._peek_type(expr.operand, env)
        if isinstance(expr, ast.BinaryExpr):
            if expr.operator in {"&&", "||", "==", "!=", "<", "<=", ">", ">="}:
                return BOOL
            left = self._peek_type(expr.left, env)
            right = self._peek_type(expr.right, env)
            if expr.operator in {"&", "|", "^"}:
                return BIT if left == BIT else INT
            promoted = promote_numeric(left, right) or left
            return FLOAT if expr.operator == "/" and promoted in {INT, UINT} else promoted
        if isinstance(expr, ast.CallExpr):
            callee = self._peek_type(expr.callee, env)
            assert isinstance(callee, FunctionType)
            return callee.returns
        return VOID

    def _bind_pattern(
        self,
        pattern: ast.Pattern,
        value_type: Type,
        value: OwnedValue,
        env: OwnershipEnvironment,
    ) -> None:
        if isinstance(pattern, ast.WildcardPattern):
            self._ensure_value_not_dropped(value, pattern.span, "wildcard")
            return

        if isinstance(pattern, ast.IdentifierPattern):
            self._bind_name(env, pattern.name, value_type, value, pattern.span, pattern.node_id)
            return

        if isinstance(pattern, ast.IndexPattern):
            self._bind_array_updates([(pattern, value_type, value)], env)
            return

        if isinstance(pattern, ast.TuplePattern):
            assert isinstance(value_type, TupleType)
            values: tuple[OwnedValue, ...]
            if isinstance(value, CompositeValue) and value.kind == "tuple":
                values = value.elements
            else:
                values = tuple(None for _ in value_type.elements)
            leaves: list[tuple[ast.Pattern, Type, OwnedValue]] = []
            for subpattern, subtype, subvalue in zip(pattern.elements, value_type.elements, values):
                self._collect_pattern_leaves(subpattern, subtype, subvalue, leaves)
            updates: dict[str, list[tuple[ast.IndexPattern, Type, OwnedValue]]] = {}
            ordinary: list[tuple[ast.Pattern, Type, OwnedValue]] = []
            for subpattern, subtype, subvalue in leaves:
                if isinstance(subpattern, ast.IndexPattern):
                    updates.setdefault(subpattern.name, []).append((subpattern, subtype, subvalue))
                else:
                    ordinary.append((subpattern, subtype, subvalue))
            for group in updates.values():
                self._bind_array_updates(group, env)
            for subpattern, subtype, subvalue in ordinary:
                self._bind_pattern(subpattern, subtype, subvalue, env)

    def _collect_pattern_leaves(
        self,
        pattern: ast.Pattern,
        value_type: Type,
        value: OwnedValue,
        leaves: list[tuple[ast.Pattern, Type, OwnedValue]],
    ) -> None:
        if not isinstance(pattern, ast.TuplePattern):
            leaves.append((pattern, value_type, value))
            return
        assert isinstance(value_type, TupleType)
        if isinstance(value, CompositeValue) and value.kind == "tuple":
            values = value.elements
        else:
            values = tuple(None for _ in value_type.elements)
        for subpattern, subtype, subvalue in zip(pattern.elements, value_type.elements, values):
            self._collect_pattern_leaves(subpattern, subtype, subvalue, leaves)

    def _bind_array_updates(
        self,
        updates: list[tuple[ast.IndexPattern, Type, OwnedValue]],
        env: OwnershipEnvironment,
    ) -> None:
        name = updates[0][0].name
        resolved = env.resolve(name)
        if resolved is None:
            raise LinearUseError("quantum array update requires a local owned value", updates[0][0].span, name)
        frame_index, input_binding = resolved
        if not isinstance(input_binding.type, ArrayType) or not self._is_owned_type(input_binding.type):
            raise LinearUseError("indexed let target requires a linear quantum array", updates[0][0].span, name)

        shape = self._array_shapes.get(input_binding.binding_id)
        if shape is None or shape.extent is None:
            raise LinearUseError(
                "quantum array update requires a statically known length",
                updates[0][0].span,
                name,
            )
        if isinstance(input_binding.value, ArrayValue):
            elements = deepcopy(input_binding.value.elements)
        elif input_binding.value is None:
            elements = [None for _ in range(shape.extent)]
        else:
            raise LinearUseError(
                "quantum array update requires statically tracked elements",
                updates[0][0].span,
                name,
            )

        indices: list[int] = []
        replacements: list[tuple[ResourceId, ...]] = []
        seen: set[int] = set()
        for pattern, _, replacement in updates:
            index = self._static_int(pattern.index)
            assert index is not None
            if index < 0 or index >= shape.extent:
                raise HyQFTypeError(
                    "quantum array update index is out of bounds",
                    pattern.index.span,
                    expected=f"0..{shape.extent - 1}",
                    actual=index,
                )
            if index in seen:
                raise LinearUseError(
                    f"quantum array element '{name}[{index}]' is updated more than once",
                    pattern.span,
                    f"{name}[{index}]",
                )
            seen.add(index)
            if elements[index] is not None:
                raise ResourceLeakError(
                    f"updating '{name}[{index}]' would drop its live quantum resource",
                    pattern.span,
                    f"{name}[{index}]",
                )
            if replacement is None or not self._is_complete_value(replacement):
                raise LinearUseError(
                    f"quantum array update for '{name}[{index}]' has no complete replacement resource",
                    pattern.span,
                    f"{name}[{index}]",
                )
            elements[index] = replacement
            indices.append(index)
            replacements.append(self._resource_ids(replacement))

        if any(element is None for element in elements):
            raise ResourceLeakError(
                f"quantum array update leaves '{name}' only partially owned",
                updates[0][0].span,
                name,
            )

        output_value = ArrayValue(elements)
        output_binding = OwnershipBinding(
            self._new_binding_id(), name, input_binding.type, output_value, updates[0][0].span
        )
        env.frames[frame_index][name] = output_binding
        self._record_binding(output_binding, updates[0][0].node_id)
        output_elements = tuple(self._resource_ids(element) for element in elements)
        fact = ArrayUpdateFact(
            tuple(pattern.node_id for pattern, _, _ in updates),
            input_binding.binding_id,
            output_binding.binding_id,
            tuple(indices),
            shape.element_resources,
            output_elements,
            tuple(replacements),
        )
        for pattern, _, _ in updates:
            self._declaration_bindings[pattern.node_id] = output_binding.binding_id
            self._array_events[pattern.node_id] = ArrayOwnershipEvent(
                pattern.node_id, input_binding.binding_id, "update", self._static_int(pattern.index)
            )
            self._array_updates[pattern.node_id] = fact

    def _take_static_array_element(
        self,
        expr: ast.IndexExpr,
        array_type: ArrayType,
        env: OwnershipEnvironment,
    ) -> tuple[Type, OwnedValue]:
        index = self._static_int(expr.index)
        if index is None:
            raise LinearUseError(
                "runtime-indexed ownership for quantum arrays is deferred",
                expr.index.span,
            )
        if not isinstance(expr.base, ast.IdentifierExpr):
            raise LinearUseError(
                "quantum array indexing requires a named array binding",
                expr.base.span,
            )

        resolved = env.resolve(expr.base.name)
        if resolved is None:
            raise LinearUseError("quantum array is not a local owned value", expr.base.span, expr.base.name)
        _, binding = resolved
        if binding.value is None:
            first = f" (first consumed at {binding.consumed_at})" if binding.consumed_at else ""
            raise LinearUseError(
                f"quantum array '{expr.base.name}' was already consumed{first}",
                expr.span,
                expr.base.name,
            )
        if not isinstance(binding.value, ArrayValue):
            raise LinearUseError(
                "indexed ownership requires an array with a statically known length",
                expr.base.span,
                expr.base.name,
            )

        if index < 0 or index >= len(binding.value.elements):
            raise HyQFTypeError(
                "quantum array index is out of bounds",
                expr.index.span,
                expected=f"0..{len(binding.value.elements) - 1}",
                actual=index,
            )
        value = binding.value.elements[index]
        if value is None:
            raise LinearUseError(
                f"quantum array element '{expr.base.name}[{index}]' was already consumed",
                expr.span,
                f"{expr.base.name}[{index}]",
            )
        binding.value.elements[index] = None
        resources = self._resource_ids(value)
        self._use_bindings[expr.base.node_id] = binding.binding_id
        self._uses[expr.node_id] = ResourceUse(
            expr.node_id, binding.binding_id, resources, "array_take", index
        )
        self._array_events[expr.node_id] = ArrayOwnershipEvent(
            expr.node_id, binding.binding_id, "take", index
        )
        if all(element is None for element in binding.value.elements):
            binding.value = None
            binding.consumed_at = expr.span
        return array_type.element, value

    def _check_duplicate_call_arguments(
        self,
        arguments: list[ast.Expr],
        env: OwnershipEnvironment,
    ) -> None:
        seen: dict[ResourceId, tuple[str, SourceSpan]] = {}
        for index, argument in enumerate(arguments, start=1):
            label = self._argument_label(argument, index)
            for resource_id in self._argument_resource_ids(argument, env):
                previous = seen.get(resource_id)
                if previous is not None:
                    previous_label, previous_span = previous
                    raise LinearUseError(
                        f"duplicate quantum arguments '{previous_label}' and '{label}' "
                        f"refer to the same live resource (first argument at {previous_span})",
                        argument.span,
                        label,
                    )
                seen[resource_id] = (label, argument.span)

    def _argument_resource_ids(
        self,
        expr: ast.Expr,
        env: OwnershipEnvironment,
    ) -> tuple[ResourceId, ...]:
        if isinstance(expr, ast.IdentifierExpr):
            resolved = env.resolve(expr.name)
            if resolved is None:
                return ()
            return self._resource_ids(resolved[1].value)
        if isinstance(expr, (ast.TupleExpr, ast.ArrayExpr)):
            return tuple(
                resource_id
                for element in expr.elements
                for resource_id in self._argument_resource_ids(element, env)
            )
        if isinstance(expr, ast.IndexExpr) and isinstance(expr.base, ast.IdentifierExpr):
            index = self._static_int(expr.index)
            resolved = env.resolve(expr.base.name)
            if index is None or resolved is None or not isinstance(resolved[1].value, ArrayValue):
                return ()
            elements = resolved[1].value.elements
            if index < 0 or index >= len(elements):
                return ()
            return self._resource_ids(elements[index])
        return ()

    def _resource_ids(self, value: OwnedValue) -> tuple[ResourceId, ...]:
        if isinstance(value, ResourceValue):
            return (value.resource_id,)
        if isinstance(value, CompositeValue):
            return tuple(
                resource_id
                for element in value.elements
                for resource_id in self._resource_ids(element)
            )
        if isinstance(value, ArrayValue):
            return tuple(
                resource_id
                for element in value.elements
                for resource_id in self._resource_ids(element)
            )
        return ()

    def _argument_label(self, expr: ast.Expr, index: int) -> str:
        if isinstance(expr, ast.IdentifierExpr):
            return expr.name
        if (
            isinstance(expr, ast.IndexExpr)
            and isinstance(expr.base, ast.IdentifierExpr)
            and (static_index := self._static_int(expr.index)) is not None
        ):
            return f"{expr.base.name}[{static_index}]"
        return f"argument {index}"

    def _bind_name(
        self,
        env: OwnershipEnvironment,
        name: str,
        value_type: Type,
        value: OwnedValue,
        span: SourceSpan,
        declaration: ast.NodeId | None,
    ) -> None:
        existing = env.resolve(name)
        if existing is not None and existing[0] == len(env.frames) - 1:
            old_binding = existing[1]
            if self._is_owned_type(old_binding.type) and old_binding.is_live:
                raise ResourceLeakError(
                    f"rebinding '{name}' would drop its live quantum resource",
                    span,
                    name,
                )
        if self._is_owned_type(value_type) and existing is not None and self._is_owned_type(existing[1].type):
            frame_index, old_binding = existing
            if old_binding.is_live:
                raise ResourceLeakError(
                    f"rebinding '{name}' would drop its live quantum resource",
                    span,
                    name,
                )
            binding = OwnershipBinding(
                self._new_binding_id(), name, value_type, value, span
            )
            env.frames[frame_index][name] = binding
            self._record_binding(binding, declaration)
            return

        self._define_new(env, name, value_type, value, span, declaration)

    def _define_new(
        self,
        env: OwnershipEnvironment,
        name: str,
        value_type: Type,
        value: OwnedValue,
        span: SourceSpan,
        declaration: ast.NodeId | None = None,
    ) -> None:
        binding = OwnershipBinding(
            self._new_binding_id(), name, value_type, value, span
        )
        env.frames[-1][name] = binding
        self._record_binding(binding, declaration)

    def _fresh_value(self, value_type: Type, span: SourceSpan) -> OwnedValue:
        if isinstance(value_type, (QuantumType, QuditType, ArrayType)) and self._is_owned_type(value_type):
            value = ResourceValue(ResourceId(self._next_resource_id), value_type, span)
            self._next_resource_id += 1
            self._resource_types[value.resource_id] = value_type
            self._resource_fresh_classes[value.resource_id] = self._allocation_class(value_type)
            return value
        if isinstance(value_type, TupleType):
            elements = tuple(self._fresh_value(element, span) for element in value_type.elements)
            if any(element is not None for element in elements):
                return CompositeValue("tuple", elements)
        return None

    def _fresh_merged_value(
        self,
        value_type: Type,
        left: OwnedValue,
        right: OwnedValue,
        span: SourceSpan,
        name: str,
    ) -> OwnedValue:
        if isinstance(left, ArrayValue) and isinstance(right, ArrayValue):
            if len(left.elements) != len(right.elements):
                raise ControlFlowOwnershipError(
                    f"quantum array '{name}' has different lengths across control-flow paths",
                    span,
                    name,
                )
            assert isinstance(value_type, ArrayType)
            return ArrayValue(
                [self._fresh_value(value_type.element, span) for _ in left.elements]
            )
        return self._fresh_value(value_type, span)

    def _record_binding(
        self,
        binding: OwnershipBinding,
        declaration: ast.NodeId | None,
        *,
        synthetic: bool = False,
    ) -> None:
        resources = self._resource_ids(binding.value)
        self._bindings[binding.binding_id] = BindingFact(
            binding.binding_id,
            binding.name,
            binding.type,
            declaration,
            binding.span,
            resources,
            synthetic,
        )
        if declaration is not None:
            self._declaration_bindings[declaration] = binding.binding_id
        if isinstance(binding.type, ArrayType):
            if isinstance(binding.value, ArrayValue):
                self._array_shapes[binding.binding_id] = ArrayShape(
                    len(binding.value.elements),
                    tuple(self._resource_ids(element) for element in binding.value.elements),
                )
            elif self._is_owned_type(binding.type):
                self._array_shapes[binding.binding_id] = ArrayShape(None)

    def _record_join(
        self,
        node_id: ast.NodeId,
        kind: str,
        name: str,
        left: OwnershipBinding,
        right: OwnershipBinding,
        output: OwnershipBinding,
    ) -> None:
        join = BranchJoin(
            node_id,
            name,
            (left.binding_id, right.binding_id),
            (self._resource_ids(left.value), self._resource_ids(right.value)),
            output.binding_id,
            self._resource_ids(output.value),
            kind,
        )
        self._branch_joins.setdefault(node_id, []).append(join)
        left_resources = self._resource_ids(left.value)
        right_resources = self._resource_ids(right.value)
        output_resources = self._resource_ids(output.value)
        if len(left_resources) == len(right_resources) == len(output_resources):
            for left_resource, right_resource, output_resource in zip(
                left_resources, right_resources, output_resources
            ):
                predecessors = tuple(
                    dict.fromkeys(
                        resource
                        for resource in (left_resource, right_resource)
                        if resource != output_resource
                    )
                )
                if predecessors:
                    self._resource_predecessors[output_resource] = predecessors

    def _record_transfer(
        self,
        expr: ast.CallExpr,
        inputs: tuple[ResourceId, ...],
        outputs: tuple[ResourceId, ...],
    ) -> None:
        if not inputs and not outputs:
            self._transfers[expr.node_id] = ResourceTransfer(
                expr.node_id, (), (), (), (), (), True
            )
            return
        successors: list[tuple[ResourceId, ResourceId]] = []
        fresh_outputs: list[ResourceId] = []
        authoritative = True
        contract: ResourceFlowContract | None = None
        output_paths: tuple[ValuePath, ...] = ()
        signature = None
        if self.registry is not None and isinstance(expr.callee, ast.IdentifierExpr):
            signature = self.registry.get(expr.callee.name)
        if signature is not None:
            contract = signature.resource_flow
            assert contract is not None
            output_paths = result_quantum_paths(signature.returns)
        elif isinstance(expr.callee, ast.IdentifierExpr):
            callable_id = CallableId(f"source::{expr.callee.name}", "1.0")
            summary = self._known_function_summaries.get(callable_id)
            if summary is not None and summary.complete and summary.resource_flow is not None:
                contract = summary.resource_flow
                symbol = self.global_scope.resolve(expr.callee.name, expr.callee.span)
                assert isinstance(symbol.type, FunctionType)
                output_paths = result_quantum_paths(symbol.type.returns)
            else:
                authoritative = False
                self._mark_summary_incomplete(
                    summary.reason if summary is not None and summary.reason else "callee resource flow is not available"
                )

        if contract is not None and (
            len(contract.quantum_inputs) != len(inputs) or len(output_paths) != len(outputs)
        ):
            contract = None
            authoritative = False
            self._mark_summary_incomplete("call resource shape is not statically representable")

        if contract is not None:
            origins = dict(contract.quantum_outputs)
            for output_path, output_resource in zip(output_paths, outputs):
                origin = origins[output_path]
                if isinstance(origin, Successor):
                    input_index = contract.quantum_inputs.index(origin.input_path)
                    successors.append((inputs[input_index], output_resource))
                elif isinstance(origin, Fresh):
                    fresh_outputs.append(output_resource)
                    self._resource_fresh_classes[output_resource] = origin.allocation_class
        else:
            successors.extend(zip(inputs, outputs))
            fresh_outputs.extend(outputs[len(inputs):])

        for source, target in successors:
            self._resource_predecessors[target] = (source,)
        continued = {source for source, _ in successors}
        self._transfers[expr.node_id] = ResourceTransfer(
            expr.node_id,
            inputs,
            outputs,
            tuple(successors),
            tuple(resource for resource in inputs if resource not in continued),
            tuple(fresh_outputs),
            authoritative,
        )

    def _summarize_function(self, decl: ast.FuncDecl) -> None:
        callable_id = CallableId(f"source::{decl.name}", "1.0")
        parameter_paths = parameter_quantum_paths(tuple(param.annotation for param in decl.params))
        output_paths = result_quantum_paths(decl.return_type or VOID)
        return_resources = [self._exits[node_id] for node_id in self._current_return_nodes]

        if output_paths and not return_resources:
            self._mark_summary_incomplete("quantum-returning function has no analyzed return exit")
        if any(len(resources) != len(output_paths) for resources in return_resources):
            self._mark_summary_incomplete("return resource shape is not statically representable")

        origins_by_output: list[set[Successor | Fresh]] = [set() for _ in output_paths]
        if self._current_summary_complete:
            for resources in return_resources:
                for index, resource in enumerate(resources):
                    origins_by_output[index].update(self._trace_origins(resource, set()))
            if any(len(origins) != 1 for origins in origins_by_output):
                self._mark_summary_incomplete("return resource origin differs across control-flow paths")

        contract = None
        if self._current_summary_complete:
            contract = ResourceFlowContract(
                parameter_paths,
                tuple(
                    (path, next(iter(origins)))
                    for path, origins in zip(output_paths, origins_by_output)
                ),
            )
        used_inputs = {
            origin.input_path
            for origins in origins_by_output
            for origin in origins
            if isinstance(origin, Successor)
        }
        self._function_summaries[callable_id] = FunctionResourceSummary(
            callable_id,
            contract,
            (
                tuple(path for path in parameter_paths if path not in used_inputs)
                if self._current_summary_complete
                else ()
            ),
            self._current_summary_complete,
            self._current_summary_reason,
        )

    def _trace_origins(
        self,
        resource: ResourceId,
        visiting: set[ResourceId],
    ) -> set[Successor | Fresh]:
        input_path = self._current_input_paths.get(resource)
        if input_path is not None:
            return {Successor(input_path)}
        if resource in visiting:
            self._mark_summary_incomplete("cyclic resource predecessor graph")
            return set()
        predecessors = self._resource_predecessors.get(resource)
        if predecessors:
            visiting.add(resource)
            origins = set().union(
                *(self._trace_origins(predecessor, visiting) for predecessor in predecessors)
            )
            visiting.remove(resource)
            return origins
        return {Fresh(self._resource_fresh_classes[resource])}

    def _allocation_class(self, value_type: Type) -> str:
        if isinstance(value_type, QuantumType):
            return value_type.name.lower()
        if isinstance(value_type, QuditType):
            return "qudit"
        if isinstance(value_type, ArrayType):
            return "quantum-array"
        return "quantum"

    def _mark_summary_incomplete(self, reason: str) -> None:
        self._current_summary_complete = False
        if self._current_summary_reason is None:
            self._current_summary_reason = reason

    def _is_owned_type(self, value_type: Type) -> bool:
        if isinstance(value_type, (QuantumType, QuditType)):
            return True
        if isinstance(value_type, ArrayType):
            return self._is_owned_type(value_type.element)
        if isinstance(value_type, TupleType):
            return any(self._is_owned_type(element) for element in value_type.elements)
        return False

    def _ensure_value_not_dropped(self, value: OwnedValue, span: SourceSpan, resource: str) -> None:
        if value is not None:
            raise ResourceLeakError("quantum result is discarded", span, resource)

    def _ensure_no_live_resources(self, env: OwnershipEnvironment, span: SourceSpan, message: str) -> None:
        for frame in env.frames:
            self._ensure_bindings_consumed(frame.values(), span, message)

    def _ensure_bindings_consumed(
        self,
        bindings,
        span: SourceSpan,
        message: str,
    ) -> None:
        for binding in bindings:
            if self._is_owned_type(binding.type) and binding.is_live:
                raise ResourceLeakError(message, span, binding.name)

    def _has_live_resources(self, env: OwnershipEnvironment) -> bool:
        return any(
            self._is_owned_type(binding.type) and binding.is_live
            for frame in env.frames
            for binding in frame.values()
        )

    def _value_key(self, value: OwnedValue):
        if isinstance(value, ResourceValue):
            return ("resource", value.resource_id)
        if isinstance(value, CompositeValue):
            return (value.kind, tuple(self._value_key(element) for element in value.elements))
        if isinstance(value, ArrayValue):
            return ("array", tuple(self._value_key(element) for element in value.elements))
        return None

    def _is_complete_value(self, value: OwnedValue) -> bool:
        if isinstance(value, ArrayValue):
            return all(element is not None and self._is_complete_value(element) for element in value.elements)
        if isinstance(value, CompositeValue):
            return all(self._is_complete_value(element) for element in value.elements if element is not None)
        return True

    def _static_int(self, expr: ast.Expr) -> int | None:
        if isinstance(expr, ast.LiteralExpr) and expr.literal_kind == "Int":
            return expr.value
        if isinstance(expr, ast.UnaryExpr) and expr.operator in {"+", "-"}:
            operand = self._static_int(expr.operand)
            if operand is not None:
                return operand if expr.operator == "+" else -operand
        return None

    def _new_binding_id(self) -> BindingId:
        result = BindingId(self._next_binding_id)
        self._next_binding_id += 1
        return result
