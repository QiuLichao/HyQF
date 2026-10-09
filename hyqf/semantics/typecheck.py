from __future__ import annotations

from dataclasses import dataclass

from hyqf.diagnostics.errors import (
    EffectError,
    HyQFTypeError,
    NameResolutionError,
    SemanticModelError,
    VerificationUnsupported,
)
from hyqf.frontend import ast
from hyqf.semantics.effects import Effect, normalize_effects
from hyqf.semantics.model import (
    CallableId,
    FlowExit,
    FlowSummary,
    SemanticModel,
    frozen_map,
)
from hyqf.semantics.ownership import OwnershipChecker
from hyqf.semantics.qops import OperationSignature, QopRegistry, standard_classical_functions, standard_registry
from hyqf.semantics.symbols import Symbol, SymbolTable
from hyqf.semantics.types import (
    ARRAY,
    BIT,
    BOOL,
    COMPLEX,
    FLOAT,
    INT,
    QUBIT,
    QUMODE,
    UINT,
    VOID,
    ArrayType,
    FunctionType,
    TupleType,
    Type,
    ensure_supported_type,
    is_numeric,
    promote_numeric,
)


@dataclass(frozen=True)
class CheckedModule:
    symbols: SymbolTable
    effects: dict[str, frozenset[Effect]]
    semantic_model: SemanticModel

    @property
    def node_types(self):
        return self.semantic_model.node_types

    @property
    def resolved_symbols(self):
        return self.semantic_model.resolved_symbols

    @property
    def call_targets(self):
        return self.semantic_model.call_targets

    @property
    def ownership(self):
        return self.semantic_model.ownership


class TypeChecker:
    def __init__(self, registry: QopRegistry | None = None):
        self.registry = registry or standard_registry()
        self.global_scope = SymbolTable()
        self.effects: dict[str, frozenset[Effect]] = {}
        self.node_types = {}
        self.resolved_symbols = {}
        self.call_targets = {}
        self.flow_summaries = {}
        self.direct_effects: dict[str, set[Effect]] = {}
        self.function_calls: dict[str, set[str]] = {}
        self._current_function: str | None = None
        for sig in self.registry.signatures().values():
            self.global_scope.define(Symbol(sig.name, sig.function_type, sig.kind, effects=sig.effects))
        self.classical_functions = standard_classical_functions()
        for sig in self.classical_functions.values():
            self.global_scope.define(Symbol(sig.name, sig.function_type, sig.kind, effects=sig.effects))

    def check(self, program: ast.Program) -> CheckedModule:
        if program.node_id == ast.UNASSIGNED_NODE_ID:
            ast.assign_node_ids(program)
        for decl in program.declarations:
            if isinstance(decl, ast.QopDecl):
                self._register_qop(decl)
        for decl in program.declarations:
            if isinstance(decl, ast.SymbolDecl):
                self._ensure_supported_type(decl.annotation, decl.span)
                if decl.annotation.contains_quantum():
                    raise HyQFTypeError("symbol parameters may not have quantum types", decl.span)
                self.global_scope.define(Symbol(decl.name, decl.annotation, "symbol", decl.span))
                self._record_decl_symbol(decl, decl.name)
            elif isinstance(decl, ast.ConstDecl):
                pass
            elif isinstance(decl, ast.FuncDecl):
                returns = decl.return_type or VOID
                self._ensure_supported_type(returns, decl.span)
                for param in decl.params:
                    self._ensure_supported_type(param.annotation, param.span)
                ftype = FunctionType(tuple(p.annotation for p in decl.params), returns, decl.is_quantum)
                self.global_scope.define(Symbol(decl.name, ftype, "qfunc" if decl.is_quantum else "func", decl.span))
                self._record_decl_symbol(decl, decl.name)

        function_decls: list[ast.FuncDecl] = []
        for decl in program.declarations:
            if isinstance(decl, ast.ConstDecl):
                self._check_const(decl)
            elif isinstance(decl, ast.SymbolDecl) and decl.constraint:
                constraint_type = self._expr_type(decl.constraint, self.global_scope, None, set())
                self._expect_type(BOOL, constraint_type, decl.constraint.span)
            elif isinstance(decl, ast.FuncDecl):
                self._check_function(decl)
                function_decls.append(decl)
            elif isinstance(decl, ast.VerifyDecl):
                raise VerificationUnsupported("verification is Phase G and is not implemented yet", decl.span)
        self._solve_function_effects(function_decls)
        ownership = self._check_ownership(program, function_decls)
        model = SemanticModel(
            program=program,
            global_symbols=frozen_map(self.global_scope.snapshot()),
            symbols=frozen_map(self.global_scope.catalog_snapshot()),
            node_types=frozen_map(self.node_types),
            resolved_symbols=frozen_map(self.resolved_symbols),
            binding_versions=frozen_map(ownership.use_bindings | ownership.declaration_bindings),
            call_targets=frozen_map(self.call_targets),
            callable_signatures=frozen_map({
                signature.callable_id: signature
                for signature in (*self.registry.signatures().values(), *self.classical_functions.values())
            }),
            function_summaries=ownership.function_summaries,
            function_effects=frozen_map(self.effects),
            flow_summaries=frozen_map(self.flow_summaries),
            ownership=ownership,
            array_shapes=ownership.array_shapes,
            array_updates=ownership.array_updates,
            branch_joins=ownership.branch_joins,
        )
        model.validate()
        return CheckedModule(self.global_scope, dict(self.effects), model)

    def _register_qop(self, decl: ast.QopDecl) -> None:
        self._ensure_supported_type(decl.return_type, decl.span)
        for param in decl.params:
            self._ensure_supported_type(param.annotation, param.span)
        sig = OperationSignature(
            decl.name,
            tuple(p.annotation for p in decl.params),
            decl.return_type,
            normalize_effects(decl.effects),
            "qop",
            f"source::{decl.name}",
            flow_policy="positional",
        )
        if self.registry.get(decl.name) is not None:
            raise NameResolutionError(f"symbol '{decl.name}' is already defined in this scope", decl.span)
        self.registry.register(sig)
        self.global_scope.define(Symbol(sig.name, sig.function_type, "qop", decl.span, sig.effects))
        self._record_decl_symbol(decl, decl.name)

    def _check_const(self, decl: ast.ConstDecl) -> None:
        if decl.annotation:
            self._ensure_supported_type(decl.annotation, decl.span)
        actual = self._expr_type(decl.value, self.global_scope, None, set())
        declared = decl.annotation or actual
        self._expect_type(declared, actual, decl.value.span)
        if declared.contains_quantum():
            raise HyQFTypeError("top-level const may not have a quantum type", decl.span)
        self.global_scope.define(Symbol(decl.name, declared, "const", decl.span))
        self._record_decl_symbol(decl, decl.name)

    def _check_function(self, decl: ast.FuncDecl) -> None:
        returns = decl.return_type or VOID
        if not decl.is_quantum:
            for param in decl.params:
                if param.annotation.contains_quantum():
                    raise HyQFTypeError("func may not accept quantum values", param.span)
            if returns.contains_quantum():
                raise HyQFTypeError("func may not return quantum values", decl.span)

        scope = self.global_scope.child()
        for param in decl.params:
            scope.define(Symbol(param.name, param.annotation, "param", param.span))
            symbol = scope.resolve(param.name, param.span)
            assert symbol.symbol_id is not None
            self.resolved_symbols[param.node_id] = symbol.symbol_id
        effects: set[Effect] = set()
        self._current_function = decl.name
        self.function_calls[decl.name] = set()
        try:
            flow = self._check_block(decl.body, scope, decl.is_quantum, returns, effects, child_scope=False)
        finally:
            self._current_function = None
        if returns != VOID and flow.exits != frozenset({FlowExit.RETURN}):
            raise HyQFTypeError(f"function '{decl.name}' must return {returns}", decl.span)
        self.direct_effects[decl.name] = effects

    def _solve_function_effects(self, declarations: list[ast.FuncDecl]) -> None:
        inferred = {
            decl.name: normalize_effects(self.direct_effects.get(decl.name, set()))
            for decl in declarations
        }
        while True:
            updated: dict[str, frozenset[Effect]] = {}
            for decl in declarations:
                combined = set(self.direct_effects.get(decl.name, set()))
                for callee in self.function_calls.get(decl.name, set()):
                    combined.update(inferred[callee])
                updated[decl.name] = normalize_effects(combined)
            if updated == inferred:
                break
            inferred = updated

        for decl in declarations:
            effects = inferred[decl.name]
            if not decl.is_quantum and effects != frozenset({Effect.PURE}):
                raise EffectError("func must be pure", decl.span)
            self.effects[decl.name] = effects
            returns = decl.return_type or VOID
            function_type = FunctionType(tuple(p.annotation for p in decl.params), returns, decl.is_quantum)
            self.global_scope.assign(
                Symbol(decl.name, function_type, "qfunc" if decl.is_quantum else "func", decl.span, effects)
            )

    def _check_ownership(
        self,
        program: ast.Program,
        declarations: list[ast.FuncDecl],
    ):
        summaries = {}
        facts = None
        recursive_functions = self._recursive_functions(declarations)
        max_passes = max(2, len(declarations) + 2)
        for _ in range(max_passes):
            checker = OwnershipChecker(
                self.global_scope,
                self.registry,
                summaries,
                recursive_functions,
            )
            facts = checker.check(program)
            updated = dict(facts.function_summaries)
            if updated == summaries:
                return facts
            summaries = updated
        raise SemanticModelError("function resource summaries did not reach a fixed point")

    def _recursive_functions(self, declarations: list[ast.FuncDecl]) -> frozenset[str]:
        names = {decl.name for decl in declarations}
        graph = {
            name: self.function_calls.get(name, set()) & names
            for name in names
        }

        def reaches_origin(origin: str, current: str, visited: set[str]) -> bool:
            for callee in graph[current]:
                if callee == origin:
                    return True
                if callee not in visited and reaches_origin(origin, callee, visited | {callee}):
                    return True
            return False

        return frozenset(
            name for name in names
            if reaches_origin(name, name, {name})
        )

    def _check_block(
        self,
        block: ast.Block,
        scope: SymbolTable,
        in_qfunc: bool | None,
        expected_return: Type,
        effects: set[Effect],
        *,
        child_scope: bool = True,
        loop_depth: int = 0,
    ) -> FlowSummary:
        local = scope.child() if child_scope else scope
        exits = frozenset({FlowExit.FALLTHROUGH})
        for stmt in block.statements:
            if FlowExit.FALLTHROUGH not in exits:
                self.flow_summaries[stmt.node_id] = FlowSummary(frozenset({FlowExit.UNREACHABLE}))
                continue
            statement_flow = self._check_stmt(stmt, local, in_qfunc, expected_return, effects, loop_depth)
            exits = (exits - {FlowExit.FALLTHROUGH}) | statement_flow.exits
        summary = FlowSummary(exits)
        self.flow_summaries[block.node_id] = summary
        return summary

    def _check_stmt(
        self,
        stmt: ast.Stmt,
        scope: SymbolTable,
        in_qfunc: bool | None,
        expected_return: Type,
        effects: set[Effect],
        loop_depth: int,
    ) -> FlowSummary:
        exits = frozenset({FlowExit.FALLTHROUGH})
        if isinstance(stmt, ast.LetStmt):
            value_type = self._expr_type(stmt.value, scope, in_qfunc, effects)
            if stmt.annotation:
                self._ensure_supported_type(stmt.annotation, stmt.span)
                self._expect_type(stmt.annotation, value_type, stmt.value.span)
                value_type = stmt.annotation
            self._bind_pattern(stmt.pattern, value_type, scope)
        elif isinstance(stmt, ast.VarStmt):
            value_type = self._expr_type(stmt.value, scope, in_qfunc, effects)
            if stmt.annotation:
                self._ensure_supported_type(stmt.annotation, stmt.span)
                self._expect_type(stmt.annotation, value_type, stmt.value.span)
                value_type = stmt.annotation
            if value_type.contains_quantum():
                raise HyQFTypeError("var is classical only", stmt.span)
            scope.define(Symbol(stmt.name, value_type, "var", stmt.span))
            symbol = scope.resolve(stmt.name, stmt.span)
            assert symbol.symbol_id is not None
            self.resolved_symbols[stmt.node_id] = symbol.symbol_id
        elif isinstance(stmt, ast.AssignmentStmt):
            if not isinstance(stmt.target, ast.IdentifierExpr):
                raise HyQFTypeError("only identifier assignment is currently supported", stmt.target.span)
            target = scope.resolve(stmt.target.name, stmt.target.span)
            assert target.symbol_id is not None
            self.resolved_symbols[stmt.target.node_id] = target.symbol_id
            self.node_types[stmt.target.node_id] = target.type
            if target.type.contains_quantum():
                raise HyQFTypeError("quantum values must use let rebinding, not assignment", stmt.target.span)
            value_type = self._expr_type(stmt.value, scope, in_qfunc, effects)
            self._expect_type(target.type, value_type, stmt.value.span)
        elif isinstance(stmt, ast.ExprStmt):
            self._expr_type(stmt.expression, scope, in_qfunc, effects)
        elif isinstance(stmt, ast.IfStmt):
            condition_type = self._expr_type(stmt.condition, scope, in_qfunc, effects)
            self._expect_type(BOOL, condition_type, stmt.condition.span)
            effects.add(Effect.CONTROL)
            then_flow = self._check_block(stmt.then_block, scope, in_qfunc, expected_return, effects, loop_depth=loop_depth)
            if stmt.else_block:
                else_flow = self._check_block(stmt.else_block, scope, in_qfunc, expected_return, effects, loop_depth=loop_depth)
            else:
                else_flow = FlowSummary(frozenset({FlowExit.FALLTHROUGH}))
            exits = then_flow.exits | else_flow.exits
        elif isinstance(stmt, ast.WhileStmt):
            condition_type = self._expr_type(stmt.condition, scope, in_qfunc, effects)
            self._expect_type(BOOL, condition_type, stmt.condition.span)
            effects.add(Effect.CONTROL)
            body_flow = self._check_block(
                stmt.body, scope, in_qfunc, expected_return, effects, loop_depth=loop_depth + 1
            )
            exits = {FlowExit.FALLTHROUGH}
            if FlowExit.RETURN in body_flow.exits:
                exits.add(FlowExit.RETURN)
            exits = frozenset(exits)
        elif isinstance(stmt, ast.ForStmt):
            raise HyQFTypeError("for-loop iterable semantics are not implemented yet", stmt.span)
        elif isinstance(stmt, ast.ReturnStmt):
            value_type = VOID if stmt.value is None else self._expr_type(stmt.value, scope, in_qfunc, effects)
            self._expect_type(expected_return, value_type, stmt.span)
            exits = frozenset({FlowExit.RETURN})
        elif isinstance(stmt, ast.BreakStmt):
            if loop_depth == 0:
                raise HyQFTypeError("break is only valid inside a loop", stmt.span)
            exits = frozenset({FlowExit.BREAK})
        elif isinstance(stmt, ast.ContinueStmt):
            if loop_depth == 0:
                raise HyQFTypeError("continue is only valid inside a loop", stmt.span)
            exits = frozenset({FlowExit.CONTINUE})
        summary = FlowSummary(exits)
        self.flow_summaries[stmt.node_id] = summary
        return summary

    def _expr_type(
        self,
        expr: ast.Expr,
        scope: SymbolTable,
        in_qfunc: bool | None,
        effects: set[Effect],
    ) -> Type:
        result = self._infer_expr_type(expr, scope, in_qfunc, effects)
        self.node_types[expr.node_id] = result
        return result

    def _infer_expr_type(
        self,
        expr: ast.Expr,
        scope: SymbolTable,
        in_qfunc: bool | None,
        effects: set[Effect],
    ) -> Type:
        if isinstance(expr, ast.LiteralExpr):
            return {"Bool": BOOL, "Int": INT, "Float": FLOAT, "Complex": COMPLEX}[expr.literal_kind]
        if isinstance(expr, ast.IdentifierExpr):
            symbol = scope.resolve(expr.name, expr.span)
            assert symbol.symbol_id is not None
            self.resolved_symbols[expr.node_id] = symbol.symbol_id
            return symbol.type
        if isinstance(expr, ast.AllocationExpr):
            if expr.quantum_type not in {QUBIT, QUMODE}:
                raise HyQFTypeError("only Qubit and Qumode allocation are currently supported", expr.span)
            if in_qfunc is False:
                raise EffectError("func may not allocate quantum resources", expr.span)
            if expr.size is not None:
                size_type = self._expr_type(expr.size, scope, in_qfunc, effects)
                self._expect_type(INT, size_type, expr.size.span)
                result = ARRAY(expr.quantum_type)
            else:
                result = expr.quantum_type
            effects.add(Effect.ALLOCATION)
            return result
        if isinstance(expr, ast.TupleExpr):
            return TupleType(tuple(self._expr_type(e, scope, in_qfunc, effects) for e in expr.elements))
        if isinstance(expr, ast.ArrayExpr):
            if not expr.elements:
                raise HyQFTypeError("empty array literals require an annotation in a later phase", expr.span)
            first = self._expr_type(expr.elements[0], scope, in_qfunc, effects)
            for element in expr.elements[1:]:
                self._expect_type(first, self._expr_type(element, scope, in_qfunc, effects), element.span)
            return ARRAY(first)
        if isinstance(expr, ast.IndexExpr):
            base = self._expr_type(expr.base, scope, in_qfunc, effects)
            index = self._expr_type(expr.index, scope, in_qfunc, effects)
            self._expect_type(INT, index, expr.index.span)
            if not isinstance(base, ArrayType):
                raise HyQFTypeError("indexing requires an array", expr.base.span, actual=base)
            return base.element
        if isinstance(expr, ast.UnaryExpr):
            operand = self._expr_type(expr.operand, scope, in_qfunc, effects)
            if expr.operator == "!":
                self._expect_type(BOOL, operand, expr.operand.span)
                return BOOL
            if expr.operator in {"+", "-"}:
                if not is_numeric(operand) or operand == BIT:
                    raise HyQFTypeError("numeric unary operator requires Int, UInt, Float, or Complex", expr.span, actual=operand)
                return operand
            if expr.operator in {"adjoint", "controlled"}:
                raise HyQFTypeError(f"{expr.operator} is Phase F and is not implemented yet", expr.span)
        if isinstance(expr, ast.BinaryExpr):
            return self._binary_type(expr, scope, in_qfunc, effects)
        if isinstance(expr, ast.CallExpr):
            return self._call_type(expr, scope, in_qfunc, effects)
        if isinstance(expr, (ast.ComposeExpr, ast.RepeatExpr)):
            raise HyQFTypeError("function transforms are Phase F and are not implemented yet", expr.span)
        raise HyQFTypeError(f"unsupported expression {type(expr).__name__}", expr.span)

    def _binary_type(self, expr: ast.BinaryExpr, scope: SymbolTable, in_qfunc: bool | None, effects: set[Effect]) -> Type:
        left = self._expr_type(expr.left, scope, in_qfunc, effects)
        right = self._expr_type(expr.right, scope, in_qfunc, effects)
        op = expr.operator
        if op in {"&&", "||"}:
            self._expect_type(BOOL, left, expr.left.span)
            self._expect_type(BOOL, right, expr.right.span)
            return BOOL
        if op in {"&", "|", "^"}:
            if left == BIT and right == BIT:
                return BIT
            if left == INT and right == INT:
                return INT
            raise HyQFTypeError("bitwise operators require Bit/Bit or Int/Int operands", expr.span, expected="Bit or Int", actual=f"{left}, {right}")
        if op in {"==", "!="}:
            if left.contains_quantum() or right.contains_quantum():
                raise HyQFTypeError("quantum values do not support equality operators", expr.span)
            self._expect_type(left, right, expr.right.span)
            return BOOL
        if op in {"<", "<=", ">", ">="}:
            promoted = promote_numeric(left, right)
            if promoted is None or promoted == COMPLEX:
                raise HyQFTypeError("comparison requires non-complex numeric operands", expr.span)
            return BOOL
        if op in {"+", "-", "*", "/", "%", "**"}:
            promoted = promote_numeric(left, right)
            if promoted is None or BIT in {left, right}:
                raise HyQFTypeError("arithmetic requires Int, UInt, Float, or Complex operands", expr.span)
            return FLOAT if op == "/" and promoted in {INT, UINT} else promoted
        raise HyQFTypeError(f"unknown binary operator {op}", expr.span)

    def _call_type(self, expr: ast.CallExpr, scope: SymbolTable, in_qfunc: bool | None, effects: set[Effect]) -> Type:
        if not isinstance(expr.callee, ast.IdentifierExpr):
            raise HyQFTypeError("only named calls are currently supported", expr.callee.span)
        symbol = scope.resolve(expr.callee.name, expr.callee.span)
        assert symbol.symbol_id is not None
        self.resolved_symbols[expr.callee.node_id] = symbol.symbol_id
        self.node_types[expr.callee.node_id] = symbol.type
        if not isinstance(symbol.type, FunctionType):
            raise HyQFTypeError("called value is not a function", expr.callee.span, actual=symbol.type)
        if symbol.kind in {"qop", "qfunc"} and in_qfunc is False:
            raise EffectError("func may not call qop or qfunc", expr.span)
        if len(expr.arguments) != len(symbol.type.params):
            raise HyQFTypeError("wrong argument count", expr.span, expected=len(symbol.type.params), actual=len(expr.arguments))
        for expected, arg in zip(symbol.type.params, expr.arguments):
            actual = self._expr_type(arg, scope, in_qfunc, effects)
            self._expect_type(expected, actual, arg.span)
        signature = self.registry.get(symbol.name) or self.classical_functions.get(symbol.name)
        if signature is not None:
            effects.update(signature.effects)
        elif symbol.kind in {"func", "qfunc"} and self._current_function is not None:
            self.function_calls[self._current_function].add(symbol.name)
        else:
            effects.update(symbol.effects)
        self.call_targets[expr.node_id] = (
            signature.callable_id
            if signature is not None
            else CallableId(f"source::{symbol.name}", "1.0")
        )
        return symbol.type.returns

    def _bind_pattern(self, pattern: ast.Pattern, value_type: Type, scope: SymbolTable) -> None:
        if isinstance(pattern, ast.IdentifierPattern):
            scope.rebind_local(Symbol(pattern.name, value_type, "let", pattern.span))
            symbol = scope.resolve(pattern.name, pattern.span)
            assert symbol.symbol_id is not None
            self.resolved_symbols[pattern.node_id] = symbol.symbol_id
            return
        if isinstance(pattern, ast.IndexPattern):
            self._bind_index_patterns([(pattern, value_type)], scope)
            return
        if isinstance(pattern, ast.WildcardPattern):
            return
        if isinstance(pattern, ast.TuplePattern):
            if not isinstance(value_type, TupleType):
                raise HyQFTypeError("tuple pattern requires tuple value", pattern.span, actual=value_type)
            if len(pattern.elements) != len(value_type.elements):
                raise HyQFTypeError("tuple pattern arity mismatch", pattern.span, expected=len(pattern.elements), actual=len(value_type.elements))
            leaves: list[tuple[ast.Pattern, Type]] = []
            for subpattern, subtype in zip(pattern.elements, value_type.elements):
                self._collect_typed_pattern_leaves(subpattern, subtype, leaves)
            updates: dict[str, list[tuple[ast.IndexPattern, Type]]] = {}
            ordinary: list[tuple[ast.Pattern, Type]] = []
            for subpattern, subtype in leaves:
                if isinstance(subpattern, ast.IndexPattern):
                    updates.setdefault(subpattern.name, []).append((subpattern, subtype))
                else:
                    ordinary.append((subpattern, subtype))
            for group in updates.values():
                self._bind_index_patterns(group, scope)
            for subpattern, subtype in ordinary:
                self._bind_pattern(subpattern, subtype, scope)
            return
        raise HyQFTypeError("unknown pattern", pattern.span)

    def _collect_typed_pattern_leaves(
        self,
        pattern: ast.Pattern,
        value_type: Type,
        leaves: list[tuple[ast.Pattern, Type]],
    ) -> None:
        if not isinstance(pattern, ast.TuplePattern):
            leaves.append((pattern, value_type))
            return
        assert isinstance(value_type, TupleType)
        for subpattern, subtype in zip(pattern.elements, value_type.elements):
            self._collect_typed_pattern_leaves(subpattern, subtype, leaves)

    def _bind_index_patterns(
        self,
        updates: list[tuple[ast.IndexPattern, Type]],
        scope: SymbolTable,
    ) -> None:
        first = updates[0][0]
        target = scope.resolve(first.name, first.span)
        if not isinstance(target.type, ArrayType):
            raise HyQFTypeError(
                "indexed let target requires an array binding",
                first.span,
                actual=target.type,
            )
        if not target.type.contains_quantum():
            raise HyQFTypeError(
                "indexed let rebinding is only supported for linear quantum arrays",
                first.span,
                actual=target.type,
            )
        for pattern, value_type in updates:
            index_type = self._expr_type(pattern.index, scope, None, set())
            self._expect_type(INT, index_type, pattern.index.span)
            if self._static_int(pattern.index) is None:
                raise HyQFTypeError(
                    "quantum array update requires a compile-time integer index",
                    pattern.index.span,
                )
            self._expect_type(target.type.element, value_type, pattern.span)
        scope.rebind_local(Symbol(first.name, target.type, "let", first.span))
        symbol = scope.resolve(first.name, first.span)
        assert symbol.symbol_id is not None
        for pattern, _ in updates:
            self.resolved_symbols[pattern.node_id] = symbol.symbol_id

    def _static_int(self, expression: ast.Expr) -> int | None:
        if isinstance(expression, ast.LiteralExpr) and expression.literal_kind == "Int":
            return expression.value
        if isinstance(expression, ast.UnaryExpr) and expression.operator in {"+", "-"}:
            operand = self._static_int(expression.operand)
            if operand is not None:
                return operand if expression.operator == "+" else -operand
        return None

    def _record_decl_symbol(self, declaration: ast.Node, name: str) -> None:
        symbol = self.global_scope.resolve(name, declaration.span)
        assert symbol.symbol_id is not None
        self.resolved_symbols[declaration.node_id] = symbol.symbol_id

    def _expect_type(self, expected: Type, actual: Type, span) -> None:
        if expected != actual:
            raise HyQFTypeError("type mismatch", span, expected=expected, actual=actual)

    def _ensure_supported_type(self, typ: Type, span) -> None:
        try:
            ensure_supported_type(typ)
        except HyQFTypeError as exc:
            if exc.span is not None:
                raise
            raise HyQFTypeError(exc.message, span, expected=exc.expected, actual=exc.actual) from exc
