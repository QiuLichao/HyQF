from __future__ import annotations

from hyqf.frontend import ast
from hyqf.frontend.spans import SourceSpan


def format_ast(node: ast.Node, *, show_spans: bool = False) -> str:
    """Return a stable tree rendering for a HyQF AST node."""
    printer = AstPrettyPrinter(show_spans=show_spans)
    printer.visit(node)
    return "\n".join(printer.lines)


def print_ast(node: ast.Node, *, show_spans: bool = False) -> None:
    print(format_ast(node, show_spans=show_spans))


class AstPrettyPrinter:
    def __init__(self, *, show_spans: bool):
        self.show_spans = show_spans
        self.lines: list[str] = []
        self.indent = 0

    def visit(self, node: ast.Node | None) -> None:
        if node is None:
            self._line("None")
            return
        method = getattr(self, f"_visit_{type(node).__name__}", None)
        if method is None:
            self._line(type(node).__name__)
            return
        method(node)

    def _visit_Program(self, node: ast.Program) -> None:
        self._branch("Program", node.span, lambda: [self.visit(decl) for decl in node.declarations])

    def _visit_ModuleDecl(self, node: ast.ModuleDecl) -> None:
        self._line(f"ModuleDecl name={node.name}", node.span)

    def _visit_ImportDecl(self, node: ast.ImportDecl) -> None:
        suffix = ".*" if node.wildcard else ""
        alias = f" alias={node.alias}" if node.alias else ""
        self._line(f"ImportDecl name={node.name}{suffix}{alias}", node.span)

    def _visit_ConstDecl(self, node: ast.ConstDecl) -> None:
        annotation = f": {node.annotation}" if node.annotation else ""
        self._branch(f"ConstDecl {node.name}{annotation}", node.span, lambda: self.visit(node.value))

    def _visit_SymbolDecl(self, node: ast.SymbolDecl) -> None:
        def body() -> None:
            if node.constraint:
                self._branch("Where", node.constraint.span, lambda: self.visit(node.constraint))

        self._branch(f"SymbolDecl {node.name}: {node.annotation}", node.span, body)

    def _visit_FuncDecl(self, node: ast.FuncDecl) -> None:
        keyword = "QFuncDecl" if node.is_quantum else "FuncDecl"
        params = ", ".join(f"{p.name}: {p.annotation}" for p in node.params)
        returns = node.return_type or "Void"
        self._branch(f"{keyword} {node.name}({params}) -> {returns}", node.span, lambda: self.visit(node.body))

    def _visit_QopDecl(self, node: ast.QopDecl) -> None:
        params = ", ".join(f"{p.name}: {p.annotation}" for p in node.params)
        effects = ", ".join(sorted(effect.value for effect in node.effects))
        self._line(f"QopDecl {node.name}({params}) -> {node.return_type} effects {{{effects}}}", node.span)

    def _visit_VerifyDecl(self, node: ast.VerifyDecl) -> None:
        label = "VerifyDecl"
        if node.modulo:
            label += f" modulo {node.modulo}"
        self._branch(label, node.span, lambda: [self.visit(node.left), self.visit(node.right)])

    def _visit_Block(self, node: ast.Block) -> None:
        self._branch("Block", node.span, lambda: [self.visit(stmt) for stmt in node.statements])

    def _visit_LetStmt(self, node: ast.LetStmt) -> None:
        annotation = f": {node.annotation}" if node.annotation else ""
        self._branch(f"LetStmt {self._pattern_text(node.pattern)}{annotation}", node.span, lambda: self.visit(node.value))

    def _visit_VarStmt(self, node: ast.VarStmt) -> None:
        annotation = f": {node.annotation}" if node.annotation else ""
        self._branch(f"VarStmt {node.name}{annotation}", node.span, lambda: self.visit(node.value))

    def _visit_AssignmentStmt(self, node: ast.AssignmentStmt) -> None:
        self._branch("AssignmentStmt", node.span, lambda: [self.visit(node.target), self.visit(node.value)])

    def _visit_ExprStmt(self, node: ast.ExprStmt) -> None:
        self._branch("ExprStmt", node.span, lambda: self.visit(node.expression))

    def _visit_IfStmt(self, node: ast.IfStmt) -> None:
        def body() -> None:
            self._branch("Condition", node.condition.span, lambda: self.visit(node.condition))
            self._branch("Then", node.then_block.span, lambda: self.visit(node.then_block))
            if node.else_block:
                self._branch("Else", node.else_block.span, lambda: self.visit(node.else_block))

        self._branch("IfStmt", node.span, body)

    def _visit_WhileStmt(self, node: ast.WhileStmt) -> None:
        def body() -> None:
            self._branch("Condition", node.condition.span, lambda: self.visit(node.condition))
            self._branch("Body", node.body.span, lambda: self.visit(node.body))

        self._branch("WhileStmt", node.span, body)

    def _visit_ForStmt(self, node: ast.ForStmt) -> None:
        def body() -> None:
            self._branch("Iterable", node.iterable.span, lambda: self.visit(node.iterable))
            self._branch("Body", node.body.span, lambda: self.visit(node.body))

        self._branch(f"ForStmt {node.name}", node.span, body)

    def _visit_ReturnStmt(self, node: ast.ReturnStmt) -> None:
        self._branch("ReturnStmt", node.span, lambda: self.visit(node.value))

    def _visit_BreakStmt(self, node: ast.BreakStmt) -> None:
        self._line("BreakStmt", node.span)

    def _visit_ContinueStmt(self, node: ast.ContinueStmt) -> None:
        self._line("ContinueStmt", node.span)

    def _visit_LiteralExpr(self, node: ast.LiteralExpr) -> None:
        self._line(f"LiteralExpr {node.literal_kind} {node.value!r}", node.span)

    def _visit_IdentifierExpr(self, node: ast.IdentifierExpr) -> None:
        self._line(f"IdentifierExpr {node.name}", node.span)

    def _visit_BinaryExpr(self, node: ast.BinaryExpr) -> None:
        self._branch(f"BinaryExpr {node.operator}", node.span, lambda: [self.visit(node.left), self.visit(node.right)])

    def _visit_UnaryExpr(self, node: ast.UnaryExpr) -> None:
        self._branch(f"UnaryExpr {node.operator}", node.span, lambda: self.visit(node.operand))

    def _visit_CallExpr(self, node: ast.CallExpr) -> None:
        def body() -> None:
            self._branch("Callee", node.callee.span, lambda: self.visit(node.callee))
            if node.arguments:
                self._branch("Arguments", node.span, lambda: [self.visit(arg) for arg in node.arguments])

        self._branch("CallExpr", node.span, body)

    def _visit_IndexExpr(self, node: ast.IndexExpr) -> None:
        self._branch("IndexExpr", node.span, lambda: [self.visit(node.base), self.visit(node.index)])

    def _visit_AllocationExpr(self, node: ast.AllocationExpr) -> None:
        label = f"AllocationExpr {node.quantum_type}"
        if node.size:
            self._branch(label, node.span, lambda: self.visit(node.size))
        else:
            self._line(label, node.span)

    def _visit_TupleExpr(self, node: ast.TupleExpr) -> None:
        self._branch("TupleExpr", node.span, lambda: [self.visit(element) for element in node.elements])

    def _visit_ArrayExpr(self, node: ast.ArrayExpr) -> None:
        self._branch("ArrayExpr", node.span, lambda: [self.visit(element) for element in node.elements])

    def _visit_ComposeExpr(self, node: ast.ComposeExpr) -> None:
        self._branch("ComposeExpr", node.span, lambda: [self.visit(fn) for fn in node.functions])

    def _visit_RepeatExpr(self, node: ast.RepeatExpr) -> None:
        self._branch("RepeatExpr", node.span, lambda: [self.visit(node.count), self.visit(node.function)])

    def _pattern_text(self, pattern: ast.Pattern) -> str:
        if isinstance(pattern, ast.IdentifierPattern):
            return pattern.name
        if isinstance(pattern, ast.WildcardPattern):
            return "_"
        if isinstance(pattern, ast.TuplePattern):
            return f"({', '.join(self._pattern_text(p) for p in pattern.elements)})"
        return type(pattern).__name__

    def _branch(self, text: str, span: SourceSpan, body) -> None:
        self._line(text, span)
        self.indent += 1
        body()
        self.indent -= 1

    def _line(self, text: str, span: SourceSpan | None = None) -> None:
        suffix = f" [{span}]" if self.show_spans and span else ""
        self.lines.append(f"{'  ' * self.indent}{text}{suffix}")
