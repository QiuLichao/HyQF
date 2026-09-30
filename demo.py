from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import print_ast
from hyqf.semantics.typecheck import TypeChecker

source = """
qfunc bad() -> Qubit {
    let qs = new Qubit[2];
    let qs[0] = H(qs[0]);
    let again = qs[0];
    return qs[0];
}

"""

program = parse_source(source, file="example.hyqf")
checked = TypeChecker().check(program)

print_ast(program)
print(checked.effects)
