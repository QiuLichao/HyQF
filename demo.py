from hyqf.frontend.parser import parse_source
from hyqf.frontend.pretty import print_ast
from hyqf.semantics.typecheck import TypeChecker

source = """
qop discard(q: Qubit) -> Void effects { measurement };

qfunc valid(q: Qubit) {
    discard(q);
}
"""

program = parse_source(source, file="example.hyqf")
checked = TypeChecker().check(program)

print_ast(program)
print(checked.effects)
