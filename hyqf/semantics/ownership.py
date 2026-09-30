from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from hyqf.diagnostics.errors import ControlFlowOwnershipError, HyQFTypeError, LinearUseError, ResourceLeakError
from hyqf.frontend import ast
from hyqf.frontend.spans import SourceSpan
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
    resource_id: int
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
    binding_id: int
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

    def __init__(self, global_scope: SymbolTable):
        self.global_scope = global_scope
        self._next_binding_id = 0
        self._next_resource_id = 0

    def check(self, program: ast.Program) -> None:
        for decl in program.declarations:
            if isinstance(decl, ast.FuncDecl):
                self._check_function(decl)

    def _check_function(self, decl: ast.FuncDecl) -> None:
        env = OwnershipEnvironment()
        for parameter in decl.params:
            self._define_new(
                env,
                parameter.name,
                parameter.annotation,
                self._fresh_value(parameter.annotation, parameter.span),
                parameter.span,
            )

        falls_through = self._check_block(decl.body, env, create_scope=False)
        if falls_through:
            self._ensure_no_live_resources(env, decl.span, f"function '{decl.name}' exits with a live quantum resource")

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
            self._define_new(env, stmt.name, stmt.annotation or value_type, None, stmt.span)
            return True

        if isinstance(stmt, ast.AssignmentStmt):
            _, value = self._eval_expr(stmt.value, env)
            self._ensure_value_not_dropped(value, stmt.span, "assignment")
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
            self._merge_environments(env, base, then_env, else_env, stmt.span)
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
            self._merge_environments(env, entry, entry, body_env, stmt.span)
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
                    merged.frames[frame_index][name] = OwnershipBinding(
                        self._new_binding_id(),
                        name,
                        base_binding.type,
                        None,
                        base_binding.span,
                        left_binding.consumed_at or right_binding.consumed_at,
                    )
                    continue

                if not self._is_complete_value(left_binding.value) or not self._is_complete_value(right_binding.value):
                    raise ControlFlowOwnershipError(
                        f"quantum array '{name}' is only partially owned on a control-flow path",
                        span,
                        name,
                    )

                if self._value_key(left_binding.value) == self._value_key(right_binding.value):
                    merged.frames[frame_index][name] = deepcopy(left_binding)
                    continue

                merged.frames[frame_index][name] = OwnershipBinding(
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
        target.replace_with(merged)

    def _eval_expr(self, expr: ast.Expr, env: OwnershipEnvironment) -> tuple[Type, OwnedValue]:
        if isinstance(expr, ast.LiteralExpr):
            return {"Bool": BOOL, "Int": INT, "Float": FLOAT, "Complex": COMPLEX}[expr.literal_kind], None

        if isinstance(expr, ast.IdentifierExpr):
            local = env.resolve(expr.name)
            if local is not None:
                _, binding = local
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
            for argument in expr.arguments:
                self._eval_expr(argument, env)
            return function_type.returns, self._fresh_value(function_type.returns, expr.span)

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
            self._bind_name(env, pattern.name, value_type, value, pattern.span)
            return

        if isinstance(pattern, ast.TuplePattern):
            assert isinstance(value_type, TupleType)
            values: tuple[OwnedValue, ...]
            if isinstance(value, CompositeValue) and value.kind == "tuple":
                values = value.elements
            else:
                values = tuple(None for _ in value_type.elements)
            for subpattern, subtype, subvalue in zip(pattern.elements, value_type.elements, values):
                self._bind_pattern(subpattern, subtype, subvalue, env)

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
        if all(element is None for element in binding.value.elements):
            binding.value = None
            binding.consumed_at = expr.span
        return array_type.element, value

    def _check_duplicate_call_arguments(
        self,
        arguments: list[ast.Expr],
        env: OwnershipEnvironment,
    ) -> None:
        seen: dict[int, tuple[str, SourceSpan]] = {}
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
    ) -> tuple[int, ...]:
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

    def _resource_ids(self, value: OwnedValue) -> tuple[int, ...]:
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
            env.frames[frame_index][name] = OwnershipBinding(
                self._new_binding_id(), name, value_type, value, span
            )
            return

        self._define_new(env, name, value_type, value, span)

    def _define_new(
        self,
        env: OwnershipEnvironment,
        name: str,
        value_type: Type,
        value: OwnedValue,
        span: SourceSpan,
    ) -> None:
        env.frames[-1][name] = OwnershipBinding(
            self._new_binding_id(), name, value_type, value, span
        )

    def _fresh_value(self, value_type: Type, span: SourceSpan) -> OwnedValue:
        if isinstance(value_type, (QuantumType, QuditType, ArrayType)) and self._is_owned_type(value_type):
            value = ResourceValue(self._next_resource_id, value_type, span)
            self._next_resource_id += 1
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

    def _new_binding_id(self) -> int:
        result = self._next_binding_id
        self._next_binding_id += 1
        return result
