from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import print_ast
from hyqf.semantics.typecheck import TypeChecker

source = """
qfunc protocol(a: Complex) -> (Qubit, Qumode) {
    let q = new Qubit;
    let m = new Qumode;
    let q = H(q);
    let (q, m) = CD(a, q, m);
    return (q, m);
}
"""

program = parse_source(source, file="example.hyqf")
checked = TypeChecker().check(program)

print_ast(program)
print(checked.effects)
