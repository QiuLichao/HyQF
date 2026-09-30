qop M(q: Qubit, p: Qubit) -> (Qubit, Qubit) effects { pure, unitary };
qfunc protocol(a: Complex) -> (Qubit, Qumode) {
    let q = new Qubit;
    let m = new Qumode;
    let (p, r) = M(q, q);
    return (p, m);
}