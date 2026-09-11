from __future__ import annotations

from hyqf.diagnostics.errors import ParseError
from hyqf.frontend import ast
from hyqf.frontend.spans import SourceSpan
from hyqf.frontend.tokens import Lexer, Token
from hyqf.semantics.effects import Effect
from hyqf.semantics.types import (
    CLASSICAL_TYPES,
    QUANTUM_TYPES,
    ArrayType,
    FunctionType,
    QuditType,
    TupleType,
    Type,
)


def parse_source(source: str, file: str = "<string>") -> ast.Program:
    return Parser(Lexer(source, file).tokenize()).parse_program()


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def parse_program(self) -> ast.Program:
        declarations: list[ast.TopLevelDecl] = []
        start = self._peek().span
        while not self._check("EOF"):
            declarations.append(self._top_level_decl())
        end = self._peek().span
        return ast.Program(start.merge(end), declarations)

    def _top_level_decl(self) -> ast.TopLevelDecl:
        if self._match_kw("module"):
            start = self._previous().span
            name = self._qualified_name()
            end = self._expect_sym(";").span
            return ast.ModuleDecl(start.merge(end), name)
        if self._match_kw("import"):
            start = self._previous().span
            name = self._qualified_name()
            wildcard = False
            if self._match_sym("."):
                self._expect_sym("*")
                wildcard = True
            alias = None
            if self._match_kw("as"):
                alias = self._expect_ident().value
            end = self._expect_sym(";").span
            return ast.ImportDecl(start.merge(end), name, wildcard, alias)
        if self._match_kw("const"):
            return self._const_decl(self._previous().span)
        if self._match_kw("symbol"):
            return self._symbol_decl(self._previous().span)
        if self._match_kw("func"):
            return self._function_decl(self._previous().span, False)
        if self._match_kw("qfunc"):
            return self._function_decl(self._previous().span, True)
        if self._match_kw("qop"):
            return self._qop_decl(self._previous().span)
        if self._match_kw("verify"):
            return self._verify_decl(self._previous().span)
        raise self._error("expected top-level declaration")

    def _const_decl(self, start: SourceSpan) -> ast.ConstDecl:
        name = self._expect_ident().value
        annotation = self._type() if self._match_sym(":") else None
        self._expect_sym("=")
        value = self._expression()
        end = self._expect_sym(";").span
        return ast.ConstDecl(start.merge(end), name, annotation, value)

    def _symbol_decl(self, start: SourceSpan) -> ast.SymbolDecl:
        name = self._expect_ident().value
        self._expect_sym(":")
        annotation = self._type()
        constraint = None
        if self._match_kw("where"):
            constraint = self._expression()
        end = self._expect_sym(";").span
        return ast.SymbolDecl(start.merge(end), name, annotation, constraint)

    def _function_decl(self, start: SourceSpan, is_quantum: bool) -> ast.FuncDecl:
        name = self._expect_ident().value
        self._expect_sym("(")
        params = [] if self._check_sym(")") else self._parameter_list()
        self._expect_sym(")")
        return_type = self._type() if self._match_sym("->") else None
        body = self._block()
        return ast.FuncDecl(start.merge(body.span), name, params, return_type, body, is_quantum)

    def _qop_decl(self, start: SourceSpan) -> ast.QopDecl:
        name = self._expect_ident().value
        self._expect_sym("(")
        params = [] if self._check_sym(")") else self._parameter_list()
        self._expect_sym(")")
        self._expect_sym("->")
        return_type = self._type()
        self._expect_kw("effects")
        self._expect_sym("{")
        effects = {self._effect()}
        while self._match_sym(","):
            effects.add(self._effect())
        self._expect_sym("}")
        end = self._expect_sym(";").span
        return ast.QopDecl(start.merge(end), name, params, return_type, frozenset(effects))

    def _verify_decl(self, start: SourceSpan) -> ast.VerifyDecl:
        left = self._expression()
        self._expect_kw("equiv")
        right = self._expression()
        modulo = None
        if self._match_kw("modulo"):
            token = self._expect_any_kw({"exact", "global_phase"})
            modulo = token.value
        end = self._expect_sym(";").span
        return ast.VerifyDecl(start.merge(end), left, right, modulo)

    def _parameter_list(self) -> list[ast.Parameter]:
        params = [self._parameter()]
        while self._match_sym(","):
            params.append(self._parameter())
        return params

    def _parameter(self) -> ast.Parameter:
        name = self._expect_ident()
        self._expect_sym(":")
        annotation = self._type()
        return ast.Parameter(name.span, name.value, annotation)

    def _block(self) -> ast.Block:
        start = self._expect_sym("{").span
        statements: list[ast.Stmt] = []
        while not self._check_sym("}"):
            if self._check("EOF"):
                raise self._error("unterminated block")
            statements.append(self._statement())
        end = self._expect_sym("}").span
        return ast.Block(start.merge(end), statements)

    def _statement(self) -> ast.Stmt:
        if self._match_kw("let"):
            start = self._previous().span
            pattern = self._pattern()
            annotation = self._type() if self._match_sym(":") else None
            self._expect_sym("=")
            value = self._expression()
            end = self._expect_sym(";").span
            return ast.LetStmt(start.merge(end), pattern, annotation, value)
        if self._match_kw("var"):
            start = self._previous().span
            name = self._expect_ident()
            annotation = self._type() if self._match_sym(":") else None
            self._expect_sym("=")
            value = self._expression()
            end = self._expect_sym(";").span
            return ast.VarStmt(start.merge(end), name.value, annotation, value)
        if self._match_kw("if"):
            start = self._previous().span
            self._expect_sym("(")
            condition = self._expression()
            self._expect_sym(")")
            then_block = self._block()
            else_block = self._block() if self._match_kw("else") else None
            return ast.IfStmt(start.merge((else_block or then_block).span), condition, then_block, else_block)
        if self._match_kw("while"):
            start = self._previous().span
            self._expect_sym("(")
            condition = self._expression()
            self._expect_sym(")")
            body = self._block()
            return ast.WhileStmt(start.merge(body.span), condition, body)
        if self._match_kw("for"):
            start = self._previous().span
            name = self._expect_ident().value
            self._expect_kw("in")
            iterable = self._expression()
            body = self._block()
            return ast.ForStmt(start.merge(body.span), name, iterable, body)
        if self._match_kw("return"):
            start = self._previous().span
            value = None if self._check_sym(";") else self._expression()
            end = self._expect_sym(";").span
            return ast.ReturnStmt(start.merge(end), value)
        if self._match_kw("break"):
            end = self._expect_sym(";").span
            return ast.BreakStmt(self._previous().span.merge(end))
        if self._match_kw("continue"):
            end = self._expect_sym(";").span
            return ast.ContinueStmt(self._previous().span.merge(end))

        expression = self._expression()
        if self._match_sym("="):
            value = self._expression()
            end = self._expect_sym(";").span
            return ast.AssignmentStmt(expression.span.merge(end), expression, value)
        end = self._expect_sym(";").span
        return ast.ExprStmt(expression.span.merge(end), expression)

    def _pattern(self) -> ast.Pattern:
        if self._peek().kind == "IDENT" and self._peek().value == "_":
            self.pos += 1
            return ast.WildcardPattern(self._previous().span)
        if self._match_ident():
            token = self._previous()
            return ast.IdentifierPattern(token.span, token.value)
        if self._match_sym("_"):
            return ast.WildcardPattern(self._previous().span)
        if self._match_sym("("):
            start = self._previous().span
            elements = [self._pattern()]
            while self._match_sym(","):
                elements.append(self._pattern())
            end = self._expect_sym(")").span
            return ast.TuplePattern(start.merge(end), elements)
        raise self._error("expected pattern")

    def _expression(self) -> ast.Expr:
        return self._logical_or()

    def _logical_or(self) -> ast.Expr:
        expr = self._logical_and()
        while self._match_sym("||"):
            op = self._previous()
            rhs = self._logical_and()
            expr = ast.BinaryExpr(expr.span.merge(rhs.span), expr, op.value, rhs)
        return expr

    def _logical_and(self) -> ast.Expr:
        expr = self._bit_or()
        while self._match_sym("&&"):
            op = self._previous()
            rhs = self._bit_or()
            expr = ast.BinaryExpr(expr.span.merge(rhs.span), expr, op.value, rhs)
        return expr

    def _bit_or(self) -> ast.Expr:
        return self._left_assoc(self._bit_xor, {"|"})

    def _bit_xor(self) -> ast.Expr:
        return self._left_assoc(self._bit_and, {"^"})

    def _bit_and(self) -> ast.Expr:
        return self._left_assoc(self._equality, {"&"})

    def _equality(self) -> ast.Expr:
        return self._left_assoc(self._comparison, {"==", "!="})

    def _comparison(self) -> ast.Expr:
        return self._left_assoc(self._additive, {"<", "<=", ">", ">="})

    def _additive(self) -> ast.Expr:
        return self._left_assoc(self._multiplicative, {"+", "-"})

    def _multiplicative(self) -> ast.Expr:
        return self._left_assoc(self._power, {"*", "/", "%"})

    def _power(self) -> ast.Expr:
        expr = self._unary()
        if self._match_sym("**"):
            op = self._previous()
            rhs = self._power()
            return ast.BinaryExpr(expr.span.merge(rhs.span), expr, op.value, rhs)
        return expr

    def _unary(self) -> ast.Expr:
        if self._match_sym("!", "+", "-") or self._match_kw("adjoint", "controlled"):
            op = self._previous()
            operand = self._unary()
            return ast.UnaryExpr(op.span.merge(operand.span), op.value, operand)
        return self._postfix()

    def _postfix(self) -> ast.Expr:
        expr = self._primary()
        while True:
            if self._match_sym("("):
                args = [] if self._check_sym(")") else self._argument_list()
                end = self._expect_sym(")").span
                expr = ast.CallExpr(expr.span.merge(end), expr, args)
                continue
            if self._match_sym("["):
                index = self._expression()
                end = self._expect_sym("]").span
                expr = ast.IndexExpr(expr.span.merge(end), expr, index)
                continue
            break
        return expr

    def _primary(self) -> ast.Expr:
        if self._match("INT"):
            t = self._previous()
            return ast.LiteralExpr(t.span, int(t.value), "Int")
        if self._match("FLOAT"):
            t = self._previous()
            return ast.LiteralExpr(t.span, float(t.value), "Float")
        if self._match("COMPLEX"):
            t = self._previous()
            return ast.LiteralExpr(t.span, complex(t.value), "Complex")
        if self._match_kw("true", "false"):
            t = self._previous()
            return ast.LiteralExpr(t.span, t.value == "true", "Bool")
        if self._match_ident():
            t = self._previous()
            return ast.IdentifierExpr(t.span, t.value)
        if self._match_kw("new"):
            start = self._previous().span
            qtype = self._quantum_type()
            size = None
            if self._match_sym("["):
                size = self._expression()
                end = self._expect_sym("]").span
                return ast.AllocationExpr(start.merge(end), qtype, size)
            return ast.AllocationExpr(start.merge(self._previous().span), qtype, size)
        if self._match_kw("compose"):
            start = self._previous().span
            self._expect_sym("(")
            functions = [] if self._check_sym(")") else self._argument_list()
            end = self._expect_sym(")").span
            return ast.ComposeExpr(start.merge(end), functions)
        if self._match_kw("repeat"):
            start = self._previous().span
            self._expect_sym("(")
            count = self._expression()
            self._expect_sym(",")
            function = self._expression()
            end = self._expect_sym(")").span
            return ast.RepeatExpr(start.merge(end), count, function)
        if self._match_sym("["):
            start = self._previous().span
            elements = [] if self._check_sym("]") else self._argument_list()
            end = self._expect_sym("]").span
            return ast.ArrayExpr(start.merge(end), elements)
        if self._match_sym("("):
            start = self._previous().span
            first = self._expression()
            if self._match_sym(","):
                elements = [first, self._expression()]
                while self._match_sym(","):
                    elements.append(self._expression())
                end = self._expect_sym(")").span
                return ast.TupleExpr(start.merge(end), elements)
            self._expect_sym(")")
            return first
        raise self._error("expected expression")

    def _argument_list(self) -> list[ast.Expr]:
        args = [self._expression()]
        while self._match_sym(","):
            args.append(self._expression())
        return args

    def _type(self) -> Type:
        if self._match_kw("fn", "qfn"):
            token = self._previous()
            is_quantum = token.value == "qfn"
            self._expect_sym("(")
            params = [] if self._check_sym(")") else self._type_list()
            self._expect_sym(")")
            self._expect_sym("->")
            returns = self._type()
            return FunctionType(tuple(params), returns, is_quantum)
        if self._match_kw("Array"):
            self._expect_sym("<")
            element = self._type()
            self._expect_sym(">")
            return ArrayType(element)
        if self._check_kw("Qubit", "Qumode", "Qudit"):
            return self._quantum_type()
        if self._match_kw(*CLASSICAL_TYPES.keys()):
            return CLASSICAL_TYPES[self._previous().value]
        if self._match_sym("("):
            first = self._type()
            self._expect_sym(",")
            elements = [first, self._type()]
            while self._match_sym(","):
                elements.append(self._type())
            self._expect_sym(")")
            return TupleType(tuple(elements))
        raise self._error("expected type")

    def _type_list(self) -> list[Type]:
        types = [self._type()]
        while self._match_sym(","):
            types.append(self._type())
        return types

    def _quantum_type(self) -> Type:
        if self._match_kw("Qubit", "Qumode"):
            return QUANTUM_TYPES[self._previous().value]
        if self._match_kw("Qudit"):
            self._expect_sym("<")
            dimension = int(self._expect("INT").value)
            self._expect_sym(">")
            return QuditType(dimension)
        raise self._error("expected quantum type")

    def _qualified_name(self) -> str:
        parts = [self._expect_ident().value]
        while self._peek().kind == "SYM" and self._peek().value == "." and self.tokens[self.pos + 1].kind == "IDENT":
            self.pos += 1
            parts.append(self._expect_ident().value)
        return ".".join(parts)

    def _effect(self) -> Effect:
        token = self._expect_any_kw({e.value for e in Effect})
        return Effect(token.value)

    def _left_assoc(self, parser, operators: set[str]) -> ast.Expr:
        expr = parser()
        while self._match_sym(*operators):
            op = self._previous()
            rhs = parser()
            expr = ast.BinaryExpr(expr.span.merge(rhs.span), expr, op.value, rhs)
        return expr

    def _match(self, *kinds: str) -> bool:
        if self._peek().kind in kinds:
            self.pos += 1
            return True
        return False

    def _match_ident(self) -> bool:
        if self._peek().kind == "IDENT":
            self.pos += 1
            return True
        return False

    def _match_kw(self, *values: str) -> bool:
        if self._peek().kind == "KW" and self._peek().value in values:
            self.pos += 1
            return True
        return False

    def _match_sym(self, *values: str) -> bool:
        if self._peek().kind == "SYM" and self._peek().value in values:
            self.pos += 1
            return True
        return False

    def _check(self, kind: str) -> bool:
        return self._peek().kind == kind

    def _check_kw(self, *values: str) -> bool:
        return self._peek().kind == "KW" and self._peek().value in values

    def _check_sym(self, value: str) -> bool:
        return self._peek().kind == "SYM" and self._peek().value == value

    def _expect(self, kind: str) -> Token:
        if self._match(kind):
            return self._previous()
        raise self._error(f"expected {kind}")

    def _expect_ident(self) -> Token:
        if self._match_ident():
            return self._previous()
        raise self._error("expected identifier")

    def _expect_kw(self, value: str) -> Token:
        if self._match_kw(value):
            return self._previous()
        raise self._error(f"expected '{value}'")

    def _expect_any_kw(self, values: set[str]) -> Token:
        if self._peek().kind == "KW" and self._peek().value in values:
            self.pos += 1
            return self._previous()
        raise self._error(f"expected one of {sorted(values)}")

    def _expect_sym(self, value: str) -> Token:
        if self._match_sym(value):
            return self._previous()
        raise self._error(f"expected '{value}'")

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _previous(self) -> Token:
        return self.tokens[self.pos - 1]

    def _error(self, message: str) -> ParseError:
        return ParseError(message, self._peek().span)
