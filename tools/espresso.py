#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# Drop-in replacement for `espresso -Dso -oeqntott <file.e>` built on the espresso
# minimizer bundled with PyEDA (pip install pyeda), for hosts without the espresso
# binary. Reads a PLA produced by tools/coredecode (.type fr or fd), minimizes each
# output separately (as -Dso does) and prints eqntott-style equations that
# tools/addassign turns into SystemVerilog:
#
#   tools/coredecode -in decode > coredecode.e
#   tools/espresso.py coredecode.e > equations.eqn
#   tools/addassign -in equations.eqn -prefix out. > equations
#
# Semantics follow espresso: for .type fr, rows give the ON-set ('1') and OFF-set
# ('0') of each output and every other minterm is a don't-care; for .type fd ('1'
# ON, '-' DC) unlisted minterms are OFF.

import sys
from pyeda.boolalg.espresso import espresso, FTYPE, DTYPE, RTYPE, set_config

PC = {'0': 1, '1': 2, '-': 3}      # positional cube notation used by pyeda


def read_pla(path):
    ilb = ob = None
    ptype = 'fd'
    rows = []
    for line in open(path):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('.ilb'):
            ilb = line.split()[1:]
        elif line.startswith('.ob'):
            ob = line.split()[1:]
        elif line.startswith('.type'):
            ptype = line.split()[1]
        elif line.startswith('.'):
            continue
        else:
            parts = line.split()
            if len(parts) == 2 and set(parts[0]) <= set('01-') and set(parts[1]) <= set('01-~'):
                rows.append((parts[0], parts[1]))
    return ilb, ob, ptype, rows


def minimize(ninputs, rows, k, ptype):
    cover = set()
    for ins, outs in rows:
        o = outs[k]
        if ptype == 'fr':
            # '1' = ON, '0' = OFF; anything not listed is DC
            if o not in '01':
                continue
            val = 1 if o == '1' else 0
        else:
            # fd: '1' = ON, '-' = DC, '0' carries no information
            if o == '1':
                val = 1
            elif o == '-':
                val = 2
            else:
                continue
        cover.add((tuple(PC[c] for c in ins), (val,)))
    if not any(v == (1,) for _, v in cover):
        return []
    intype = FTYPE | (RTYPE if ptype == 'fr' else DTYPE)
    res = espresso(ninputs, 1, cover, intype=intype)
    return sorted((imp for imp, out in res if out[0] == 1), key=lambda t: t)


def term_str(imp, ilb):
    lits = []
    for v, name in zip(imp, ilb):
        if v == 1:
            lits.append('!' + name)
        elif v == 2:
            lits.append(name)
    return '(' + '&'.join(lits) + ')' if lits else '(1)'


def wrap(name, terms):
    # eqntott-like layout: "name = (t) | (t) | ...;" wrapped near 80 columns
    s = '%s = %s;' % (name, ' | '.join(terms)) if terms else '%s = ();' % name
    out, line = [], ''
    for tok in s.split(' '):
        if line and len(line) + 1 + len(tok) > 76:
            out.append(line)
            line = '    ' + tok
        else:
            line = (line + ' ' + tok) if line else tok
    out.append(line)
    return '\n'.join(out)


def main():
    set_config(single_expand=False, remove_essential=True, force_irredundant=True,
               unwrap_onset=True, recompute_onset=False, use_super_gasp=False)
    ilb, ob, ptype, rows = read_pla(sys.argv[1])
    ni = len(ilb)
    for k, name in enumerate(ob):
        imps = minimize(ni, rows, k, ptype)
        # deterministic order: fewer literals first, then by literal pattern (MSB first)
        imps.sort(key=lambda t: (sum(1 for v in t if v != 3), [(-1 if v == 3 else v) for v in t]))
        print(wrap(name, [term_str(t, ilb) for t in imps]))
        print()


if __name__ == '__main__':
    main()
