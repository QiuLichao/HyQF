from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hyqf.frontend.spans import SourceSpan
from hyqf.semantics.effects import Effect
from hyqf.semantics.types import Type


@dataclass(frozen=True)
class Node:
    span: SourceSpan


@dataclass(frozen=True)
class Program(Node):
    declarations: list["TopLevelDecl"]


class TopLevelDecl(Node):
    pass


@dataclass(frozen=True)
class ModuleDecl(TopLevelDecl):
    name: str


@dataclass(frozen=True)
class ImportDecl(TopLevelDecl):
    name: str
    wildcard: bool
    alias: str | None


@dataclass(frozen=True)
class ConstDecl(TopLevelDecl):
    name: str
    annotation: Type | None
    value: "Expr"


@dataclass(frozen=True)
class SymbolDecl(TopLevelDecl):
    name: str
    annotation: Type
    constraint: "Expr | None"


@dataclass(frozen=True)
class Parameter(Node):
    name: str
    annotation: Type


@dataclass(frozen=True)
class FuncDecl(TopLevelDecl):
    name: str
    params: list[Parameter]
    return_type: Type | None
    body: "Block"
    is_quantum: bool


@dataclass(frozen=True)
class QopDecl(TopLevelDecl):
    name: str
    params: list[Parameter]
    return_type: Type
    effects: frozenset[Effect]


@dataclass(frozen=True)
class VerifyDecl(TopLevelDecl):
    left: "Expr"
    right: "Expr"
    modulo: str | None


class Stmt(Node):
    pass


@dataclass(frozen=True)
class Block(Node):
    statements: list[Stmt]


class Pattern(Node):
    pass


@dataclass(frozen=True)
class IdentifierPattern(Pattern):
    name: str


@dataclass(frozen=True)
class WildcardPattern(Pattern):
    pass


@dataclass(frozen=True)
class TuplePattern(Pattern):
    elements: list[Pattern]


@dataclass(frozen=True)
class LetStmt(Stmt):
    pattern: Pattern
    annotation: Type | None
    value: "Expr"


@dataclass(frozen=True)
class VarStmt(Stmt):
    name: str
    annotation: Type | None
    value: "Expr"


@dataclass(frozen=True)
class AssignmentStmt(Stmt):
    target: "Expr"
    value: "Expr"


@dataclass(frozen=True)
class ExprStmt(Stmt):
    expression: "Expr"


@dataclass(frozen=True)
class IfStmt(Stmt):
    condition: "Expr"
    then_block: Block
    else_block: Block | None


@dataclass(frozen=True)
class WhileStmt(Stmt):
    condition: "Expr"
    body: Block


@dataclass(frozen=True)
class ForStmt(Stmt):
    name: str
    iterable: "Expr"
    body: Block


@dataclass(frozen=True)
class ReturnStmt(Stmt):
    value: "Expr | None"


@dataclass(frozen=True)
class BreakStmt(Stmt):
    pass


@dataclass(frozen=True)
class ContinueStmt(Stmt):
    pass


class Expr(Node):
    pass


@dataclass(frozen=True)
class LiteralExpr(Expr):
    value: Any
    literal_kind: str


@dataclass(frozen=True)
class IdentifierExpr(Expr):
    name: str


@dataclass(frozen=True)
class BinaryExpr(Expr):
    left: Expr
    operator: str
    right: Expr


@dataclass(frozen=True)
class UnaryExpr(Expr):
    operator: str
    operand: Expr


@dataclass(frozen=True)
class CallExpr(Expr):
    callee: Expr
    arguments: list[Expr]


@dataclass(frozen=True)
class IndexExpr(Expr):
    base: Expr
    index: Expr


@dataclass(frozen=True)
class AllocationExpr(Expr):
    quantum_type: Type
    size: Expr | None


@dataclass(frozen=True)
class TupleExpr(Expr):
    elements: list[Expr]


@dataclass(frozen=True)
class ArrayExpr(Expr):
    elements: list[Expr]


@dataclass(frozen=True)
class ComposeExpr(Expr):
    functions: list[Expr]


@dataclass(frozen=True)
class RepeatExpr(Expr):
    count: Expr
    function: Expr
