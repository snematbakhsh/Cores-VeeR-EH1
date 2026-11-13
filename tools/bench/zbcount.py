#!/usr/bin/env python3
"""zbcount.py OUTDIR : static count of Zba/Zbb/Zbs instructions per benchmark, for one run of
run.sh (OUTDIR holds its objects and ELFs): in the benchmark's own objects ("app"), and in the
whole linked image ("image": app + runtime + libc + compiler builtins, only what the linker kept).
Uses llvm-objdump: $OBJDUMP, else next to $CLANG, else on PATH."""
import glob, os, re, shutil, subprocess, sys
from collections import Counter, defaultdict

def find_objdump():
    if os.environ.get('OBJDUMP'): return os.environ['OBJDUMP']
    clang = shutil.which(os.environ.get('CLANG', 'clang'))
    if clang:
        p = os.path.join(os.path.dirname(os.path.realpath(clang)), 'llvm-objdump')
        if os.path.exists(p): return p
    return shutil.which('llvm-objdump') or sys.exit('llvm-objdump not found; set OBJDUMP')
OBJDUMP = find_objdump()
EXT = {
    'Zba': ['sh1add', 'sh2add', 'sh3add'],
    'Zbb': ['andn', 'orn', 'xnor', 'clz', 'ctz', 'cpop', 'min', 'minu', 'max', 'maxu',
            'sext.b', 'sext.h', 'zext.h', 'rol', 'ror', 'rori', 'orc.b', 'rev8'],
    'Zbs': ['bclr', 'bclri', 'bext', 'bexti', 'binv', 'binvi', 'bset', 'bseti'],
}
OP2EXT = {op: e for e, ops in EXT.items() for op in ops}
# raw bytes print as 'ff010113' (newer LLVM) or '13 01 01 ff' (older)
INSN = re.compile(r'\s*[0-9a-f]+:\s+(?:[0-9a-f]{4,8}|(?:[0-9a-f]{2} ){1,3}[0-9a-f]{2})\s+([a-z0-9.]+)')

def count(path):
    out = subprocess.run([OBJDUMP, '-d', '--mattr=+zba,+zbb,+zbs', path], capture_output=True, text=True).stdout
    n, ops = 0, Counter()
    for line in out.splitlines():
        m = INSN.match(line)
        if not m: continue
        n += 1
        if m.group(1) in OP2EXT: ops[m.group(1)] += 1
    return n, ops

d = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
app, napp = defaultdict(Counter), Counter()
for o in sorted(glob.glob(os.path.join(d, '*.o'))):
    b = os.path.basename(o)
    if b.endswith('_main.o') or b in ('crt0.o', 'printf.o', 'beebsc.o'): continue
    bench = 'coremark' if b == 'cmark.o' else b.split('_')[0]   # objects are <bench>_<source>.o
    n, ops = count(o)
    napp[bench] += n; app[bench].update(ops)
print('%-16s %6s %5s %5s %5s %7s %6s  %s' % ('bench', 'insns', 'Zba', 'Zbb', 'Zbs', 'image', 'libZb', 'most used (app)'))
for b in sorted(napp):
    c = app[b]
    byext = Counter()
    for op, n in c.items(): byext[OP2EXT[op]] += n
    elf = os.path.join(d, b + '.elf')
    img = sum(count(elf)[1].values()) if os.path.exists(elf) else 0
    top = ', '.join('%s %d' % (op, n) for op, n in c.most_common(4))
    print('%-16s %6d %5d %5d %5d %7d %6d  %s' % (b, napp[b], byext['Zba'], byext['Zbb'], byext['Zbs'],
                                               img, img - sum(c.values()), top))
