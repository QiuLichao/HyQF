from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import format_ast
from tests.helpers import FIXTURES, read_text


def test_format_ast_prints_stable_tree_from_fixture():
    source_path = FIXTURES / "parser" / "valid" / "minimal_qfunc.hyqf"
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    assert format_ast(program) == read_text(source_path.with_suffix(".ast"))


def test_format_ast_can_include_spans():
    source_path = FIXTURES / "parser" / "valid" / "const_decl.hyqf"
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)
    output = format_ast(program, show_spans=True)

    assert f"Program [{source_path.name}:" in output
    assert f"ConstDecl x: Int [{source_path.name}:" in output
