from pathlib import Path

import pytest

from hyqf.diagnostics.errors import ParseError
from hyqf.frontend import ast
from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import format_ast
from tests.helpers import FIXTURES, read_text


PARSER_VALID = sorted((FIXTURES / "parser" / "valid").glob("*.hyqf"))
PARSER_INVALID = sorted((FIXTURES / "parser" / "invalid").glob("*.hyqf"))


@pytest.mark.parametrize("source_path", PARSER_VALID, ids=lambda p: p.stem)
def test_parser_valid_fixtures_match_expected_ast(source_path: Path):
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    assert format_ast(program) == read_text(source_path.with_suffix(".ast"))


@pytest.mark.parametrize("source_path", PARSER_INVALID, ids=lambda p: p.stem)
def test_parser_invalid_fixtures_match_expected_error(source_path: Path):
    with pytest.raises(ParseError) as exc:
        parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    assert exc.value.code == read_text(source_path.with_suffix(".error"))
    assert exc.value.span is not None


def test_parser_retains_source_spans_from_fixture():
    source_path = FIXTURES / "parser" / "valid" / "top_level_declarations.hyqf"
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    assert program.declarations[0].span.file == source_path.name
    assert program.declarations[0].span.line == 1
    assert program.declarations[2].span.line == 3


def test_gate_names_remain_identifiers_not_keywords():
    source_path = FIXTURES / "parser" / "valid" / "hybrid_program.hyqf"
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)
    evolve = program.declarations[2]
    first_let = evolve.body.statements[0]

    assert isinstance(first_let.value, ast.CallExpr)
    assert isinstance(first_let.value.callee, ast.IdentifierExpr)
    assert first_let.value.callee.name == "H"
