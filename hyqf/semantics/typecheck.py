from __future__ import annotations

from dataclasses import dataclass

from hyqf.diagnostics.errors import EffectError, HyQFTypeError, NameResolutionError, VerificationUnsupported
from hyqf.frontend import ast
from hyqf.semantics.effects import Effect, normalize_effects
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


class TypeChecker:
    def __init__(self, registry: QopRegistry | None = None):
        self.registry = registry or standard_registry()
        self.global_scope = SymbolTable()
        self.effects: dict[str, frozenset[Effect]] = {}
        for sig in self.registry.signatures().values():
            self.global_scope.define(Symbol(sig.name, sig.function_type, sig.kind, effects=sig.effects))
        for sig in standard_classical_functions().values():
            self.global_scope.define(Symbol(sig.name, sig.function_type, sig.kind, effects=sig.effects))

    def check(self, program: ast.Program) -> CheckedModule:
        for decl in program.declarations:
            if isinstance(decl, ast.QopDecl):
                self._register_qop(decl)
        for decl in program.declarations:
            if isinstance(decl, ast.SymbolDecl):
                self._ensure_supported_type(decl.annotation, decl.span)
                self.global_scope.define(Symbol(decl.name, decl.annotation, "symbol", decl.span))
            elif isinstance(decl, ast.ConstDecl):
                pass
            elif isinstance(decl, ast.FuncDecl):
                returns = decl.return_type or VOID
                self._ensure_supported_type(returns, decl.span)
                for param in decl.params:
                    self._ensure_supported_type(param.annotation, param.span)
                ftype = FunctionType(tuple(p.annotation for p in decl.params), returns, decl.is_quantum)
                self.global_scope.define(Symbol(decl.name, ftype, "qfunc" if decl.is_quantum else "func", decl.span))

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
                raise VerificationUnsupported("verification is Phase G and is not implemented in Phase A/B", decl.span)
        for decl in function_decls:
            self._check_function(decl)
        return CheckedModule(self.global_scope, dict(self.effects))

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
        )
        self.registry.register(sig)
        self.global_scope.define(Symbol(sig.name, sig.function_type, "qop", decl.span, sig.effects))

    def _check_const(self, decl: ast.ConstDecl) -> None:
        if decl.annotation:
            self._ensure_supported_type(decl.annotation, decl.span)
        actual = self._expr_type(decl.value, self.global_scope, None, set())
        declared = decl.annotation or actual
        self._expect_type(declared, actual, decl.value.span)
        if declared.contains_quantum():
            raise HyQFTypeError("top-level const may not have a quantum type", decl.span)
        self.global_scope.define(Symbol(decl.name, declared, "const", decl.span))

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
        effects: set[Effect] = set()
        saw_return = self._check_block(decl.body, scope, decl.is_quantum, returns, effects, child_scope=False)
        if returns != VOID and not saw_return:
            raise HyQFTypeError(f"function '{decl.name}' must return {returns}", decl.span)
        inferred = normalize_effects(effects)
        if not decl.is_quantum and inferred != frozenset({Effect.PURE}):
            raise EffectError("func must be pure", decl.span)
        self.effects[decl.name] = inferred
        ftype = FunctionType(tuple(p.annotation for p in decl.params), returns, decl.is_quantum)
        self.global_scope.assign(Symbol(decl.name, ftype, "qfunc" if decl.is_quantum else "func", decl.span, inferred))

    def _check_block(
        self,
        block: ast.Block,
        scope: SymbolTable,
        in_qfunc: bool | None,
        expected_return: Type,
        effects: set[Effect],
        *,
        child_scope: bool = True,
    ) -> bool:
        local = scope.child() if child_scope else scope
        saw_return = False
        for stmt in block.statements:
            if saw_return:
                continue
            saw_return = self._check_stmt(stmt, local, in_qfunc, expected_return, effects)
        return saw_return

    def _check_stmt(
        self,
        stmt: ast.Stmt,
        scope: SymbolTable,
        in_qfunc: bool | None,
        expected_return: Type,
        effects: set[Effect],
    ) -> bool:
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
        elif isinstance(stmt, ast.AssignmentStmt):
            if not isinstance(stmt.target, ast.IdentifierExpr):
                raise HyQFTypeError("only identifier assignment is supported in Phase A/B", stmt.target.span)
            target = scope.resolve(stmt.target.name, stmt.target.span)
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
            self._check_block(stmt.then_block, scope, in_qfunc, expected_return, effects)
            if stmt.else_block:
                self._check_block(stmt.else_block, scope, in_qfunc, expected_return, effects)
        elif isinstance(stmt, ast.WhileStmt):
            condition_type = self._expr_type(stmt.condition, scope, in_qfunc, effects)
            self._expect_type(BOOL, condition_type, stmt.condition.span)
            effects.add(Effect.CONTROL)
            self._check_block(stmt.body, scope, in_qfunc, expected_return, effects)
        elif isinstance(stmt, ast.ForStmt):
            raise HyQFTypeError("for-loop iterable semantics are not implemented in Phase A/B", stmt.span)
        elif isinstance(stmt, ast.ReturnStmt):
            value_type = VOID if stmt.value is None else self._expr_type(stmt.value, scope, in_qfunc, effects)
            self._expect_type(expected_return, value_type, stmt.span)
            return True
        return False

    def _expr_type(
        self,
        expr: ast.Expr,
        scope: SymbolTable,
        in_qfunc: bool | None,
        effects: set[Effect],
    ) -> Type:
        if isinstance(expr, ast.LiteralExpr):
            return {"Bool": BOOL, "Int": INT, "Float": FLOAT, "Complex": COMPLEX}[expr.literal_kind]
        if isinstance(expr, ast.IdentifierExpr):
            return scope.resolve(expr.name, expr.span).type
        if isinstance(expr, ast.AllocationExpr):
            if expr.quantum_type not in {QUBIT, QUMODE}:
                raise HyQFTypeError("only Qubit and Qumode allocation are supported in Phase A/B", expr.span)
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
                raise HyQFTypeError(f"{expr.operator} is Phase F and is not implemented in Phase A/B", expr.span)
        if isinstance(expr, ast.BinaryExpr):
            return self._binary_type(expr, scope, in_qfunc, effects)
        if isinstance(expr, ast.CallExpr):
            return self._call_type(expr, scope, in_qfunc, effects)
        if isinstance(expr, (ast.ComposeExpr, ast.RepeatExpr)):
            raise HyQFTypeError("function transforms are Phase F and are not implemented in Phase A/B", expr.span)
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
            raise HyQFTypeError("only named calls are supported in Phase A/B", expr.callee.span)
        symbol = scope.resolve(expr.callee.name, expr.callee.span)
        if not isinstance(symbol.type, FunctionType):
            raise HyQFTypeError("called value is not a function", expr.callee.span, actual=symbol.type)
        if symbol.kind in {"qop", "qfunc"} and in_qfunc is False:
            raise EffectError("func may not call qop or qfunc", expr.span)
        if len(expr.arguments) != len(symbol.type.params):
            raise HyQFTypeError("wrong argument count", expr.span, expected=len(symbol.type.params), actual=len(expr.arguments))
        for expected, arg in zip(symbol.type.params, expr.arguments):
            actual = self._expr_type(arg, scope, in_qfunc, effects)
            self._expect_type(expected, actual, arg.span)
        effects.update(symbol.effects)
        return symbol.type.returns

    def _bind_pattern(self, pattern: ast.Pattern, value_type: Type, scope: SymbolTable) -> None:
        if isinstance(pattern, ast.IdentifierPattern):
            scope.rebind_local(Symbol(pattern.name, value_type, "let", pattern.span))
            return
        if isinstance(pattern, ast.WildcardPattern):
            return
        if isinstance(pattern, ast.TuplePattern):
            if not isinstance(value_type, TupleType):
                raise HyQFTypeError("tuple pattern requires tuple value", pattern.span, actual=value_type)
            if len(pattern.elements) != len(value_type.elements):
                raise HyQFTypeError("tuple pattern arity mismatch", pattern.span, expected=len(pattern.elements), actual=len(value_type.elements))
            for subpattern, subtype in zip(pattern.elements, value_type.elements):
                self._bind_pattern(subpattern, subtype, scope)
            return
        raise HyQFTypeError("unknown pattern", pattern.span)

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
