#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the self-checking Zba/Zbb/Zbs tests in testbench/tests/.

  gen_bitmanip_tests.py directed [--target eh1|qemu] [--seed N] > bitmanip.s
  gen_bitmanip_tests.py random   [--target eh1|qemu] [--seed N] [--len N] > bitmanip_random.s

Expected values come from the reference model below. --target qemu emits the same
checks as a Linux user-mode program (exit code 0 = pass) so the model can be
cross-checked against an independent implementation:

  gen_bitmanip_tests.py random --target qemu > t.s
  riscv64-unknown-elf-gcc -march=rv32imc_zba_zbb_zbs -mabi=ilp32 -nostdlib -static -o t t.s
  qemu-riscv32 -cpu rv32,zba=true,zbb=true,zbs=true ./t; echo $?

eh1 target layout: code in ICCM, data in DCCM, both preloaded by the testbench
(see bitmanip.ld). Operands reach the instruction under test through immediates,
DCCM loads (load-to-use goes to the secondary ALU), multiplies and back-to-back
results, and in both issue slots. Each extension's checks are compiled in only if
that extension is configured (RV_BITMANIP_ZB* in defines.h); otherwise its
instructions are checked to raise an illegal-instruction exception.
"""
import argparse, random, sys

M = 0xffffffff
def s32(x): x &= M; return x - (1 << 32) if x & 0x80000000 else x

# ---------------------------------------------------------------- reference model
def clz(a): return 32 - (a & M).bit_length()
def ctz(a): a &= M; return 32 if a == 0 else ((a & -a).bit_length() - 1)
def rol(a, s): s &= 31; return ((a << s) | (a >> (32 - s))) & M
def ror(a, s): s &= 31; return ((a >> s) | (a << (32 - s))) & M

RTYPE = {   # name: (fn(a,b), extension)
    'sh1add': (lambda a, b: ((a << 1) + b) & M, 'ZBA'),
    'sh2add': (lambda a, b: ((a << 2) + b) & M, 'ZBA'),
    'sh3add': (lambda a, b: ((a << 3) + b) & M, 'ZBA'),
    'andn':   (lambda a, b: a & ~b & M, 'ZBB'),
    'orn':    (lambda a, b: (a | ~b) & M, 'ZBB'),
    'xnor':   (lambda a, b: ~(a ^ b) & M, 'ZBB'),
    'max':    (lambda a, b: a if s32(a) >= s32(b) else b, 'ZBB'),
    'maxu':   (lambda a, b: a if a >= b else b, 'ZBB'),
    'min':    (lambda a, b: a if s32(a) < s32(b) else b, 'ZBB'),
    'minu':   (lambda a, b: a if a < b else b, 'ZBB'),
    'rol':    (lambda a, b: rol(a, b), 'ZBB'),
    'ror':    (lambda a, b: ror(a, b), 'ZBB'),
    'bclr':   (lambda a, b: a & ~(1 << (b & 31)) & M, 'ZBS'),
    'bext':   (lambda a, b: (a >> (b & 31)) & 1, 'ZBS'),
    'binv':   (lambda a, b: a ^ (1 << (b & 31)), 'ZBS'),
    'bset':   (lambda a, b: a | (1 << (b & 31)), 'ZBS'),
}
ITYPE = {   # name: (fn(a,shamt), extension); shamt 0..31
    'rori':  (lambda a, s: ror(a, s), 'ZBB'),
    'bclri': (lambda a, s: a & ~(1 << s) & M, 'ZBS'),
    'bexti': (lambda a, s: (a >> s) & 1, 'ZBS'),
    'binvi': (lambda a, s: a ^ (1 << s), 'ZBS'),
    'bseti': (lambda a, s: a | (1 << s), 'ZBS'),
}
UNARY = {   # name: (fn(a), extension)
    'clz':    (clz, 'ZBB'),
    'ctz':    (ctz, 'ZBB'),
    'cpop':   (lambda a: bin(a & M).count('1'), 'ZBB'),
    'sext.b': (lambda a: (a & 0xff) - ((a & 0x80) << 1) & M, 'ZBB'),
    'sext.h': (lambda a: (a & 0xffff) - ((a & 0x8000) << 1) & M, 'ZBB'),
    'zext.h': (lambda a: a & 0xffff, 'ZBB'),
    'orc.b':  (lambda a: sum((0xff << (8 * k)) for k in range(4) if (a >> (8 * k)) & 0xff), 'ZBB'),
    'rev8':   (lambda a: int.from_bytes((a & M).to_bytes(4, 'little'), 'big'), 'ZBB'),
}
BASE_R = {
    'add': lambda a, b: (a + b) & M, 'sub': lambda a, b: (a - b) & M,
    'and': lambda a, b: a & b, 'or': lambda a, b: a | b, 'xor': lambda a, b: a ^ b,
    'sll': lambda a, b: (a << (b & 31)) & M, 'srl': lambda a, b: a >> (b & 31),
    'sra': lambda a, b: (s32(a) >> (b & 31)) & M,
    'slt': lambda a, b: int(s32(a) < s32(b)), 'sltu': lambda a, b: int(a < b),
    'mul': lambda a, b: (a * b) & M,
    'mulh': lambda a, b: ((s32(a) * s32(b)) >> 32) & M,
    'mulhu': lambda a, b: ((a * b) >> 32) & M,
    'mulhsu': lambda a, b: ((s32(a) * b) >> 32) & M,
}
BASE_I = {
    'addi': lambda a, i: (a + i) & M, 'andi': lambda a, i: a & (i & M),
    'ori': lambda a, i: a | (i & M), 'xori': lambda a, i: a ^ (i & M),
    'slti': lambda a, i: int(s32(a) < i), 'sltiu': lambda a, i: int(a < (i & M)),
}
BASE_SH = {'slli': lambda a, s: (a << s) & M, 'srli': lambda a, s: a >> s,
           'srai': lambda a, s: (s32(a) >> s) & M}
BRANCH = {'beq': lambda a, b: a == b, 'bne': lambda a, b: a != b,
          'blt': lambda a, b: s32(a) < s32(b), 'bge': lambda a, b: s32(a) >= s32(b),
          'bltu': lambda a, b: a < b, 'bgeu': lambda a, b: a >= b}

# Encodings next to the implemented ones that must stay illegal on this core.
ALWAYS_ILLEGAL = [
    ('clmul a0,a1,a2',            0x0ac59533),
    ('clmulr a0,a1,a2',           0x0ac5a533),
    ('clmulh a0,a1,a2',           0x0ac5b533),
    ('pack a0,a1,a2 (rs2!=0)',    0x08c5c533),
    ('packh a0,a1,a2',            0x08c5f533),
    ('brev8 a0,a1',               0x6875d513),
    ('zip a0,a1',                 0x08f59513),
    ('unzip a0,a1',               0x08f5d513),
    ('xperm4 a0,a1,a2',           0x28c5a533),
    ('xperm8 a0,a1,a2',           0x28c5c533),
    ('rev8 (RV64 encoding)',      0x6b85d513),
    ('rori shamt[5]=1',           0x6215d513),
    ('bseti shamt[5]=1',          0x2a159513),
    ('bclri shamt[5]=1',          0x4a159513),
    ('binvi shamt[5]=1',          0x6a159513),
    ('bexti shamt[5]=1',          0x4a15d513),
    ('clz/ctz/cpop space 0x603',  0x60359513),
    ('clz/ctz/cpop space 0x606',  0x60659513),
    ('clz/ctz/cpop space 0x607',  0x60759513),
    ('gorci 0x283',               0x2835d513),
    ('zext.h (RV64 OP-32)',       0x0805c53b),
    ('sh1add.uw (RV64)',          0x20c5a53b),
]

def enc_r(f7, rs2, rs1, f3, rd, op): return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
# x10=a0, x11=a1, x12=a2. Implemented encodings, used to check trapping when configured out.
OP_ENC = {
    'sh1add': enc_r(0x10, 12, 11, 2, 10, 0x33), 'sh2add': enc_r(0x10, 12, 11, 4, 10, 0x33),
    'sh3add': enc_r(0x10, 12, 11, 6, 10, 0x33),
    'andn': enc_r(0x20, 12, 11, 7, 10, 0x33), 'orn': enc_r(0x20, 12, 11, 6, 10, 0x33),
    'xnor': enc_r(0x20, 12, 11, 4, 10, 0x33),
    'max': enc_r(0x05, 12, 11, 6, 10, 0x33), 'maxu': enc_r(0x05, 12, 11, 7, 10, 0x33),
    'min': enc_r(0x05, 12, 11, 4, 10, 0x33), 'minu': enc_r(0x05, 12, 11, 5, 10, 0x33),
    'rol': enc_r(0x30, 12, 11, 1, 10, 0x33), 'ror': enc_r(0x30, 12, 11, 5, 10, 0x33),
    'bclr': enc_r(0x24, 12, 11, 1, 10, 0x33), 'bext': enc_r(0x24, 12, 11, 5, 10, 0x33),
    'binv': enc_r(0x34, 12, 11, 1, 10, 0x33), 'bset': enc_r(0x14, 12, 11, 1, 10, 0x33),
    'rori': enc_r(0x30, 7, 11, 5, 10, 0x13), 'bclri': enc_r(0x24, 7, 11, 1, 10, 0x13),
    'bexti': enc_r(0x24, 7, 11, 5, 10, 0x13), 'binvi': enc_r(0x34, 7, 11, 1, 10, 0x13),
    'bseti': enc_r(0x14, 7, 11, 1, 10, 0x13),
    'clz': enc_r(0x30, 0, 11, 1, 10, 0x13), 'ctz': enc_r(0x30, 1, 11, 1, 10, 0x13),
    'cpop': enc_r(0x30, 2, 11, 1, 10, 0x13), 'sext.b': enc_r(0x30, 4, 11, 1, 10, 0x13),
    'sext.h': enc_r(0x30, 5, 11, 1, 10, 0x13), 'zext.h': enc_r(0x04, 0, 11, 4, 10, 0x33),
    'orc.b': enc_r(0x14, 7, 11, 5, 10, 0x13), 'rev8': enc_r(0x34, 24, 11, 5, 10, 0x13),
}

# ---------------------------------------------------------------- program skeleton
class Prog:
    def __init__(self, target):
        self.t = target
        self.lines = []
        self.data = []          # DCCM words (operand table / random test memory)
        self.base = 0           # offset of s0 into the table
        self.nchk = 0

    def emit(self, s): self.lines.append(s)

    def word(self, v):
        """add a data word; returns its offset from s0, moving s0 forward when the
        offset would no longer fit a 12-bit immediate"""
        self.data.append(v & M)
        off = 4 * (len(self.data) - 1)
        if off - self.base > 2040:
            self.emit(f'    addi s0, s0, {off - self.base}')
            self.base = off
        return off - self.base

    def check(self, reg, exp, tmp='t5'):
        """fail unless reg == exp; t6 holds the check number for the failure report"""
        self.nchk += 1
        self.emit(f'    li t6, {self.nchk}')
        self.emit(f'    li {tmp}, {exp:#x}')
        self.emit(f'    bne {reg}, {tmp}, fail')

    def render(self, title):
        o = []
        o.append(f'// {title}')
        o.append('// Generated by tools/gen_bitmanip_tests.py; do not edit.')
        if self.t == 'eh1':
            o.append('#include "defines.h"')
            o.append('#define STDOUT 0xd0580000')
        o.append('    .option arch, +zba, +zbb, +zbs')
        o.append('    .option norelax             // nothing here sets gp; keep la/call absolute')
        if self.t == 'eh1':
            o += EH1_PROLOGUE
        else:
            o += QEMU_PROLOGUE
        o += self.lines
        o += EH1_EPILOGUE if self.t == 'eh1' else QEMU_EPILOGUE
        o.append('    .section .data')
        o.append('    .balign 4')
        o.append('test_data:')
        for v in self.data:
            o.append(f'    .word {v:#010x}')
        o.append('    .word 0')
        return '\n'.join(o) + '\n'

EH1_PROLOGUE = '''
    .section .text.init, "ax"
    .global _start
_start:
    li t0, 0x59555555          // MRAC: cacheable, except region 0xd (mailbox)
    csrw 0x7c0, t0
    la t0, trap_handler
    csrw mtvec, t0
    la t0, test_main
    jr t0

    .text
trap_handler:                   // expected traps: illegal instruction, mtval = instruction
    csrr t3, mcause
    li t4, 2
    bne t3, t4, unexpected_trap
    csrr t3, mtval
    bne t3, s10, unexpected_trap
    csrr t3, mepc
    addi t3, t3, 4
    csrw mepc, t3
    addi s11, s11, 1
    mret
unexpected_trap:
    li t6, -1
    j fail

test_main:
    la s0, test_data
    li s11, 0
'''.split('\n')

EH1_EPILOGUE = '''
pass:
    li t0, STDOUT
    li t1, 0xff
    sb t1, 0(t0)
1:  j 1b

fail:                           // print "FAIL <check#>" (hex), then signal failure
    li t0, STDOUT
    la t1, fail_msg
2:  lbu t2, 0(t1)
    beqz t2, 3f
    sb t2, 0(t0)
    addi t1, t1, 1
    j 2b
3:  li t1, 28
4:  srl t2, t6, t1
    andi t2, t2, 15
    addi t2, t2, '0'
    li t3, '9'
    ble t2, t3, 5f
    addi t2, t2, 'a'-'0'-10
5:  sb t2, 0(t0)
    addi t1, t1, -4
    bgez t1, 4b
    li t2, 10
    sb t2, 0(t0)
    li t1, 1
    sb t1, 0(t0)
6:  j 6b

    .section .rodata
fail_msg:
    .asciz "\\nFAIL check #"
'''.split('\n')

QEMU_PROLOGUE = '''
    .text
    .global _start
_start:
    la s0, test_data
    li s11, 0
'''.split('\n')

QEMU_EPILOGUE = '''
pass:
    li a0, 0
    li a7, 93
    ecall
fail:
    li a0, 1
    li a7, 93
    ecall
'''.split('\n')

# ---------------------------------------------------------------- directed test
EDGE = [0, 1, 2, 3, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x10000,
        0x7fffffff, 0x80000000, 0x80000001, 0xfffffffe, 0xffffffff, 0x12345678,
        0x87654321, 0xdeadbeef, 0x00ff00ff, 0xff00ff00, 0x0f0f0f0f, 0x00010000, 0x01000000]

def operand_vectors(name, rng):
    v = []
    if name in UNARY:
        for k in range(32):
            v += [(1 << k,), ((1 << k) - 1,), (M ^ (1 << k),), (M << k & M,)]
        v += [(x,) for x in EDGE]
        for k in range(4):                       # one byte nonzero at a time (orc.b/rev8)
            v += [(0x80 << (8 * k),), (0x01 << (8 * k),)]
        v += [(rng.getrandbits(32),) for _ in range(16)]
    elif name in ITYPE:
        for s in range(32):
            v += [(rng.choice(EDGE), s), (rng.getrandbits(32), s), (1 << s, s), (M ^ (1 << s), s)]
    else:
        amount_op = name in ('rol', 'ror', 'bclr', 'bext', 'binv', 'bset')
        if amount_op:
            for s in range(32):
                v += [(0x87654321, s), (rng.getrandbits(32), s), (1 << s, s), (M ^ (1 << s), s)]
            # only rs2[4:0] counts
            v += [(0x12345678, s | (rng.getrandbits(27) << 5)) for s in (0, 1, 15, 31)]
        for a in EDGE[::2]:
            for b in EDGE[1::2]:
                v.append((a, b))
        if name.startswith(('min', 'max')):
            for a, b in [(0x7fffffff, 0x80000000), (0x80000000, 0x7fffffff), (0, M), (M, 0),
                         (5, 5), (M, M), (0x80000000, 0x80000000), (1, 0xfffffffe)]:
                v.append((a, b))
        v += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(16)]
    return v

def gen_directed(target, seed):
    rng = random.Random(seed)
    p = Prog(target)
    ops = list(RTYPE) + list(ITYPE) + list(UNARY)
    for name in ops:
        if name in RTYPE: fn, ext = RTYPE[name]
        elif name in ITYPE: fn, ext = ITYPE[name]
        else: fn, ext = UNARY[name]
        p.emit(f'\n// ---- {name}')
        # s0 moves forward inside a block as its data grows; set it outside the #ifdef so a
        # block that is compiled out (extension configured out) leaves the next one right
        p.emit(f'    la s0, test_data + {p.base}')
        if target == 'eh1':
            p.emit(f'#ifdef RV_BITMANIP_{ext}')
        for vec in operand_vectors(name, rng):
            a = vec[0]
            b = vec[1] if len(vec) > 1 else None
            exp = fn(a, b) if b is not None else fn(a)
            style = rng.randrange(6)
            # operand a delivery
            if style == 1:                       # load-to-use from DCCM -> secondary ALU
                off = p.word(a)
                p.emit(f'    lw a1, {off}(s0)')
            elif style == 2:                     # multiply result feeds the op -> secondary ALU
                p.emit(f'    li a3, {a:#x}')
                p.emit('    li a4, 1')
                p.emit('    mul a1, a3, a4')
            else:
                p.emit(f'    li a1, {a:#x}')
            if name in RTYPE:
                if style == 3:                   # second operand loaded as well
                    off = p.word(b)
                    p.emit(f'    lw a2, {off}(s0)')
                else:
                    p.emit(f'    li a2, {b:#x}')
                if rng.random() < 0.5:
                    p.emit('    nop')            # shift the op to the other issue slot
                p.emit(f'    {name} a0, a1, a2')
            elif name in ITYPE:
                if rng.random() < 0.5: p.emit('    nop')
                p.emit(f'    {name} a0, a1, {b}')
            else:
                if rng.random() < 0.5: p.emit('    nop')
                p.emit(f'    {name} a0, a1')
            if style == 4:                       # consume the result right away
                p.emit(f'    li a5, {exp:#x}')
                p.emit('    xor a6, a0, a5')
                p.check('a6', 0)
            elif style == 5:                     # store the result and read it back
                off = p.word(0)
                p.emit(f'    sw a0, {off}(s0)')
                p.emit(f'    lw a7, {off}(s0)')
                p.check('a7', exp)
            else:
                p.check('a0', exp)
            # rd = x0 must not be written
            if rng.random() < 0.05:
                if name in RTYPE: p.emit(f'    {name} x0, a1, a2')
                elif name in ITYPE: p.emit(f'    {name} x0, a1, {b}')
                else: p.emit(f'    {name} x0, a1')
                p.check('x0', 0)
        if target == 'eh1':
            p.emit('#else')
            p.emit('    li a1, 0x12345678')
            p.emit('    li a2, 7')
            illegal_check(p, name, OP_ENC[name])
            p.emit('#endif')
    if target == 'eh1':
        p.emit('\n// ---- encodings that must remain illegal')
        for desc, enc in ALWAYS_ILLEGAL:
            p.emit(f'// {desc}')
            illegal_check(p, desc, enc)
    p.emit('    j pass')
    return p.render('Directed test of the Zba, Zbb and Zbs instructions on VeeR EH1')

def illegal_check(p, desc, enc):
    p.emit(f'    li s10, {enc:#x}')
    p.emit('    mv s9, s11')
    p.emit(f'    .word {enc:#x}')
    p.emit('    addi s9, s9, 1')
    p.nchk += 1
    p.emit(f'    li t6, {p.nchk}')
    p.emit('    bne s9, s11, fail')

# ---------------------------------------------------------------- random test
def gen_random(target, seed, length):
    rng = random.Random(seed)
    p = Prog(target)
    # registers: s0 (x8) = data base; t5/t6 (x30/x31) used by the checks only;
    # s11 (x27) trap count, s10 (x26) expected mtval: leave both alone too.
    regs = [r for r in range(1, 32) if r not in (8, 26, 27, 30, 31)]
    R = {r: 0 for r in range(32)}
    NWORDS = 64
    mem = [rng.choice(EDGE + [rng.getrandbits(32) for _ in range(4)]) for _ in range(NWORDS)]
    for w in mem: p.word(w)
    for r in regs:
        R[r] = rng.choice(EDGE + [rng.getrandbits(32)] * 3)
        p.emit(f'    li x{r}, {R[r]:#x}')
    recent = []
    exts = ['ZBA', 'ZBB', 'ZBS']

    def src():
        if recent and rng.random() < 0.7:
            return rng.choice(recent[-3:])
        return rng.choice(regs + [0])

    def dst():
        if rng.random() < 0.03: return 0
        return rng.choice(regs)

    def wr(rd, v):
        if rd: R[rd] = v & M
        recent.append(rd)

    bm_r = list(RTYPE); bm_i = list(ITYPE); bm_u = list(UNARY)
    for n in range(length):
        k = rng.random()
        if k < 0.55:                            # bit manipulation
            c = rng.random()
            if c < 0.5:
                op = rng.choice(bm_r); a, b, d = src(), src(), dst()
                p.emit(f'    {op} x{d}, x{a}, x{b}'); wr(d, RTYPE[op][0](R[a], R[b]))
            elif c < 0.7:
                op = rng.choice(bm_i); a, d = src(), dst(); s = rng.randrange(32)
                p.emit(f'    {op} x{d}, x{a}, {s}'); wr(d, ITYPE[op][0](R[a], s))
            else:
                op = rng.choice(bm_u); a, d = src(), dst()
                p.emit(f'    {op} x{d}, x{a}'); wr(d, UNARY[op][0](R[a]))
        elif k < 0.68:                          # base ALU
            c = rng.random(); d = dst(); a = src()
            if c < 0.5:
                op = rng.choice([o for o in BASE_R if not o.startswith('mul')]); b = src()
                p.emit(f'    {op} x{d}, x{a}, x{b}'); wr(d, BASE_R[op](R[a], R[b]))
            elif c < 0.8:
                op = rng.choice(list(BASE_I)); i = rng.randrange(-2048, 2048)
                p.emit(f'    {op} x{d}, x{a}, {i}'); wr(d, BASE_I[op](R[a], i))
            else:
                op = rng.choice(list(BASE_SH)); s = rng.randrange(32)
                p.emit(f'    {op} x{d}, x{a}, {s}'); wr(d, BASE_SH[op](R[a], s))
        elif k < 0.80:                          # loads from DCCM
            d = dst(); w = rng.randrange(NWORDS); kind = rng.choice(['lw', 'lh', 'lhu', 'lb', 'lbu'])
            if kind == 'lw': off = 4 * w; v = mem[w]
            elif kind in ('lh', 'lhu'):
                hb = rng.choice([0, 2]); off = 4 * w + hb; v = (mem[w] >> (8 * hb)) & 0xffff
                if kind == 'lh': v = (v - ((v & 0x8000) << 1)) & M
            else:
                bb = rng.randrange(4); off = 4 * w + bb; v = (mem[w] >> (8 * bb)) & 0xff
                if kind == 'lb': v = (v - ((v & 0x80) << 1)) & M
            p.emit(f'    {kind} x{d}, {off}(s0)'); wr(d, v)
        elif k < 0.87:                          # stores of fresh results (store-data bypass)
            a = src(); w = rng.randrange(NWORDS); kind = rng.choice(['sw', 'sw', 'sh', 'sb'])
            if kind == 'sw':
                off = 4 * w; mem[w] = R[a]
            elif kind == 'sh':
                hb = rng.choice([0, 2]); off = 4 * w + hb
                mem[w] = (mem[w] & ~(0xffff << (8 * hb)) | ((R[a] & 0xffff) << (8 * hb))) & M
            else:
                bb = rng.randrange(4); off = 4 * w + bb
                mem[w] = (mem[w] & ~(0xff << (8 * bb)) | ((R[a] & 0xff) << (8 * bb))) & M
            p.emit(f'    {kind} x{a}, {off}(s0)')
        elif k < 0.93:                          # multiplies (results go to the secondary ALU users)
            op = rng.choice(['mul', 'mul', 'mulh', 'mulhu', 'mulhsu']); a, b, d = src(), src(), dst()
            p.emit(f'    {op} x{d}, x{a}, x{b}'); wr(d, BASE_R[op](R[a], R[b]))
        else:                                   # data-dependent branch over one bitmanip op
            op = rng.choice(list(BRANCH)); a, b = src(), src()
            taken = BRANCH[op](R[a], R[b])
            lbl = f'L_br{n}'
            p.emit(f'    {op} x{a}, x{b}, {lbl}')
            bop = rng.choice(bm_r); x, y, d = src(), src(), dst()
            p.emit(f'    {bop} x{d}, x{x}, x{y}')
            if not taken: wr(d, RTYPE[bop][0](R[x], R[y]))
            p.emit(f'{lbl}:')
    p.emit('\n// ---- check registers')
    for r in regs:
        p.check(f'x{r}', R[r])
    p.emit('// ---- check memory')
    for w in range(NWORDS):
        p.emit(f'    lw t5, {4 * w}(s0)')
        p.check('t5', mem[w], tmp='t4')
    p.emit('    j pass')
    body = p.render(f'Random Zba/Zbb/Zbs instruction stream (seed {seed}, {length} instructions) on VeeR EH1')
    if target == 'eh1':
        # every op in the stream needs all three extensions
        body = body.replace('test_main:\n', 'test_main:\n#if !defined(RV_BITMANIP_ZBA) || !defined(RV_BITMANIP_ZBB) || !defined(RV_BITMANIP_ZBS)\n    j pass\n#endif\n', 1)
    return body

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('kind', choices=['directed', 'random'])
    ap.add_argument('--target', choices=['eh1', 'qemu'], default='eh1')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--len', type=int, default=3000)
    a = ap.parse_args()
    if a.kind == 'directed':
        sys.stdout.write(gen_directed(a.target, a.seed))
    else:
        sys.stdout.write(gen_random(a.target, a.seed, a.len))

if __name__ == '__main__':
    main()
