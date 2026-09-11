from __future__ import annotations

from enum import StrEnum


class Effect(StrEnum):
    PURE = "pure"
    UNITARY = "unitary"
    MEASUREMENT = "measurement"
    ALLOCATION = "allocation"
    CONTROL = "control"


def normalize_effects(effects: set[Effect] | frozenset[Effect]) -> frozenset[Effect]:
    if not effects:
        return frozenset({Effect.PURE})
    if len(effects) > 1 and Effect.PURE in effects:
        effects = set(effects)
        effects.remove(Effect.PURE)
    return frozenset(effects)
