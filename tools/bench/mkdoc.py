#!/usr/bin/env python3
"""mkdoc.py RESULTS OUTDIR : the results table (OUTDIR/table.md) and chart (OUTDIR/bitmanip-cycles.svg)
of docs/bitmanip-performance.md, from the before/after CSVs bench.sh writes. Each benchmark's
cycles, instructions and code size are averaged over the layouts present (before, before+fn8,
before+fn32 and the same for after); the table is sorted by change in cycles."""
import csv, math, os, sys

if len(sys.argv) != 3: sys.exit(__doc__)
R, OUT = sys.argv[1], sys.argv[2]
LAYOUTS = ['', '+fn8', '+fn32']
def load(tag):
    p = os.path.join(R, tag + '.csv')
    return {r['bench']: r for r in csv.DictReader(open(p))} if os.path.exists(p) else None
lay = [l for l in LAYOUTS if load('before' + l) and load('after' + l)]
if not lay: sys.exit(f'no before/after results in {R}')
B = [load('before' + l) for l in lay]; A = [load('after' + l) for l in lay]
for L in B + A:
    for b, r in L.items():
        if r['cycles'].endswith('?') or r['cycles'] == 'NA': sys.exit(f'{b} failed its self-check; not writing a report')
def mean(L, b, k): v = [float(d[b][k]) for d in L]; return sum(v) / len(v)
gm = lambda x: (math.exp(sum(map(math.log, x)) / len(x)) - 1) * 100
rows = []
for b in B[0]:
    rows.append(dict(b=('CoreMark' if b == 'coremark' else b), bc=mean(B, b, 'cycles'), ac=mean(A, b, 'cycles'),
      dc=(mean(A, b, 'cycles') / mean(B, b, 'cycles') - 1) * 100, di=(mean(A, b, 'inst') / mean(B, b, 'inst') - 1) * 100,
      dt=(mean(A, b, 'text') / mean(B, b, 'text') - 1) * 100))
rows.sort(key=lambda r: r['dc'])
geo = gm([1 + r['dc'] / 100 for r in rows])
emb = [r for r in rows if r['b'] != 'CoreMark']
print('layouts: %d; cycles geomean %+.2f%% (Embench %+.2f%%), code size %+.2f%%, Embench instructions %+.2f%%' % (
    len(lay), geo, gm([1 + r['dc'] / 100 for r in emb]), gm([1 + r['dt'] / 100 for r in rows]), gm([1 + r['di'] / 100 for r in emb])))
f = lambda p: '0.0%' if abs(p) < 0.05 else ('%+.1f%%' % p).replace('-', '−')
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, 'table.md'), 'w') as o:
    o.write('| Benchmark | Before (cycles) | After (cycles) | Cycles | Instructions | Code size |\n| --- | ---: | ---: | ---: | ---: | ---: |\n')
    for r in rows:
        name = r['b'] + (' (ticks/iter)' if r['b'] == 'CoreMark' else '')
        o.write('| %s | %s | %s | %s | %s | %s |\n' % (name, format(round(r['bc']), ','), format(round(r['ac']), ','), f(r['dc']), f(r['di']), f(r['dt'])))
# SVG chart
left, right, y0, rowH, lo, hi = 170, 650, 100, 22, -28, 2
W = 760; H = y0 + len(rows) * rowH + 50
x = lambda v: left + (v - lo) / (hi - lo) * (right - left)
col = lambda p: '#3f9a5a' if p < -0.5 else ('#c8524b' if p > 0.5 else '#9aa0a6')
s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
     f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
     f'<text x="16" y="30" font-size="16" font-weight="600" fill="#1f2328">Zba/Zbb/Zbs cut VeeR EH1 cycles {abs(geo):.1f}% (geomean), {abs(rows[0]["dc"]):.0f}% on SHA-256</text>',
     f'<text x="16" y="52" fill="#59636e">Change in cycles per benchmark; all code, including libc and compiler builtins, built for the ISA; mean of {len(lay)} code layout{"s" if len(lay) > 1 else ""}</text>',
     f'<text x="16" y="72" fill="#59636e">Green: fewer cycles. Red: more cycles. Grey: within 0.5%.</text>',
     f'<line x1="{x(0)}" x2="{x(0)}" y1="{y0-8}" y2="{y0+len(rows)*rowH}" stroke="#8c959f"/>',
     f'<line x1="{x(geo):.1f}" x2="{x(geo):.1f}" y1="{y0-8}" y2="{y0+len(rows)*rowH}" stroke="#57606a" stroke-dasharray="4 3"/>',
     f'<text x="{x(geo)-6:.1f}" y="{y0+len(rows)*rowH+34}" text-anchor="end" fill="#59636e">geomean {geo:.2f}%</text>']
for v in (0, -10, -20):
    s.append(f'<text x="{x(v):.1f}" y="{y0+len(rows)*rowH+16}" text-anchor="middle" fill="#59636e">{v}%</text>')
vx = x(hi) + 14
s.append(f'<text x="{vx:.1f}" y="{y0-14}" fill="#59636e">cycles</text>')
for i, r in enumerate(rows):
    yy = y0 + i * rowH; x1 = min(x(0), x(r['dc'])); w = max(abs(x(r['dc']) - x(0)), 1.5)
    s.append(f'<text x="{left-12}" y="{yy+11}" text-anchor="end" fill="#1f2328">{r["b"]}</text>')
    s.append(f'<rect x="{x1:.1f}" y="{yy}" width="{w:.1f}" height="14" fill="{col(r["dc"])}" fill-opacity="0.75"><title>{r["b"]}: {f(r["dc"])}</title></rect>')
    s.append(f'<text x="{vx:.1f}" y="{yy+11}" fill="#1f2328">{f(r["dc"])}</text>')
s.append('</svg>')
open(os.path.join(OUT, 'bitmanip-cycles.svg'), 'w').write('\n'.join(s) + '\n')
print('wrote', os.path.join(OUT, 'table.md'), 'and', os.path.join(OUT, 'bitmanip-cycles.svg'))
