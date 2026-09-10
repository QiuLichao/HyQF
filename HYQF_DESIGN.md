# HyQF v0.1 Technical Design Specification

> **Status:** implementation specification for the first research prototype  
> **Project scope:** high-level programming frontend + verification-oriented middle-end for hybrid continuous-variable/discrete-variable (CV-DV) quantum programs  
> **Provisional name:** **HyQF** (Hybrid Quantum Functional language). The name is not semantically significant and may be changed later.

---

## 1. Purpose

HyQF is a high-level, function-oriented quantum programming language and compiler frontend for hybrid CV-DV quantum computation. Its purpose is to let programmers describe algorithms in terms of logical quantum values (`Qubit`, `Qumode`), classical values, functions, measurement-driven control flow, and symbolic parameters, without requiring them to specify physical qubit indices, Fock-to-qubit encodings, target-native gate sequences, hardware topology, or target ISA instructions.

The project is intentionally **not** a full hardware compiler. The first prototype ends at a typed, structured intermediate representation plus symbolic analysis and equivalence verification.

The core design goals are:

1. **Hybrid CV-DV abstraction:** `Qubit` and `Qumode` are distinct first-class quantum types.
2. **Function-oriented programming:** quantum functions are explicit language objects and may be composed or transformed.
3. **Linear quantum resources:** quantum values cannot be copied or multiply owned.
4. **Dynamic classical-quantum control:** measurement results may participate in classical computation and runtime `if`/`while` control.
5. **Logical resource abstraction:** programs allocate logical quantum resources; physical resource mapping is outside the frontend.
6. **Symbolic semantics and verification:** symbolic parameters and operations are preserved so that program equivalence can be checked without always expanding to concrete matrices.
7. **Backend independence:** the core language and IR are not coupled to Qiskit, PennyLane, Bosonic Qiskit, Hybridlane, or a specific hardware ISA.

---

## 2. Explicit Non-Goals for v0.1

The following are **out of scope** for the first implementation:

- gate-sequence optimization;
- gate commutation optimization;
- target-native gate decomposition;
- instruction selection;
- logical-to-physical qubit mapping;
- qumode-to-Fock/qubit encoding;
- hardware connectivity/routing;
- scheduling and pulse compilation;
- automatic Fock cutoff selection;
- production-grade numerical simulation;
- full equivalence checking for arbitrary programs with measurement, `if`, `while`, and unbounded loops;
- automatic differentiation / variational optimization.

The design must keep future support for these features possible, but v0.1 must not depend on them.

---

## 3. Recommended Implementation Stack

The reference implementation should use:

- **Python 3.12+**
- **Lark** for parsing
- Python `dataclasses` (or small immutable classes) for AST and IR nodes
- **SymPy** for symbolic expressions and algebraic simplification
- **pytest** for tests
- no mandatory dependency on Qiskit/PennyLane in the compiler core

Optional backend adapters may be separate packages/modules and must depend only on the public IR/backend interface.

---

## 4. Compilation Pipeline

The compiler pipeline is:

```text
Source
  -> Lexer / Parser
  -> AST
  -> Name Resolution
  -> Type Checking
  -> Effect Checking
  -> Linear Ownership Checking
  -> Lowering to FHQIR
  -> Symbolic Normalization
  -> Equivalence Verification
  -> IR printer / optional backend adapter
```

`FHQIR` means **Functional Hybrid Quantum Intermediate Representation**.

The source language is ergonomic and Python-like, but the IR is explicit, immutable, SSA-like, and structured.

---

## 5. Staging Model

HyQF distinguishes three categories of values:

### 5.1 Compile-time values

Examples: literal integers used to instantiate fixed-size structures, declarations, static configuration.

### 5.2 Runtime classical values

Examples: `Int`, `Float`, `Bool`, `Bit`, measurement results, arithmetic results. These may drive runtime control flow.

### 5.3 Runtime quantum values

Examples: `Qubit`, `Qumode`. These are linear resources and are never implicitly copied.

A runtime measurement result is **not** treated as a compile-time constant. Therefore control flow depending on a measurement remains explicit in the IR.

---

## 6. Lexical Specification

### 6.1 Identifiers

```ebnf
IDENTIFIER ::= LETTER { LETTER | DIGIT | "_" } ;
LETTER     ::= "A".."Z" | "a".."z" | "_" ;
DIGIT      ::= "0".."9" ;
```

Only ASCII identifiers are required in v0.1.

### 6.2 Reserved Keywords

```text
module import as
func qfunc qop return
let var const symbol
if else while for in break continue
new
adjoint controlled compose repeat
verify equiv modulo exact global_phase assume
true false
Bool Bit Int UInt Float Complex Void
Qubit Qumode Qudit Array fn qfn
effects unitary measurement allocation control pure
yield
```

Gate names such as `H`, `CX`, `D`, `CD`, `JC` are **not** lexer keywords. They are standard-library `qop` declarations.

### 6.3 Literals

Supported literals:

```text
0 1 42
0.5 3.14159
1e-5 2.3e4
1.0j 2.5j
true false
```

Complex values use Python-style `j` syntax.

### 6.4 Comments

```text
// single-line
/* multi-line */
```

Blocks use `{ ... }`; indentation is not syntactically significant.

---

## 7. Type System

### 7.1 Classical types

```text
Bool
Bit
Int
UInt
Float
Complex
Void
Array<T>
Tuple
```

`Bit` and `Bool` are distinct. A `Bit` is a runtime classical bit, typically produced by quantum measurement. Conversion to `Bool` or `Int` must be explicit using standard-library casts.

### 7.2 Quantum types

```text
Qubit
Qumode
Qudit<d>    // reserved; parser/type syntax may support it, semantics may be deferred
```

For v0.1 only `Qubit` and `Qumode` must be fully implemented.

### 7.3 Function types

```text
fn(T1, T2, ...) -> R
qfn(T1, T2, ...) -> R
```

`fn` denotes a classical pure function. `qfn` denotes a quantum-capable function.

A `qfn` may have quantum arguments and results. Quantum arguments/results are linear resources.

---

## 8. Core Surface Grammar

The grammar below is normative for v0.1 except where a production is explicitly marked as reserved.

```ebnf
program
    ::= { top_level_decl } EOF ;

top_level_decl
    ::= module_decl
     |  import_decl
     |  const_decl
     |  symbol_decl
     |  func_decl
     |  qfunc_decl
     |  qop_decl
     |  verify_decl ;

module_decl
    ::= "module" qualified_name ";" ;

import_decl
    ::= "import" qualified_name [ "." "*" ] [ "as" IDENTIFIER ] ";" ;

const_decl
    ::= "const" IDENTIFIER [ ":" type ] "=" expression ";" ;

symbol_decl
    ::= "symbol" IDENTIFIER ":" type [ "where" expression ] ";" ;

func_decl
    ::= "func" IDENTIFIER
        "(" [ parameter_list ] ")"
        [ "->" type ]
        block ;

qfunc_decl
    ::= "qfunc" IDENTIFIER
        "(" [ parameter_list ] ")"
        [ "->" type ]
        block ;

qop_decl
    ::= "qop" IDENTIFIER
        "(" [ parameter_list ] ")"
        [ "->" type ]
        "effects" "{" effect_list "}" ";" ;

parameter_list
    ::= parameter { "," parameter } ;

parameter
    ::= IDENTIFIER ":" type ;

block
    ::= "{" { statement } "}" ;

statement
    ::= let_stmt
     |  var_stmt
     |  assignment_stmt
     |  expression_stmt
     |  if_stmt
     |  while_stmt
     |  for_stmt
     |  return_stmt
     |  break_stmt
     |  continue_stmt ;

let_stmt
    ::= "let" pattern [ ":" type ] "=" expression ";" ;

var_stmt
    ::= "var" IDENTIFIER [ ":" classical_type ] "=" expression ";" ;

assignment_stmt
    ::= lvalue "=" expression ";" ;

expression_stmt
    ::= expression ";" ;

if_stmt
    ::= "if" "(" expression ")" block [ "else" block ] ;

while_stmt
    ::= "while" "(" expression ")" block ;

for_stmt
    ::= "for" IDENTIFIER "in" expression block ;

return_stmt
    ::= "return" [ expression ] ";" ;

break_stmt
    ::= "break" ";" ;

continue_stmt
    ::= "continue" ";" ;

verify_decl
    ::= "verify" expression "equiv" expression
        [ "modulo" equivalence_mode ] ";" ;

pattern
    ::= IDENTIFIER
     |  "_"
     |  "(" pattern { "," pattern } ")" ;
```

---

## 9. Expression Grammar and Precedence

```ebnf
expression     ::= logical_or ;
logical_or     ::= logical_and { "||" logical_and } ;
logical_and    ::= bit_or { "&&" bit_or } ;
bit_or         ::= bit_xor { "|" bit_xor } ;
bit_xor        ::= bit_and { "^" bit_and } ;
bit_and        ::= equality { "&" equality } ;
equality       ::= comparison { ( "==" | "!=" ) comparison } ;
comparison     ::= additive { ( "<" | "<=" | ">" | ">=" ) additive } ;
additive       ::= multiplicative { ( "+" | "-" ) multiplicative } ;
multiplicative ::= power { ( "*" | "/" | "%" ) power } ;
power          ::= unary [ "**" power ] ;
unary          ::= ( "!" | "+" | "-" ) unary
                 | "adjoint" unary
                 | "controlled" unary
                 | postfix ;
postfix        ::= primary { call_suffix | index_suffix } ;
call_suffix    ::= "(" [ argument_list ] ")" ;
index_suffix   ::= "[" expression "]" ;
primary        ::= literal
                 | IDENTIFIER
                 | allocation_expr
                 | tuple_expr
                 | array_expr
                 | compose_expr
                 | repeat_expr
                 | "(" expression ")" ;

allocation_expr ::= "new" quantum_type [ "[" expression "]" ] ;
compose_expr    ::= "compose" "(" expression { "," expression } ")" ;
repeat_expr     ::= "repeat" "(" expression "," expression ")" ;
```

`compose(f, g, h)` executes `f` first, then `g`, then `h`; mathematically the composed function is `h o g o f`.

`repeat(n, f)` requires a non-negative compile-time integer `n` in v0.1.

---

## 10. Type Grammar

```ebnf
type
    ::= classical_type
     |  quantum_type
     |  array_type
     |  tuple_type
     |  function_type ;

classical_type
    ::= "Bool" | "Bit" | "Int" | "UInt" | "Float" | "Complex" | "Void" ;

quantum_type
    ::= "Qubit"
     |  "Qumode"
     |  "Qudit" "<" INTEGER ">" ;

array_type
    ::= "Array" "<" type ">" ;

tuple_type
    ::= "(" type "," type { "," type } ")" ;

function_type
    ::= "fn"  "(" [ type_list ] ")" "->" type
     |  "qfn" "(" [ type_list ] ")" "->" type ;
```

---

## 11. Standard Quantum Operations

The compiler must not hardcode gate names into the lexer. Standard operations are registered through `qop` signatures.

A minimal standard library should declare at least:

```text
qop H(q: Qubit) -> Qubit effects { unitary };
qop X(q: Qubit) -> Qubit effects { unitary };
qop RZ(theta: Float, q: Qubit) -> Qubit effects { unitary };
qop CX(c: Qubit, t: Qubit) -> (Qubit, Qubit) effects { unitary };

qop D(alpha: Complex, m: Qumode) -> Qumode effects { unitary };
qop R(theta: Float, m: Qumode) -> Qumode effects { unitary };
qop S(zeta: Complex, m: Qumode) -> Qumode effects { unitary };
qop BS(theta: Float, phi: Float, a: Qumode, b: Qumode)
    -> (Qumode, Qumode) effects { unitary };

qop CD(alpha: Complex, q: Qubit, m: Qumode)
    -> (Qubit, Qumode) effects { unitary };
qop CR(theta: Float, q: Qubit, m: Qumode)
    -> (Qubit, Qumode) effects { unitary };
qop JC(theta: Float, phi: Float, q: Qubit, m: Qumode)
    -> (Qubit, Qumode) effects { unitary };
```

The first prototype only needs enough operations to exercise DV, CV, and hybrid typing.

---

## 12. Measurement Semantics

Measurement is explicitly represented as a quantum effect and may produce runtime classical values.

To keep linear semantics correct, a non-destructive/projective measurement returns the post-measurement quantum resource **and** the classical result:

```text
qop measure_z(q: Qubit) -> (Qubit, Bit) effects { measurement };
qop measure_n(m: Qumode) -> (Qumode, UInt) effects { measurement };
qop measure_x(m: Qumode) -> (Qumode, Float) effects { measurement };
```

Example:

```text
let q = new Qubit;
let q = H(q);
let (q, r) = measure_z(q);
let x = int(r) + 1;
if (x > 1) {
    let q = X(q);
}
return q;
```

The repeated identifier `q` is legal **linear rebinding**: the old binding is consumed and a new binding is introduced. Internally each binding lowers to a distinct SSA value.

A future destructive measurement may be declared as a separate `qop` whose signature returns only the classical result. It is not required in v0.1.

Backends may declare that a measurement form is unsupported or destructive. Backend capability checking is separate from language semantics.

---

## 13. Classical Computation

Measurement results are ordinary runtime classical values after type checking. Examples:

```text
let (q1, r1) = measure_z(q1);
let (q2, r2) = measure_z(q2);
let parity = r1 ^ r2;
let count = int(r1) + 2 * int(r2);
let cond = count > 1;
```

Required standard casts:

```text
bool(Bit) -> Bool
int(Bit)  -> Int
float(Int) -> Float
```

Implicit `Bit -> Bool` conversion is forbidden in v0.1.

---

## 14. Function Semantics

### 14.1 `func`

`func` is a classical pure function.

Rules:

- may not accept or return `Qubit`, `Qumode`, or arrays/tuples containing quantum types;
- may not call a `qop` or `qfunc`;
- has inferred effect `pure`.

### 14.2 `qfunc`

`qfunc` may accept/return quantum resources and may call `qop`/`qfunc`.

A quantum function is a first-class value with type `qfn(...) -> ...`.

Example:

```text
qfunc evolve(q: Qubit, m: Qumode, theta: Float)
    -> (Qubit, Qumode) {
    let q = H(q);
    let (q, m) = CR(theta, q, m);
    return (q, m);
}
```

Semantically this is a linear state transformer. The surface language may reuse names, but IR values are immutable.

---

## 15. Linear Ownership Rules

Quantum values are linear resources.

### 15.1 No copying

This is invalid:

```text
let q = new Qubit;
let a = q;
let b = q;
```

The second use of `q` must produce a `LinearUseError`.

### 15.2 No aliasing as independent operands

Invalid:

```text
let q = new Qubit;
let (q1, q2) = CX(q, q);
```

Invalid:

```text
let m = new Qumode;
let (a, b) = BS(0.1, 0.0, m, m);
```

### 15.3 Consumption and rebinding

For a call `let q2 = H(q1);`, `q1` is consumed and `q2` becomes the unique live resource.

Name rebinding:

```text
let q = H(q);
```

is syntactic sugar for consuming the current binding and introducing a new binding with the same source-level name.

### 15.4 Function boundaries

If a `qfunc` receives a quantum resource, it must either:

- return a live successor resource;
- pass ownership into another returned resource structure;
- or consume it through a consuming operation.

Implicit dropping of live quantum resources is an error.

### 15.5 Arrays of quantum resources

`Array<Qubit>` and `Array<Qumode>` are linear containers.

For v0.1:

- statically indexed access is supported;
- runtime indexed access may be represented in AST/IR but is an **MVP extension point**, because safe exclusive borrowing of a dynamically selected element requires additional ownership machinery.

Do not silently treat runtime `qs[i]` as a copyable reference.

---

## 16. Effect System

Supported effects:

```text
pure
unitary
measurement
allocation
control
```

Effects form a set attached to every `qfunc` and `qop`.

Rules:

- a `func` must have exactly `{pure}`;
- primitive gates have `{unitary}`;
- measurements have `{measurement}`;
- `new Qubit` / `new Qumode` introduce `{allocation}`;
- runtime `if`/`while` depending on runtime values introduce `{control}`;
- a function's effects are inferred as the union of its operations, excluding `pure` when other effects exist.

### 16.1 `adjoint`

`adjoint f` is valid in v0.1 only if:

- `f` is a `qfunc`;
- inferred effects are exactly `{unitary}`;
- `f` contains no measurement, allocation, runtime control flow, or external side effects.

The result is a symbolic function transform in the IR. No gate-level inversion is required in v0.1.

### 16.2 `controlled`

`controlled f` follows the same eligibility rule as `adjoint` in v0.1.

If `f : qfn(A...) -> R`, then `controlled f` conceptually adds a leading control `Qubit` and returns the updated control plus the original result structure. The transform remains symbolic in the IR.

### 16.3 `compose`

`compose(f, g, ...)` is valid only when adjacent function types unify. Composition preserves symbolic function structure until an explicit inlining/lowering pass.

---

## 17. Control-Flow Semantics

Runtime control flow is part of the program IR and must not be flattened to a fixed gate list when its condition depends on runtime values.

### 17.1 `if`

The condition must have type `Bool`.

Each branch starts from the same incoming linear-resource environment. At the merge:

- the same set of outer-scope quantum resource names must remain live in both branches;
- corresponding resources must have the same quantum type;
- consuming a resource in only one branch is an ownership error;
- new branch-local resources do not escape unless explicitly returned through a surrounding function/result mechanism.

When a quantum resource is rebound in both branches, lowering creates structured `IfOp` results representing the merged post-branch resource versions.

### 17.2 `while`

The condition must have type `Bool`.

Any outer-scope classical or quantum binding rebound by the loop becomes a loop-carried value in the IR.

The loop body must preserve the type and linear ownership shape of all loop-carried quantum resources across the backedge.

The number of iterations may be unknown at compile time.

### 17.3 `for`

`for` is supported for classical iterables/ranges.

- compile-time fixed ranges may be unrolled by a future pass, but v0.1 does not require unrolling;
- runtime loops lower to structured loop IR.

---

## 18. FHQIR Design

FHQIR is the canonical internal representation after static semantic analysis.

### 18.1 Properties

FHQIR must be:

- typed;
- SSA-like;
- immutable at the value level;
- linear-resource aware;
- structured-control-flow aware;
- symbolic-parameter preserving;
- backend independent.

### 18.2 Core entities

```text
Module
Function
Block / Region
Value
Type
EffectSet
Operation
```

### 18.3 Core operation classes

```text
AllocQubitOp
AllocQumodeOp
QuantumOp
MeasureOp
ClassicalOp
CallOp
IfOp
WhileOp
ForOp
ReturnOp
YieldOp
AdjointFunctionOp
ControlledFunctionOp
ComposeFunctionOp
RepeatFunctionOp
```

### 18.4 Example lowering

Source:

```text
qfunc protocol(alpha: Complex) -> UInt {
    let q = new Qubit;
    let m = new Qumode;
    let q = H(q);
    let (q, m) = CD(alpha, q, m);
    let (q, r) = measure_z(q);
    let x = int(r) + 1;
    if (x > 1) {
        let m = D(alpha, m);
    }
    let (m, n) = measure_n(m);
    return n;
}
```

Possible FHQIR form:

```text
func @protocol(%alpha: Complex) -> UInt {
  %q0 = q.alloc
  %m0 = cv.alloc
  %q1 = q.h %q0
  %q2, %m1 = hybrid.cd %alpha, %q1, %m0
  %q3, %r0 = q.measure_z %q2
  %x0 = cast.int %r0
  %x1 = arith.add %x0, 1
  %c0 = arith.gt %x1, 1
  %m2 = scf.if %c0 -> Qumode {
    ^then:
      %m3 = cv.displace %alpha, %m1
      yield %m3
    ^else:
      yield %m1
  }
  %m4, %n0 = cv.measure_n %m2
  return %n0
}
```

The exact textual printer syntax may differ; the semantic structure must remain equivalent.

---

## 19. Symbolic Parameters

Top-level symbolic values are declared with:

```text
symbol theta: Float;
symbol alpha: Complex;
```

Optional assumptions:

```text
symbol theta: Float where theta >= 0.0 && theta <= 6.283185;
```

Symbolic values are stored as SymPy expressions in the semantic layer and are not numerically bound during verification.

---

## 20. Equivalence Verification

### 20.1 User-facing syntax

```text
verify f equiv g;
verify f equiv g modulo global_phase;
```

### 20.2 Result domain

The verifier returns one of:

```text
Equivalent
NotEquivalent
Unknown
```

`Unknown` is mandatory. Failure to prove equivalence is not the same as proving non-equivalence.

### 20.3 v0.1 verification scope

The verifier must accept only functions satisfying all of:

- both are `qfunc` values with identical function types;
- effects are exactly `{unitary}`;
- straight-line body only;
- no `if`, `while`, `for`;
- no measurement;
- no allocation inside the function body;
- no runtime dynamic indexing;
- operations belong to a registered symbolic rule set.

If preconditions are not met, return `Unknown` with a diagnostic reason.

### 20.4 Normalization architecture

```text
qfunc
 -> symbolic operation graph/sequence
 -> rewrite to fixed point
 -> canonical parameter simplification (SymPy)
 -> canonical operation form
 -> structural comparison
 -> optional fallback checker
```

### 20.5 Minimal rewrite rules

DV examples:

```text
H ; H                  -> I
X ; X                  -> I
RZ(a) ; RZ(b)          -> RZ(a + b)
RZ(0)                  -> I
```

CV examples:

```text
R(a) ; R(b)            -> R(a + b)
R(0)                   -> I
D(0)                   -> I
D(a) ; D(b)            -> Phase(phi(a,b)) ; D(a + b)
```

where

```text
phi(a,b) = (a*conjugate(b) - conjugate(a)*b) / 2
```

The phase tracker must distinguish exact equivalence from equivalence modulo global phase.

Hybrid rewrite rules should be registry-driven. v0.1 may include only a very small number of trusted identities; the verifier must return `Unknown` rather than inventing a decomposition.

### 20.6 Optional DV matrix fallback

A small exact/symbolic matrix fallback is permitted only for DV-only functions with a very small bounded number of qubits (for example <= 3) and must use exact/SymPy arithmetic where practical. It is a fallback, not the primary verifier architecture.

### 20.7 CV Gaussian canonicalization

Full Gaussian `(S, d)` canonicalization is a **Phase 2** feature, not required for v0.1.

---

## 21. Backend Interface

The core compiler must expose a backend-neutral interface but no production backend is required in v0.1.

Recommended interface:

```python
class Backend(Protocol):
    name: str
    capabilities: BackendCapabilities
    def compile(self, module: FHQIRModule) -> BackendArtifact: ...
```

Suggested capability flags:

```text
qubit
qumode
mid_circuit_qubit_measurement
mid_circuit_fock_measurement
mid_circuit_homodyne_measurement
classical_bool
classical_integer
classical_float
if_control
while_control
dynamic_qubit_allocation
dynamic_qumode_allocation
```

The language must be more expressive than any one backend. Unsupported features are reported during backend lowering, not rejected by the frontend merely because one backend lacks them.

Potential future adapters:

- Guppy/HUGR for the dynamic DV subset;
- Bosonic Qiskit for CV-DV simulation-oriented subsets;
- Hybridlane/PennyLane for compatible hybrid circuit subsets.

These are future adapters, not core dependencies.

---

## 22. Diagnostics

Every AST node should retain a source span (`file`, `line`, `column`, end position if available).

Required diagnostic classes include:

```text
ParseError
NameResolutionError
TypeError
EffectError
LinearUseError
ResourceLeakError
ControlFlowOwnershipError
VerificationUnsupported
BackendCapabilityError
```

Diagnostics should include:

- concise primary message;
- source location;
- expected vs actual type when relevant;
- resource name and first-consumption location for ownership errors;
- a machine-readable error code.

---

## 23. Proposed Python Package Layout

```text
hyqf/
  __init__.py

  frontend/
    grammar.lark
    parser.py
    ast.py
    spans.py

  semantics/
    symbols.py
    types.py
    effects.py
    resolver.py
    typecheck.py
    ownership.py

  ir/
    module.py
    function.py
    block.py
    value.py
    ops.py
    printer.py
    builder.py

  stdlib/
    classical.py
    dv.py
    cv.py
    hybrid.py
    measurement.py

  symbolic/
    expressions.py
    rules.py
    normalize.py
    phase.py

  verifier/
    verify.py
    result.py
    dv_matrix.py

  backend/
    interface.py
    capabilities.py

  diagnostics/
    errors.py

  cli.py

tests/
  parser/
  typing/
  ownership/
  control_flow/
  ir/
  verifier/
```

---

## 24. Implementation Order

### Phase A: parsing and AST

Implement lexer/parser, AST nodes, spans, pretty-print tests.

### Phase B: semantic core

Implement symbol tables, classical/quantum types, function signatures, standard `qop` registry, type checker.

### Phase C: linear ownership

Implement use tracking, consumption/rebinding rules, function boundary leak checks, alias rejection for multi-resource operations.

### Phase D: FHQIR

Lower valid AST into typed SSA-like FHQIR; implement IR printer.

### Phase E: runtime control flow representation

Implement `IfOp`, `WhileOp`, branch joins, loop-carried values, measurement-result classical dataflow.

### Phase F: functional transforms

Implement symbolic `adjoint`, `controlled`, `compose`, and `repeat` nodes plus eligibility/effect checks.

### Phase G: symbolic verifier

Implement rule registry, SymPy normalization, global-phase tracker, 3-valued result domain, and a small DV fallback checker.

### Phase H: optional backend adapter

Only after the frontend/IR/verifier are stable.

---

## 25. MVP Acceptance Tests

The implementation is considered a valid v0.1 prototype when all of the following pass.

### 25.1 Hybrid typing

Accepted:

```text
let q = new Qubit;
let m = new Qumode;
let q = H(q);
let m = D(1.0j, m);
let (q, m) = CD(1.0j, q, m);
```

Rejected:

```text
let q = new Qubit;
let q = D(1.0j, q);
```

### 25.2 Linear ownership

Rejected:

```text
let q = new Qubit;
let (a, b) = CX(q, q);
```

Rejected:

```text
let m = new Qumode;
let (a, b) = BS(0.1, 0.0, m, m);
```

### 25.3 Measurement to classical computation

Accepted:

```text
let q = new Qubit;
let (q, r) = measure_z(q);
let x = int(r) + 2;
let c = x > 1;
```

### 25.4 Runtime control flow

Accepted and lowered to structured IR:

```text
let q = new Qubit;
let (q, r) = measure_z(q);
if (bool(r)) {
    let q = X(q);
} else {
    let q = H(q);
}
return q;
```

Both branches must produce one live `Qubit` at the merge.

### 25.5 Effect checking

Accepted:

```text
qfunc f(q: Qubit) -> Qubit {
    let q = H(q);
    return q;
}
let g = adjoint f;
```

Rejected:

```text
qfunc f(q: Qubit) -> (Qubit, Bit) {
    let (q, r) = measure_z(q);
    return (q, r);
}
let g = adjoint f;
```

### 25.6 Symbolic equivalence

Must prove `Equivalent`:

```text
symbol a: Float;
symbol b: Float;

qfunc f(q: Qubit) -> Qubit {
    let q = RZ(a, q);
    let q = RZ(b, q);
    return q;
}

qfunc g(q: Qubit) -> Qubit {
    let q = RZ(a + b, q);
    return q;
}

verify f equiv g modulo global_phase;
```

Must return `Unknown`, not crash, for a function containing measurement or `while`.

---

## 26. Design Invariants

The following invariants must hold throughout the implementation:

1. **No quantum value duplication.** Each live quantum IR value has a unique ownership chain.
2. **No implicit physical resource identity.** Surface/IR quantum values are logical resources, not hardware indices.
3. **No backend leakage into the frontend.** Qiskit/PennyLane/Bosonic-Qiskit objects must not appear in AST, type system, or FHQIR core classes.
4. **No flattening of runtime control flow.** Measurement-dependent `if`/`while` remains structured in the IR.
5. **No eager matrix expansion in the frontend.** Quantum operations remain symbolic unless an explicit verifier/backend pass requests a matrix.
6. **No unsound verifier answers.** Unsupported proof obligations return `Unknown`.
7. **Surface mutability does not imply IR mutability.** `var` is classical only; quantum rebinding always lowers to new SSA values.
8. **Operation typing is registry-driven.** New DV/CV/hybrid operations can be added without changing the lexer/parser.

---

## 27. Deferred Features

The following are intentionally deferred until after v0.1:

- full runtime-indexed exclusive borrowing for `Array<Qubit>` / `Array<Qumode>`;
- higher-order lambdas/closures over quantum resources;
- full Gaussian symplectic canonicalization;
- measurement/control-flow equivalence;
- loop invariants and bounded-loop proof rules;
- automatic differentiation;
- resource estimation and Fock-cutoff inference;
- target ISA lowering and physical mapping;
- optimization passes.

The parser/IR design should not make these features impossible, but they must not complicate the v0.1 implementation unnecessarily.

---

## 28. Minimal Example Program

```text
module demo.hybrid;

symbol alpha: Complex;

qfunc evolve(q: Qubit, m: Qumode, a: Complex)
    -> (Qubit, Qumode) {
    let q = H(q);
    let (q, m) = CD(a, q, m);
    return (q, m);
}

qfunc protocol(a: Complex) -> UInt {
    let q = new Qubit;
    let m = new Qumode;

    let (q, m) = evolve(q, m, a);
    let (q, bit) = measure_z(q);
    let score = int(bit) + 1;

    if (score > 1) {
        let m = D(a, m);
    }

    let (m, n) = measure_n(m);
    return n;
}
```

This example exercises the intended core semantics: hybrid types, quantum functions, logical allocation, linear rebinding, hybrid operations, mid-circuit measurement, classical computation, runtime control flow, and CV measurement.

---

## 29. Summary for the Implementer

Build **a language frontend and verification-oriented IR**, not a hardware compiler. The highest-priority correctness properties are type correctness, linear ownership correctness, explicit runtime control flow, symbolic preservation, and conservative verification. Prefer a small sound implementation over broad but unsound feature coverage.

The implementation should be considered successful if it can parse and statically validate nontrivial hybrid CV-DV programs, lower them to readable structured FHQIR, represent measurement-dependent control flow without flattening it, and prove a useful but deliberately restricted set of symbolic unitary equivalences.
