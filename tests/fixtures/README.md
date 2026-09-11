# HyQF Test Fixtures

Each `.hyqf` file is one standalone test program.

Expected outputs live next to the source file:

- `parser/valid/*.ast`: expected `format_ast(parse_source(source))` output.
- `parser/invalid/*.error`: expected diagnostic error code from parsing.
- `typing/valid/*.effects`: expected serialized function effect inference.
- `typing/invalid/*.error`: expected diagnostic error code from type checking.

The fixture groups intentionally separate parser coverage from semantic coverage:

- Parser fixtures may use deferred or unresolved constructs when the syntax itself is valid.
- Typing fixtures only assert rules implemented in Phase A/B.
- Deferred phases must fail conservatively with a diagnostic, not silently behave as implemented.
- Algorithm fixtures are frontend/type-system models of known algorithms. They do not claim backend executability.
