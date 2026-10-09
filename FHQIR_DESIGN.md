# FHQIR Design Specification

Status: Draft for Phase D implementation  
Target IR version: 1.0  
Project: HyQF  
Authoritative relationship: this document refines Sections 18, 20, 26, and the Phase D/E/G requirements of `HYQF_DESIGN.md`. If the two documents conflict on FHQIR details, this document takes precedence after the conflict is reviewed and recorded.

---

## 1. Purpose

FHQIR means **Functional Hybrid Quantum Intermediate Representation**. It is the canonical representation between the validated HyQF frontend and all later analyses, transformations, symbolic verification, serialization, and optional backend adapters.

FHQIR is not a gate-list cache and is not a wrapper around Qiskit, PennyLane, SymPy, NumPy, or a hardware SDK. It is a standalone, backend-neutral, typed, SSA-like IR with first-class linear quantum resources and structured runtime control flow.

The first production-quality version must support:

- classical, DV, CV, and hybrid quantum values;
- immutable SSA values and explicit use-def chains;
- linear ownership of every quantum value and quantum aggregate;
- function and qop calls;
- allocation, unitary operations, measurement, and terminal consumption;
- tuples and quantum arrays without implicit copying;
- structured `if`, `while`, and `for` representation;
- early return, loop break, and loop continue as structured exits;
- exact preservation of symbolic parameters;
- deterministic text serialization and round-trip parsing;
- IR validation independent of the source frontend;
- symbolic-verifier extraction without eager matrix expansion;
- future backend lowering without leaking backend objects into the core.

---

## 2. Normative Language

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

- A malformed IR object MUST be rejected with an IR diagnostic; core validation MUST NOT rely on Python `assert`.
- A consumer MUST validate an externally loaded module before running transformations, verification, or backend lowering.
- Unsupported semantics MUST produce an explicit unsupported/unknown result, not a guessed interpretation.

---

## 3. Scope and Boundaries

### 3.1 In scope

FHQIR 1.0 specifies:

- the core Python object model;
- the canonical type and attribute model;
- values, operations, blocks, regions, functions, and modules;
- operation schemas and operation registries;
- quantum resource-flow contracts;
- structured control-flow contracts;
- aggregate and array ownership semantics;
- AST-to-FHQIR lowering contracts;
- validation layers;
- symbolic-verifier integration points;
- deterministic text format and versioning;
- pass and rewrite safety rules;
- engineering acceptance and test requirements.

### 3.2 Explicitly out of scope for FHQIR 1.0

The core does not define:

- physical qubit, resonator, mode, or register indices;
- hardware topology, routing, scheduling, pulse timing, or target ISA;
- Fock cutoffs or qumode-to-qubit encodings;
- arbitrary aliasing or shared mutable quantum references;
- eager dense matrices attached to ordinary operations;
- full verification of measurement-dependent equivalence;
- unbounded-loop proof rules;
- production optimization policy;
- automatic differentiation;
- an ABI for a specific vendor runtime.

These features may be represented by later dialects or analyses, but MUST NOT alter the semantics of the FHQIR core.

---

## 4. Compiler Pipeline Position

The intended pipeline is:

```text
HyQF source
  -> Lexer / Parser
  -> AST with SourceSpan
  -> name, type, effect, and ownership checking
  -> SemanticModel
  -> FHQIR lowering
  -> FHQIR validation and freeze
  -> analyses / functional transforms
  -> symbolic verifier or optional backend adapter
```

FHQIR lowering MUST consume a successful semantic result. It MUST NOT silently repeat name resolution with different rules or infer ownership from source names alone.

---

## 5. Global Design Invariants

Every valid FHQIR module MUST satisfy all of the following.

1. **Single definition.** Every SSA value has exactly one definition: a block argument or an operation result.
2. **Dominance.** A value definition dominates every ordinary use.
3. **No quantum duplication.** Every linear value is consumed at most once on each executable path.
4. **No quantum loss.** A live linear value reaches a return, yield, loop exit, terminal consumer, or another linear owner.
5. **No implicit physical identity.** Quantum values represent logical resource versions, never hardware locations.
6. **Immutable values.** An operation creates successor values; it never mutates an existing SSA value.
7. **Explicit control flow.** Runtime `if` and loops remain structured regions and are never flattened into a fixed gate sequence.
8. **Explicit resource flow.** Every quantum operation declares how result resources relate to operand resources.
9. **Registry-driven operations.** Operation meaning is referenced by stable schema/semantic identifiers, not recognized by parser special cases.
10. **Symbolic preservation.** Symbolic expressions remain exact and inspectable until an explicit verifier or backend requests another representation.
11. **Backend isolation.** Core IR attributes contain only FHQIR-defined serializable values.
12. **Determinism.** Equivalent construction order produces deterministic printing, hashing, and validation output.
13. **Conservative verification.** Incomplete semantics result in `Unknown`, never an unsound proof.

---

## 6. Frontend Semantic Contract

The current `CheckedModule` is not sufficient for reliable FHQIR lowering because it only exposes global symbols and inferred effects. Phase D SHOULD first introduce an immutable `SemanticModel`.

```python
@dataclass(frozen=True)
class SemanticModel:
    program: ast.Program
    global_symbols: SymbolTableSnapshot
    symbols: Mapping[SymbolId, SymbolFact]
    node_types: Mapping[NodeId, Type]
    resolved_symbols: Mapping[NodeId, SymbolId]
    binding_versions: Mapping[NodeId, BindingId]
    call_targets: Mapping[NodeId, CallableId]
    callable_signatures: Mapping[CallableId, OperationSignature]
    function_summaries: Mapping[CallableId, FunctionResourceSummary]
    function_effects: Mapping[FunctionId, frozenset[Effect]]
    ownership: OwnershipFacts
    array_shapes: Mapping[BindingId, StaticExtent | DynamicExtent]
    branch_joins: Mapping[NodeId, BranchJoin]
```

`OwnershipFacts` MUST record at least:

- each quantum binding version;
- the source occurrence that consumes it;
- the successor binding or terminal consumer;
- qop-call alias decisions;
- branch and loop-carried resource joins;
- static array unpack/repack relationships;
- whether a value exits by return, yield, break, or continue.

The lowering implementation MAY recompute local convenience data, but it MUST treat the semantic model as authoritative.

---

## 7. Core Object Model

### 7.1 Stable identifiers

The implementation MUST use typed identifiers rather than raw integers at API boundaries.

```python
@dataclass(frozen=True, order=True)
class ModuleId:
    value: int

@dataclass(frozen=True, order=True)
class FunctionId:
    value: int

@dataclass(frozen=True, order=True)
class BlockId:
    value: int

@dataclass(frozen=True, order=True)
class OperationId:
    value: int

@dataclass(frozen=True, order=True)
class ValueId:
    value: int
```

IDs are unique within a module. Textual `%0` names are printer-assigned stable aliases and are not semantic hardware identifiers.

### 7.2 Module

```python
@dataclass
class IRModule:
    name: str
    version: IRVersion
    symbols: SymbolTable
    functions: list[IRFunction]
    qop_declarations: list[IRQopDeclaration]
    attributes: AttributeMap
    source_map: SourceMap
```

A module owns all IDs, symbol declarations, operation schemas referenced by stable IDs, and source-map entries. Function and qop names MUST be fully qualified before serialization.

### 7.3 Function

```python
@dataclass
class IRFunction:
    id: FunctionId
    name: QualifiedName
    signature: IRFunctionType
    body: Region
    effects: frozenset[Effect]
    visibility: Visibility
    attributes: AttributeMap
    span: SourceSpan | None
```

An external function has no body and MUST carry an explicit trusted signature and resource-flow summary. A defined function has exactly one top-level region.

### 7.4 Region

```python
@dataclass
class Region:
    owner: Operation | IRFunction
    blocks: list[Block]
```

FHQIR uses regions to preserve structured control flow. Most 1.0 structured operations use one entry block per region, but the core permits multiple blocks for future canonical control-flow lowering.

A region MUST NOT capture a linear value implicitly. Every external linear input appears as an explicit parent-operation operand and an entry block argument. Classical captures SHOULD also be explicit so serialization and dependency analysis remain local.

### 7.5 Block

```python
@dataclass
class Block:
    id: BlockId
    arguments: list[BlockArgument]
    operations: list[Operation]
```

Every non-empty reachable block MUST end in exactly one terminator. No operation may appear after a terminator.

### 7.6 Value

```python
@dataclass
class Value:
    id: ValueId
    type: IRType
    owner: BlockArgumentOwner | OperationResultOwner
    uses: UseList
    span: SourceSpan | None
```

A `Value` is immutable after module freeze. `uses` is maintained by the builder/rewriter and is not part of semantic equality or text serialization.

### 7.7 Operation

```python
@dataclass
class Operation:
    id: OperationId
    opcode: OperationName
    operands: list[Value]
    results: list[Value]
    attributes: AttributeMap
    regions: list[Region]
    effects: frozenset[Effect]
    span: SourceSpan | None
```

The core operation object is generic. Its legal shape and semantics come from an `OperationSchema` resolved through the registry.

Arbitrary Python objects, closures, backend handles, SymPy nodes, NumPy arrays, and executable callbacks MUST NOT appear in `AttributeMap`.

---

## 8. Construction, Mutation, and Freeze

IR construction occurs in two states.

### 8.1 Mutable builder state

`IRBuilder` and `IRRewriter` MAY create operations, replace operands, split blocks, and maintain use lists. Mutations MUST be transactional: a failed rewrite leaves the module unchanged.

### 8.2 Frozen state

After validation, `module.freeze()` produces a read-only module suitable for hashing, caching, parallel analysis, symbolic verification, and serialization.

Passes MUST NOT mutate a frozen module. A transforming pass returns a new frozen module or explicitly opens a builder transaction.

---

## 9. Type System

FHQIR reuses HyQF semantic types where possible but defines an IR-specific closed hierarchy.

### 9.1 Public types

```text
Bool
Bit
Int
UInt
Float
Complex
Void
Qubit
Qumode
Qudit<d>             reserved until semantics are enabled
Tuple<T...>
Array<T, extent?>
Function<(T...) -> (R...), quantum?>
```

`extent` is one of:

- a non-negative compile-time integer;
- a symbolic shape value with an explicit constraint;
- `dynamic`.

Source `Array<T>` lowers to `Array<T, dynamic>` unless semantic analysis proves a static extent. An explicit `array.erase_extent` operation may widen `Array<T, N>` to `Array<T, dynamic>`. Narrowing a dynamic extent requires a checked assertion operation and is not implicit.

### 9.2 Internal-only types

The following types are legal inside FHQIR but not in source signatures:

```text
ControlToken
ProofToken
ArrayUpdateToken<T, extent?>
OpaqueType<dialect, name, parameters...>
```

An `ArrayUpdateToken` is linear, cannot be returned from a public function, cannot be stored in a tuple/array, and must be consumed by its matching array update terminator.

### 9.3 Linear type predicate

```text
is_linear(Qubit)                   = true
is_linear(Qumode)                  = true
is_linear(Qudit<d>)                = true
is_linear(Array<T, n>)             = is_linear(T)
is_linear(Tuple<T...>)             = any(is_linear(Ti))
is_linear(Function<...>)           = false in 1.0
is_linear(classical primitive)     = false
```

A tuple or array containing a quantum value is one linear aggregate owner. Its children cannot be used independently until an explicit unpack operation consumes the aggregate.

### 9.4 Type interning

Types SHOULD be immutable and interned. Structural type equality MUST NOT depend on object identity.

---

## 10. Exact Constants and Symbolic Values

Symbolic verification requires exact literals. FHQIR MUST NOT reconstruct source decimals from Python `float` values.

Before Phase D lowering, AST literals SHOULD retain their original lexeme or use exact literal classes:

```python
IntegerLiteral(value: int)
DecimalLiteral(text: str)
RationalLiteral(numerator: int, denominator: int)
ComplexLiteral(real: ExactNumber, imag: ExactNumber)
```

FHQIR attributes support only canonical exact forms:

```text
integer<42>
decimal<"0.125">
rational<1, 8>
complex<rational<0,1>, rational<1,1>>
symbol_ref<@theta>
```

Ordinary symbolic arithmetic is represented by classical SSA operations such as `arith.add`, `arith.mul`, `arith.pow`, and `complex.conjugate`. The core does not embed SymPy. The symbolic verifier translates validated classical subgraphs into its own symbolic representation.

Top-level symbols are immutable module declarations containing:

- qualified name;
- classical type;
- optional assumption expression;
- source span.

Quantum-typed top-level symbols are invalid.

---

## 11. SSA and Use-Def Rules

### 11.1 Classical values

Classical SSA values may have any number of uses unless their type is an internal linear token.

### 11.2 Linear values

A linear value has at most one consuming use on each executable path. Reading a linear value is always a consuming operation; the core has no general-purpose quantum borrow.

Structured operations are special consuming uses:

- an `if` consumes each carried input once at the parent level;
- mutually exclusive region block arguments represent path-local ownership, not duplicated resources;
- a loop consumes its initial carried values once and transfers successors through backedges;
- array update regions receive exclusive element ownership.

### 11.3 Dominance

Validation computes dominance within each region and structured dominance across parent/child regions. A parent operation's operands dominate its region entry arguments. Region-local definitions do not escape except through explicit terminator operands.

### 11.4 Logical resource lineage

FHQIR may compute a `ResourceLineage` analysis for debugging and symbolic extraction. A lineage is a chain of logical SSA successors rooted at a function argument or allocation. It MUST NOT be interpreted as a physical device identity.

Lineage is derived from operation resource-flow contracts and is not a mutable field on `Value`.

---

## 12. Operation Schema Registry

Every opcode is resolved through a registry.

```python
@dataclass(frozen=True)
class OperationSchema:
    name: OperationName
    version: SemanticVersion
    operand_spec: tuple[TypeConstraint, ...]
    result_spec: tuple[TypeConstraint, ...]
    region_spec: tuple[RegionConstraint, ...]
    attribute_spec: Mapping[str, AttributeConstraint]
    traits: frozenset[OperationTrait]
    effect_rule: EffectRule
    resource_flow: ResourceFlowContract
    symbolic_semantics_id: str | None
```

Suggested traits include:

```text
Terminator
Pure
Linear
Quantum
Measurement
Allocation
RegionBranch
LoopLike
CallLike
SymbolicallyInterpretable
HasNoSideEffects
```

The serialized module stores schema names and semantic versions, never Python callback implementations. Host applications map trusted IDs to implementations.

Unknown schemas MAY be parsed as opaque operations for round-trip tooling, but:

- full validation reports them as unsupported unless an explicit opaque contract is present;
- symbolic verification returns `Unknown`;
- backend lowering requires declared support.

---

## 13. Quantum Resource-Flow Contracts

Every operation with quantum operands or results MUST declare a leaf-level resource-flow contract.

```python
class ResultOrigin:
    pass

@dataclass(frozen=True)
class Successor(ResultOrigin):
    input_path: ValuePath

@dataclass(frozen=True)
class Fresh(ResultOrigin):
    allocation_class: str

@dataclass(frozen=True)
class ResourceFlowContract:
    quantum_inputs: tuple[ValuePath, ...]
    quantum_outputs: Mapping[ValuePath, ResultOrigin]
```

`ValuePath` identifies a quantum leaf in an operand or result aggregate, for example operand 1, tuple element 0.

Rules:

1. A quantum input path appears at most once in `Successor` outputs.
2. Two outputs MUST NOT claim the same predecessor.
3. A `Fresh` output requires the `allocation` effect or an explicitly trusted resource-creation trait.
4. An input with no successor is terminally consumed.
5. A pure unitary qop has a bijection between quantum inputs and outputs and has no `Fresh` outputs.
6. A non-destructive measurement normally maps its quantum output to the measured input and also produces classical data.
7. Resource-kind conversion requires an explicit trusted conversion trait; it is not inferred from unequal types.

Examples:

```text
H(Qubit) -> Qubit
  output 0 = Successor(input 0)

CX(Qubit, Qubit) -> (Qubit, Qubit)
  output 0 = Successor(input 0)
  output 1 = Successor(input 1)

measure_z(Qubit) -> (Qubit, Bit)
  output quantum leaf = Successor(input 0)

discard(Qubit) -> Void
  input 0 has no successor

alloc_qubit() -> Qubit
  output 0 = Fresh("qubit")
```

The registry MUST reject a qop contract that duplicates a predecessor, creates an undeclared resource, or contradicts its signature/effects.

---

## 14. Core Operation Set

FHQIR 1.0 defines the following operation families.

### 14.1 Constants and symbols

```text
builtin.constant
symbol.ref
```

`builtin.constant` has no operands, one classical result, and a canonical exact-value attribute. `symbol.ref` references a declared immutable symbolic parameter.

### 14.2 Classical arithmetic and logic

```text
arith.add  arith.sub  arith.mul  arith.div  arith.mod  arith.pow
arith.eq   arith.ne   arith.lt   arith.le   arith.gt   arith.ge
logic.and  logic.or   logic.not
bit.and    bit.or     bit.xor
cast.bool  cast.int   cast.uint  cast.float  cast.complex
complex.conjugate
```

These operations are pure. Their type rules mirror the frontend type checker and MUST be revalidated in IR.

### 14.3 Allocation

```text
%q = quantum.alloc_qubit
%m = quantum.alloc_qumode
%a = quantum.alloc_array {element = Qubit, extent = 4}
```

Allocation produces fresh linear resources and carries `{allocation}`.

### 14.4 Generic qop application

```text
%q1 = quantum.apply @std.dv.H(%q0)
%q2 = quantum.apply @std.dv.RZ(%theta, %q1)
%q3, %m1 = quantum.apply @std.hybrid.CD(%alpha, %q2, %m0)
```

The callee is a stable qop declaration reference. Operand/result types, effects, and resource flow come from the registry. The operation MUST NOT contain a backend gate object.

### 14.5 Measurement

```text
%q1, %bit = quantum.measure @std.measure.measure_z(%q0)
%m1, %n = quantum.measure @std.measure.measure_n(%m0)
```

Measurement is distinct from unitary application so analyses can reject or isolate it without inspecting arbitrary attributes. Destructive measurements omit a quantum successor only when the registered contract explicitly declares terminal consumption.

### 14.6 Function call

```text
%q1, %m1 = func.call @evolve(%q0, %m0, %alpha)
```

Calls consume all linear argument leaves. Defined functions receive a computed resource-flow summary. External calls require a declared summary and trust level.

Recursive quantum calls are unsupported in FHQIR 1.0 unless a later extension supplies a verified ownership/effect summary and termination contract. The validator rejects recursive cycles containing linear values by default.

### 14.7 Return

```text
func.return %q1, %result
```

`func.return` is a terminator. Its operands must exactly match the function ABI layout and transfer ownership out of the function.

### 14.8 Functional transforms

Reserved operations are:

```text
functional.adjoint
functional.controlled
functional.compose
functional.repeat
```

They hold function references and symbolic transform parameters, never eagerly expanded gates. Their eligibility rules use function effect summaries. These operations belong to Phase F but their representation is reserved now to avoid redesigning function values.

---

## 15. Source Aggregate Layout

Source tuples may be nested while IR operations use ordered flat operand/result lists. Lowering therefore uses an immutable `ValueTree` helper.

```python
@dataclass(frozen=True)
class ValueTree:
    source_type: Type
    leaves: tuple[Value, ...]
    layout: TypeLayout
```

`ValueTree` is not an independently ownable IR value. It is a compiler-side view mapping a source value shape to SSA leaves.

Rules:

- tuples are recursively flattened into ordered leaves for function and qop ABI;
- tuple structure is preserved by `TypeLayout` and source-map metadata;
- arrays remain one aggregate SSA value unless explicitly unpacked;
- an explicit first-class tuple needed by a generic operation uses `aggregate.pack_tuple` and `aggregate.unpack_tuple`;
- pack consumes all linear children; unpack consumes the aggregate and produces unique children.

The same canonical flattening function MUST be used by signatures, calls, returns, printer annotations, and symbolic extraction.

---

## 16. Array Semantics

Quantum arrays are linear containers. FHQIR MUST NOT model `array[index]` as a copyable quantum reference.

### 16.1 Classical arrays

Classical arrays may use:

```text
array.get
array.set
array.length
```

`array.get` is non-consuming only when the element type is non-linear.

### 16.2 Static quantum arrays

A statically sized quantum array may be consumed by:

```text
%q0, %q1 = array.unpack %qs : Array<Qubit, 2>
%qs2 = array.pack %q0n, %q1 : Array<Qubit, 2>
```

`array.unpack` consumes the whole array. Every returned element is independently linear. `array.pack` consumes every element and creates one new linear aggregate.

Current surface programs that extract all statically known elements and later rebuild the array lower to one unpack and one pack, even if extraction statements are separated in the AST. The semantic model supplies the proven extraction/reassembly facts.

### 16.3 Atomic element update

Static source updates such as `let qs[0] = H(qs[0]);` lower to `array.update`:

```text
%qs1 = array.update %qs0[%i] : Array<Qubit, dynamic> {
  ^entry(%element: Qubit, %token: ArrayUpdateToken<Qubit, dynamic>):
    %next = quantum.apply @std.dv.H(%element)
    array.yield %token, %next
}
```

Semantics:

- `array.update` consumes the input array;
- the region receives exclusive ownership of exactly one element;
- the token proves exclusive access to the unexposed remainder;
- the region must yield the same token and exactly one successor element;
- no quantum element or update token may escape the region;
- the result is one complete successor array;
- runtime bounds failure is represented by an explicit checked/trapping policy attribute, never ignored.

The current frontend emits authoritative `ArrayUpdateFact` records for compile-time indices, including grouped tuple updates. Extending the same operation to runtime indices remains future work and requires an explicit bounds policy. It does not create aliases.

### 16.4 Forbidden array states

A partially unpacked array is not a public FHQIR value. Array update tokens cannot cross calls, returns, ordinary yields, or serialization boundaries outside their owning update operation.

Returning, storing, or calling with an incomplete quantum array is invalid.

---

## 17. Structured If

Canonical form:

```text
%q1 = scf.if %condition carry(%q0) -> (Qubit) {
  ^then(%qt: Qubit):
    %q2 = quantum.apply @std.dv.H(%qt)
    scf.yield %q2
  ^else(%qe: Qubit):
    scf.yield %qe
}
```

Rules:

1. The condition is `Bool`.
2. Every outer linear value used by a branch is an explicit carried operand.
3. Both region entry blocks receive path-local arguments corresponding to carried operands.
4. A source `if` without `else` gets an explicit pass-through else region.
5. Every branch reaching the parent continuation ends in `scf.yield` with identical arity and types.
6. A branch may terminate with `func.return`, `scf.break`, or `scf.continue` when context permits; such a branch does not reach the parent continuation.
7. Results are defined on all paths that reach the continuation. SSA users after the operation are unreachable from non-yielding branches.
8. Branch-local linear values must be yielded, returned, terminally consumed, or diagnosed as leaks.

The `scf.if` operation carries `{control}` plus the union of nested effects.

---

## 18. Structured While

Canonical form:

```text
%q_final = scf.while (%q_iter = %q0) -> (Qubit) {
  ^condition(%qc: Qubit):
    %c = func.call @condition()
    scf.condition %c, %qc
  ^body(%qb: Qubit):
    %qn = quantum.apply @std.dv.H(%qb)
    scf.continue %qn
}
```

Rules:

1. Initial operands are consumed by the `scf.while` operation.
2. Condition block arguments are loop-carried ownership versions.
3. `scf.condition false, values...` yields final operation results.
4. `scf.condition true, values...` transfers values to body arguments.
5. `scf.continue` transfers successor values to the next condition evaluation.
6. `scf.break` transfers values directly to loop results.
7. Every backedge and break edge matches the loop result layout and types.
8. A body path may use `func.return`; that path exits the containing function.
9. Loop-carried linear shape is invariant even when concrete SSA IDs change.
10. The core represents the loop; proof of termination is not required for execution semantics.

FHQIR can represent `break/continue` with live quantum values only through explicit carried operands. The current Phase C conservative rejection may be relaxed after lowering and validation implement these contracts.

---

## 19. Structured For

`scf.for` is a convenience operation for classical iterables/ranges with explicit loop-carried values. It follows the same linear backedge rules as `scf.while`.

The canonicalization pass MAY lower `scf.for` to `scf.while`. Compile-time unrolling is an optional later transformation and MUST preserve source locations and linear ownership.

Dynamic iteration over a quantum array is not implicit borrowing. It requires an array-specific linear iteration operation or atomic update semantics.

---

## 20. Early Exits and Region Terminators

Legal terminators are context-sensitive:

```text
func.return
scf.yield
scf.condition
scf.continue
scf.break
array.yield
cf.unreachable
```

The validator computes each region's allowed successor set. A terminator either:

- transfers values to a parent continuation;
- transfers values to a loop edge;
- exits the function;
- declares the path unreachable.

Every transferred linear value is consumed by the terminator. Non-transferred live linear values on that path are leaks.

This multi-exit structured model permits one `if` branch to return while another yields to the continuation without flattening the source `if` into a gate list.

---

## 21. Function ABI and Calls

### 21.1 Canonical layout

Function arguments and results use the canonical `TypeLayout` flattening rule. The serialized signature records both logical source types and flattened ABI leaves.

### 21.2 Effect summary

Function effects are recomputed from the body and call graph. Serialized/inferred effects are checked, not blindly trusted.

### 21.3 Resource summary

For each function, analysis computes:

```python
@dataclass(frozen=True)
class FunctionResourceSummary:
    result_origins: Mapping[ValuePath, Successor | Fresh]
    terminal_inputs: frozenset[ValuePath]
```

A straight-line unitary qfunc normally has a bijective successor mapping. A function containing allocation may have fresh results. A terminal consumer may omit input successors.

### 21.4 Calls and inlining

Symbolic verification MAY inline defined calls. It MUST detect recursion and enforce an inlining budget. When not inlined, a call is interpretable only if a trusted symbolic summary exists.

---

## 22. AST-to-FHQIR Lowering

Lowering is deterministic and source-map preserving.

### 22.1 Environment

The lowerer maintains:

```text
BindingId -> ValueTree
SymbolId  -> module/function/qop reference
Control context -> allowed terminators
```

Source names are diagnostic labels only. Binding IDs from the semantic model select values.

### 22.2 Let and rebinding

Source:

```text
let q = H(q);
```

Lowering:

```text
%q1 = quantum.apply @std.dv.H(%q0)
```

The environment changes from the old `q` binding ID to the new binding ID. No IR mutation occurs.

### 22.3 Renamed successor

```text
let p = H(q);
```

also lowers to one successor value. The source map records that `%q1` is named `p`; the old `q` binding remains consumed for diagnostics.

### 22.4 Tuple destructuring

Qop multi-results bind directly through `TypeLayout`. A materialized tuple is unpacked by an explicit consuming aggregate operation.

### 22.5 Allocation

Allocation lowers to fresh-resource operations. A statically known array extent is retained in the IR type.

### 22.6 Calls

The semantic model supplies the exact callable target. The lowerer MUST NOT resolve calls by string lookup again.

### 22.7 If

The lowerer obtains branch carried values and joins from `OwnershipFacts`. Missing else branches are synthesized as pass-through regions. Branch-local source rebindings become yielded successor values.

### 22.8 While and for

Loop-carried binding versions become operation operands and block arguments. The lowerer emits explicit `condition`, `continue`, and `break` terminators.

### 22.9 Return

Return expressions are flattened according to the function result layout and emitted as `func.return` operands.

### 22.10 Unsupported source features

If semantic analysis accepted syntax whose IR semantics are not enabled, lowering fails with `LoweringUnsupported`; it MUST NOT emit malformed or partially interpreted IR.

---

## 23. IR Validation

Validation runs in deterministic layers and returns all safely discoverable diagnostics.

### 23.1 Structural validation

- unique IDs;
- valid parent ownership;
- no operation in multiple blocks;
- terminator placement;
- valid region ownership;
- valid symbol references.

### 23.2 Schema validation

- known opcode/schema version;
- operand, result, region, and attribute counts;
- required attributes present;
- unknown attributes rejected unless schema permits extensions.

### 23.3 Type validation

- operand/result constraints;
- function call signature;
- return/yield/loop edge layouts;
- array extent compatibility;
- internal token confinement.

### 23.4 SSA validation

- one definition per value;
- dominance;
- no use before definition;
- no cross-region escape except through terminators.

### 23.5 Linear ownership validation

- at most one consuming use per path;
- no unconsumed live value at exits;
- correct structured branch transfer;
- correct loop-carried transfer;
- aggregate pack/unpack ownership;
- array update token pairing;
- no duplicated resource-flow predecessor.

### 23.6 Effect validation

- operation effects match schemas;
- function effects equal normalized transitive body effects;
- a pure function contains no non-pure operations;
- unitary-only transforms satisfy eligibility constraints.

### 23.7 Resource-flow validation

- every quantum output has one legal origin;
- unitary contracts are bijective;
- fresh outputs are authorized;
- terminal consumption is explicit;
- call summaries match defined bodies.

### 23.8 Validation API

```python
@dataclass(frozen=True)
class IRDiagnostic:
    code: str
    message: str
    span: SourceSpan | None
    operation_id: OperationId | None
    notes: tuple[DiagnosticNote, ...]

def validate_module(module: IRModule) -> tuple[IRDiagnostic, ...]: ...
```

No downstream consumer may proceed when an error-severity diagnostic exists.

---

## 24. Symbolic Verifier Integration

### 24.1 Eligibility

The FHQIR symbolic verifier 1.0 accepts a function pair only when:

- logical function types and ABI layouts match;
- effects are exactly `{unitary}`;
- bodies are straight-line after permitted call inlining;
- no allocation, measurement, runtime control, or dynamic array operation exists;
- all quantum operations have trusted symbolic semantic IDs;
- all classical parameter subgraphs are symbolically translatable;
- resource-flow summaries are bijective.

Failure of eligibility returns `Unknown` with a reason.

### 24.2 Extraction

The verifier derives a `SymbolicProgram`:

```python
@dataclass(frozen=True)
class SymbolicProgram:
    input_wires: tuple[SymbolicWire, ...]
    operations: tuple[SymbolicOperation, ...]
    output_wires: tuple[SymbolicWire, ...]
    global_phase: SymbolicExpr
```

Wire identities derive from function input leaf order and resource-flow lineage, not source variable names or physical indices.

Output ordering is part of semantics.

### 24.3 Semantic registry

The verifier uses a separate trusted registry keyed by `symbolic_semantics_id`.

```python
@dataclass(frozen=True)
class SymbolicOperationSpec:
    semantic_id: str
    version: SemanticVersion
    family: str
    rewrite_tags: frozenset[str]
    exact_rules: tuple[RuleId, ...]
    matrix_provider_id: str | None
    phase_behavior: PhaseBehavior
```

Serialized IR stores IDs only. Executable providers are loaded from a trusted host registry.

### 24.4 Result domain

```text
Equivalent
NotEquivalent
Unknown
```

`Equivalent` requires a proof trace from trusted rewrites, a complete canonical subtheory, or an exact fallback calculation.

`NotEquivalent` requires an exact counterexample, incompatible proven canonical forms in a complete subtheory, or an exact matrix witness.

Structural mismatch after incomplete rewriting yields `Unknown`.

### 24.5 Global phase

Normalization tracks phase separately from operations. Exact mode compares the simplified phase difference with zero. Global-phase mode compares operation/output normal forms while allowing a scalar unit-modulus phase.

### 24.6 Matrix fallback

An optional exact matrix fallback is restricted to a configured small DV wire count. Matrices are created inside the verifier and never attached to ordinary FHQIR operations.

Floating-point random testing may search for candidate counterexamples but MUST NOT establish equivalence. A candidate becomes `NotEquivalent` only after exact or rigorously bounded confirmation.

### 24.7 Proof artifacts

Verification results SHOULD include:

- function/module content hashes;
- registry semantic versions;
- eligibility checks;
- rewrite trace or canonicalization certificate;
- assumptions used;
- optional exact counterexample;
- verifier version and resource limits.

This makes results reproducible and auditable.

---

## 25. Text Format

FHQIR has a deterministic UTF-8 textual form with LF as canonical serialized newline.

Example:

```text
fhq.module @demo version "1.0" {
  fhq.symbol @theta : Float

  fhq.func @rotate(%q0: Qubit, %theta0: Float) -> (Qubit)
      effects [unitary] {
    %q1 = quantum.apply @std.dv.RZ(%theta0, %q0)
        : (Float, Qubit) -> (Qubit)
    func.return %q1 : (Qubit)
  }
}
```

Requirements:

- deterministic declaration order;
- deterministic SSA numbering based on dominance-order traversal;
- sorted attribute keys and effect names;
- fully qualified symbol references;
- exact numeric literal spelling;
- explicit types at operation boundaries where inference could be ambiguous;
- optional source locations in a stable `loc(...)` suffix;
- comments have no semantic meaning.

The parser/printer MUST satisfy:

```text
parse(print(valid_module)) == semantically_equal(valid_module)
print(parse(text)) == canonical_text
```

### 25.1 Versioning

The module header contains a major/minor IR version.

- Major changes may alter semantics or required structure.
- Minor changes are backward-compatible additions.
- Unknown major versions are rejected.
- Unknown optional minor features are preserved only through explicit opaque-extension containers.

### 25.2 Non-text serialization

A JSON debug/export format MAY be provided but is not the canonical hash representation. A binary format is deferred until the text schema stabilizes.

---

## 26. Source Mapping and Diagnostics

Every function and operation SHOULD retain its primary `SourceSpan`. Inlined or synthesized operations also carry an origin chain:

```python
@dataclass(frozen=True)
class Origin:
    primary: SourceSpan | None
    callsite: SourceSpan | None
    generated_by: str | None
    parent: Origin | None
```

Diagnostics SHOULD report:

- primary operation/source location;
- related defining/consuming location;
- function and block names;
- stable diagnostic code;
- notes for inlining or synthesized control flow.

Synthesized pass-through branches are marked generated but point back to the source `if`.

---

## 27. Pass Infrastructure

```python
class IRPass(Protocol):
    name: str
    required_analyses: frozenset[type]
    preserved_analyses: frozenset[type]
    def run(self, module: FrozenIRModule, context: PassContext) -> PassResult: ...
```

The pass manager MUST:

- validate required preconditions;
- enforce deterministic pass order;
- invalidate stale analyses;
- apply resource/time budgets;
- optionally validate after every transforming pass in debug/test mode;
- preserve source origins through rewrites;
- never swallow diagnostics.

Initial analyses:

```text
DominanceAnalysis
UseDefAnalysis
LinearOwnershipAnalysis
EffectAnalysis
CallGraphAnalysis
ResourceFlowAnalysis
StaticArrayShapeAnalysis
SymbolDependencyAnalysis
```

Initial canonicalization passes MAY remove identity aggregate operations and normalize exact classical expressions, but optimization is not required for Phase D acceptance.

---

## 28. Python Package Layout

Recommended package structure:

```text
hyqf/
  ir/
    __init__.py
    ids.py
    types.py
    attributes.py
    value.py
    operation.py
    block.py
    region.py
    function.py
    module.py
    schema.py
    registry.py
    builder.py
    rewriter.py
    validation.py
    printer.py
    parser.py
    lowering.py
    source_map.py
    dialects/
      builtin.py
      arith.py
      quantum.py
      aggregate.py
      array.py
      scf.py
      functional.py
    analysis/
      dominance.py
      ownership.py
      effects.py
      call_graph.py
      resource_flow.py
      array_shape.py
    passes/
      manager.py
      canonicalize.py

  symbolic/
    expressions.py
    operations.py
    registry.py
    rules.py
    normalize.py
    phase.py

  verifier/
    result.py
    eligibility.py
    extract.py
    verify.py
    dv_matrix.py
```

FHQIR core modules MUST NOT import `hyqf.backend`, Qiskit, PennyLane, or concrete verifier engines.

---

## 29. Determinism, Hashing, and Caching

A frozen validated module has a semantic content hash computed from canonical text excluding non-semantic source locations and debug names.

Hash inputs include:

- IR major/minor semantic version;
- canonical types and exact attributes;
- operation schema names and versions;
- function bodies and signatures;
- qop resource-flow contracts;
- referenced symbolic-semantic IDs.

Source paths, timestamps, object IDs, use-list order, and Python hash randomization MUST NOT affect semantic hashes.

Verifier and analysis caches MUST additionally key on registry semantic versions, assumptions, configuration, and resource limits.

---

## 30. Performance and Resource Limits

The object model SHOULD support modules with at least hundreds of functions and hundreds of thousands of operations without requiring dense matrices.

Engineering requirements:

- construction and validation are linear or near-linear in ordinary module size;
- use lists avoid full-module scans for local rewrites;
- dominance and call-graph analyses are cached;
- recursive nesting depth is bounded or traversed iteratively when loading untrusted text;
- parsers enforce configurable limits on file size, operation count, region depth, attribute size, and exact-number size;
- symbolic normalization has step, time, expression-size, and matrix-dimension budgets;
- budget exhaustion returns a diagnostic or `Unknown`, never a partial proof.

---

## 31. Trust and Security Boundaries

FHQIR files and plugin-provided schemas may be untrusted.

- Parsing MUST NOT execute code.
- Attribute decoding MUST use a closed data model.
- Schema and symbolic provider IDs are data, not import strings to execute automatically.
- Executable schema/verifier providers are loaded only from an explicitly trusted host registry.
- A plugin rule cannot produce `Equivalent` unless its provider is marked trusted for proof.
- Untrusted rewrite rules may be used for exploration but their result status is at most `Unknown` without independent checking.
- External function/qop declarations require explicit trust and capability metadata.

---

## 32. Testing Strategy

### 32.1 Unit tests

- type and attribute equality/interner behavior;
- builder use-list maintenance;
- operation-schema validation;
- exact literal round trips;
- resource-flow contract validation;
- source-map propagation.

### 32.2 Golden text tests

Each input HyQF fixture lowers to a standalone `.fhqir` expected file. Printer output is byte-stable.

### 32.3 Negative validation fixtures

Manually malformed modules cover:

- duplicate IDs;
- use before definition;
- non-dominating use;
- double linear use;
- resource leak;
- invalid branch yield;
- invalid loop backedge;
- duplicate resource successor;
- unauthorized fresh resource;
- partial array escape;
- incorrect function return;
- effect mismatch;
- unknown schema/version.

### 32.4 Round-trip and property tests

- `parse(print(module))` semantic equality;
- randomized valid SSA construction followed by validation;
- mutation tests where one invariant is broken at a time;
- alpha-renaming invariance;
- deterministic hashes across processes;
- linear verifier never accepts a duplicated resource.

### 32.5 Symbolic-verifier tests

- structural equivalence under source renaming;
- output permutation sensitivity;
- `H;H`, `X;X`, and rotation fusion;
- exact versus global-phase mode;
- unknown qop returns `Unknown`;
- measurement/control/loop returns `Unknown`;
- exact matrix witness for small DV non-equivalence;
- proof replay and registry-version mismatch.

### 32.6 Integration tests

QFT, teleportation, and Shor fixtures MUST lower and validate. Only verifier-eligible unitary straight-line subsets are expected to prove equivalence.

---

## 33. Phase D Readiness Audit

The Phase A-C semantic contract was upgraded before FHQIR lowering. The completed prerequisites and remaining boundaries are recorded here.

### 33.1 AST identity and exact literals

- every parsed AST node has a deterministic preorder `NodeId` in addition to its source span;
- floating literals retain both their exact `Decimal` value and source lexeme;
- complex literals use `ExactComplex` decimal components and retain their source lexeme;
- pretty printing remains stable and can optionally display node IDs.

### 33.2 Semantic result

- `CheckedModule` preserves its global-symbol and effect interfaces and now exposes an immutable `SemanticModel`;
- the model records expression types, symbol resolution, binding versions, stable call targets, flow summaries, and ownership facts;
- the complete symbol catalog includes globals, parameters, local declarations, rebinding versions, and synthesized bindings, so every recorded `SymbolId` is resolvable;
- `SemanticModel.validate()` rejects dangling IDs, unknown call targets, inconsistent aliases, incomplete transfer partitions, and malformed summary/array/join facts before lowering;
- lowering MUST consume these facts as authoritative instead of rerunning frontend resolution.

### 33.3 Ownership facts

- `OwnershipChecker.check` returns immutable `OwnershipFacts` while retaining existing diagnostics;
- binding versions, resource uses, call transfers, terminal inputs, return exits, and branch/loop joins are persistent;
- defined functions have fixed-point resource summaries that distinguish successor, terminal, and fresh resources across call boundaries;
- each call transfer states whether its lineage is authoritative; recursive or shape-dependent cases are explicitly incomplete rather than positionally guessed;
- resource IDs represent logical lineage versions, not physical hardware locations.

### 33.4 Control-flow outcomes

- blocks and statements have path-aware summaries for fallthrough, return, break, continue, and unreachable;
- non-void functions require return on every reachable path;
- break and continue are rejected outside loops. Phase E still owns their FHQIR representation.

### 33.5 qop registry metadata

- operation signatures have qualified callable IDs, semantic versions, resource-flow contracts, and optional symbolic semantic IDs;
- standard qops expose stable symbolic IDs and validated input-to-output lineage;
- the registry rejects duplicate identities, duplicate predecessors, invalid paths, undeclared fresh outputs, and non-bijective unitary contracts.
- host-registered quantum operations require either an explicit contract or an explicit `positional` policy;
- a successor whose input and output resource kinds differ requires an explicit stable `resource_conversion_id`; unannotated kind conversion is rejected;
- source qops are formally restricted to the canonical positional policy in v0.1; non-positional ownership flow requires a host registry declaration.

### 33.6 Array shape facts

- static extents, per-element resource groups, and extraction events survive in the semantic model;
- compile-time indexed `let` updates emit atomic input/output lineage through `ArrayUpdateFact`;
- unknown-length arrays carry an explicit dynamic shape fact;
- runtime-indexed quantum extraction and update remain rejected until dynamic exclusive-borrow semantics are implemented.

### 33.7 Conservative lowering boundaries

- recursive functions that carry linear values have `complete=False` resource summaries and remain rejected by FHQIR 1.0 lowering;
- a function boundary that packs multiple independently tracked static-array elements into one source `Array<T>` may have a shape-dependent incomplete summary; Phase D must lower its recorded unpack/pack facts directly or issue a documented lowering-boundary diagnostic;
- an incomplete summary and a non-authoritative transfer are never accepted as proof input by the symbolic verifier.

### 33.8 Compatibility requirement

These migrations preserve the parser/type/ownership public behavior. FHQIR work must continue to add semantic data rather than weaken diagnostics or reinterpret previously valid programs.

---

## 34. Implementation Plan

### D0: Semantic model prerequisite

- [x] assign stable AST node, symbol, and binding IDs;
- [x] record node types and call targets;
- [x] return ownership/branch/array facts from Phase C;
- [x] preserve exact literal lexemes;
- [x] compute transitive effect and resource-flow summaries to a fixed point;
- [x] validate the complete semantic model before exposing it to lowering.

### D1: Core IR

- IDs, types, attributes, values, operations, blocks, regions, functions, modules;
- builder, use lists, source map;
- builtin/classical/quantum schemas.

### D2: Straight-line lowering

- constants, symbols, arithmetic;
- allocation, qop, measurement, call, return;
- tuples and static arrays;
- deterministic printer.

### D3: Core validation

- structure, schema, type, SSA, dominance;
- linear ownership, effects, resource-flow contracts;
- negative fixtures.

### E1: Structured control

- `scf.if` with pass-through else;
- `scf.while` and loop-carried values;
- early return, break, continue;
- control-flow validation and printing.

### E2: Serialization

- text parser;
- canonical round trip;
- versioning and semantic hash.

### F preparation

- symbolic function references;
- reserved functional transform operations;
- function effect/resource summaries.

### G1: Symbolic verifier MVP

- eligibility checker;
- symbolic extraction and exact classical expressions;
- trusted semantic registry;
- structural canonicalization and minimal rewrite rules;
- phase tracking;
- `Equivalent / NotEquivalent / Unknown` results and proof trace.

### G2: Exact fallback

- small DV exact matrix provider;
- exact counterexample reporting;
- strict resource budgets.

---

## 35. Acceptance Criteria

Phase D is complete only when all of the following are true:

1. Every currently valid straight-line Phase A-C fixture lowers to FHQIR or receives a documented, intentional lowering-boundary diagnostic.
2. Every emitted module passes all IR validation layers.
3. Invalid hand-authored IR cannot bypass linear ownership checks.
4. Source names may change without changing semantic hashes or verifier extraction.
5. `let q = H(q)` creates two distinct SSA values with one ownership edge.
6. qop aliases such as `CX(q, q)` cannot be constructed as valid FHQIR.
7. Measurement returns an explicit post-measurement resource when its contract is non-destructive.
8. Static quantum arrays unpack and repack without aliases or partial escape.
9. Canonical printer output is deterministic and covered by golden files.
10. Text round-trip preserves semantic equality.
11. No backend/verifier-specific object appears in core IR.

Phase E is complete when valid `if/while/for`, pass-through branches, loop-carried quantum values, and structured exits lower, print, round-trip, and validate.

The symbolic verifier MVP is complete when it can prove the required rotation-fusion and self-canceling identities, distinguish exact/global-phase modes, reject a proven non-equivalent small DV pair, and return reasoned `Unknown` for unsupported programs.

---

## 36. Deferred Extensions

The following are deliberately deferred but accommodated by the design:

- Qudit operation semantics;
- safe general dynamic quantum-array iteration;
- closures capturing quantum resources;
- recursive quantum functions;
- exception handling and asynchronous control;
- Gaussian `(S, d, phase)` CV canonicalization;
- measurement/control-flow equivalence;
- loop invariants and bounded model checking;
- differentiation and gradient IR;
- resource estimation dialects;
- backend scheduling and physical mapping dialects;
- binary serialization.

Each extension must preserve the global invariants and introduce an explicit versioned schema rather than weakening validation.

---

## 37. End-to-End Example

Source:

```text
qfunc conditional(q: Qubit, enabled: Bool) -> Qubit {
    if (enabled) {
        let q = H(q);
    }
    let q = RZ(0.5, q);
    return q;
}
```

Canonical FHQIR sketch:

```text
fhq.func @conditional(%q0: Qubit, %enabled: Bool) -> (Qubit)
    effects [control, unitary] {
  %q1 = scf.if %enabled carry(%q0) -> (Qubit) {
    ^then(%qt: Qubit):
      %q2 = quantum.apply @std.dv.H(%qt)
          : (Qubit) -> (Qubit)
      scf.yield %q2 : (Qubit)
    ^else(%qe: Qubit):
      scf.yield %qe : (Qubit)
  }
  %c0 = builtin.constant decimal<"0.5"> : Float
  %q3 = quantum.apply @std.dv.RZ(%c0, %q1)
      : (Float, Qubit) -> (Qubit)
  func.return %q3 : (Qubit)
}
```

Properties:

- `%q0` has one parent-level consuming use: `scf.if`;
- `%qt` and `%qe` are mutually exclusive path-local owners;
- both continuation paths yield exactly one `Qubit`;
- `%q1` is the only live merged successor;
- the exact decimal spelling is preserved;
- symbolic verification returns `Unknown` in v1 because runtime control is present;
- a backend may later lower the structured `if` only if its capabilities support runtime control.

---

## 38. Final Engineering Rule

FHQIR is the semantic firewall of HyQF. Frontend convenience syntax ends at lowering; verifier and backend assumptions begin only after independent IR validation. When a feature cannot be represented while preserving SSA, linear ownership, structured control flow, exact symbols, and explicit resource flow, the implementation must reject it or return `Unknown` rather than encode an approximation as valid FHQIR.
