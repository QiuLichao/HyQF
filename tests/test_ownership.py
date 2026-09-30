from pathlib import Path

import pytest

from hyqf.diagnostics.errors import DiagnosticError, LinearUseError
from hyqf.frontend.parser import parse_source
from hyqf.semantics.typecheck import TypeChecker
from tests.helpers import FIXTURES, read_text, serialize_effects


OWNERSHIP_VALID = sorted((FIXTURES / "ownership" / "valid").glob("*.hyqf"))
OWNERSHIP_INVALID = sorted((FIXTURES / "ownership" / "invalid").glob("*.hyqf"))


@pytest.mark.parametrize("source_path", OWNERSHIP_VALID, ids=lambda p: p.stem)
def test_ownership_valid_fixtures_match_expected_effects(source_path: Path):
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)
    checked = TypeChecker().check(program)

    assert serialize_effects(checked.effects) == read_text(source_path.with_suffix(".effects"))


@pytest.mark.parametrize("source_path", OWNERSHIP_INVALID, ids=lambda p: p.stem)
def test_ownership_invalid_fixtures_match_expected_error(source_path: Path):
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    with pytest.raises(DiagnosticError) as exc:
        TypeChecker().check(program)

    assert exc.value.code == read_text(source_path.with_suffix(".error"))
    assert exc.value.span is not None


def test_linear_use_diagnostic_reports_first_consumption_location():
    source = """
qfunc bad(q: Qubit) -> Qubit {
    let p = H(q);
    let r = X(q);
    return p;
}
"""
    program = parse_source(source, file="first-consumption.hyqf")

    with pytest.raises(LinearUseError) as exc:
        TypeChecker().check(program)

    assert "first consumed at first-consumption.hyqf:3:15-3:16" in exc.value.message
    assert exc.value.resource == "q"
    assert exc.value.span.line == 4


def test_duplicate_arguments_have_a_call_specific_diagnostic():
    source = """
qop M(q: Qubit, p: Qubit) -> (Qubit, Qubit) effects { pure, unitary };

qfunc bad() -> (Qubit, Qubit) {
    let q = new Qubit;
    let (p, r) = M(q, q);
    return (p, r);
}
"""
    program = parse_source(source, file="duplicate-arguments.hyqf")

    with pytest.raises(LinearUseError) as exc:
        TypeChecker().check(program)

    assert "duplicate quantum arguments 'q' and 'q'" in exc.value.message
    assert "first argument at duplicate-arguments.hyqf:6:20-6:21" in exc.value.message
    assert exc.value.resource == "q"
    assert exc.value.span.line == 6
