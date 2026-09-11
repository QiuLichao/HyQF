from __future__ import annotations

from pathlib import Path

from hyqf.semantics.effects import Effect


FIXTURES = Path(__file__).parent / "fixtures"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def serialize_effects(effects: dict[str, frozenset[Effect]]) -> str:
    if not effects:
        return "<no functions>"
    lines = []
    for name in sorted(effects):
        values = ", ".join(sorted(effect.value for effect in effects[name]))
        lines.append(f"{name}: {values}")
    return "\n".join(lines)
