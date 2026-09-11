from pathlib import Path

import pytest

from hyqf.diagnostics.errors import DiagnosticError
from hyqf.frontend.parser import parse_source
from hyqf.semantics.effects import Effect
from hyqf.semantics.qops import OperationSignature, QopRegistry
from hyqf.semantics.typecheck import TypeChecker
from hyqf.semantics.types import COMPLEX, QUBIT
from tests.helpers import FIXTURES, read_text, serialize_effects


TYPING_VALID = sorted(
    p for p in (FIXTURES / "typing" / "valid").glob("*.hyqf") if p.name != "external_registry_qop.hyqf"
)
TYPING_INVALID = sorted((FIXTURES / "typing" / "invalid").glob("*.hyqf"))


@pytest.mark.parametrize("source_path", TYPING_VALID, ids=lambda p: p.stem)
def test_typecheck_valid_fixtures_match_expected_effects(source_path: Path):
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)
    checked = TypeChecker().check(program)

    assert serialize_effects(checked.effects) == read_text(source_path.with_suffix(".effects"))


@pytest.mark.parametrize("source_path", TYPING_INVALID, ids=lambda p: p.stem)
def test_typecheck_invalid_fixtures_match_expected_error(source_path: Path):
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)

    with pytest.raises(DiagnosticError) as exc:
        TypeChecker().check(program)

    assert exc.value.code == read_text(source_path.with_suffix(".error"))
    assert exc.value.span is not None


def test_qop_registry_is_extensible_and_call_types_are_registry_driven():
    registry = QopRegistry()
    registry.register(OperationSignature("Pulse", (COMPLEX, QUBIT), QUBIT, frozenset({Effect.UNITARY})))
    source_path = FIXTURES / "typing" / "valid" / "external_registry_qop.hyqf"
    program = parse_source(source_path.read_text(encoding="utf-8"), file=source_path.name)
    checked = TypeChecker(registry).check(program)

    assert serialize_effects(checked.effects) == read_text(source_path.with_suffix(".effects"))
