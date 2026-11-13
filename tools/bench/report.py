#!/usr/bin/env python3
"""report.py [-r RESULTS] [-b BASECFG] CFG [CFG...]
Per-config summary over layouts.  A config's tags are CFG, CFG+fn8, CFG+fn32 (whatever exists in
RESULTS, default bench-work/results).  For each benchmark: mean cycles over layouts, % change of that mean vs the base config,
layout spread (max-min)/min of the config, mean mispredict / fetch-stall / decode-stall / misaligned
deltas vs base.  Geomean over benchmarks of the mean-cycle ratio, plus text-size geomean.
Rows marked '!' failed verification in at least one layout."""
import csv, sys, math, glob, os, argparse
ap = argparse.ArgumentParser(); ap.add_argument('-b', '--base', default='before')
ap.add_argument('-r', '--results', default='bench-work/results', help='directory with <tag>.csv files')
ap.add_argument('-l', '--layouts', default='default,fn8,fn32', help='layouts to average (tag suffixes)')
ap.add_argument('--pmu', action='store_true', help='print PMU deltas instead of spreads')
ap.add_argument('cfgs', nargs='+'); a = ap.parse_args()
R = a.results
def load(tag):
    p = f'{R}/{tag}.csv'
    if not os.path.exists(p): return None
    d = {}
    for r in csv.DictReader(open(p)):
        try:
            d[r['bench']] = dict(cyc=float(r['cycles'].rstrip('?')), inst=float(r['inst']), text=float(r['text'] or 0),
                                 bad=r['cycles'].endswith('?'), pmu=[float(r[k]) if r.get(k) else None for k in ('mispred','fetchstall','decstall','misalign')])
        except ValueError: pass
    return d
def cfgdata(c):
    runs = []
    for l in a.layouts.split(','):
        d = load(c if l == 'default' else f'{c}+{l}')
        if d: runs.append(d)
    if not runs: return None
    out = {}
    for b in runs[0]:
        rs = [r[b] for r in runs if b in r]
        cy = [r['cyc'] for r in rs]
        pm = [[r['pmu'][i] for r in rs if r['pmu'][i] is not None] for i in range(4)]
        out[b] = dict(cyc=sum(cy)/len(cy), spread=(max(cy)-min(cy))/min(cy), n=len(cy), text=sum(r['text'] for r in rs)/len(rs),
                      inst=sum(r['inst'] for r in rs)/len(rs), bad=any(r['bad'] for r in rs),
                      pmu=[sum(p)/len(p) if p else None for p in pm])
    return out
base = cfgdata(a.base)
if not base: sys.exit(f'no data for base config {a.base}')
cols = [(c, cfgdata(c)) for c in a.cfgs]
cols = [(c, d) for c, d in cols if d]
hdr = '%-15s %9s %5s' % ('bench', 'base cyc', 'n')
for c, _ in cols: hdr += ' %21s' % c[:21]
print(hdr)
print('%-15s %9s %5s' % ('', '', '') + ''.join(' %10s %10s' % (('dcyc%', 'dmispred') if a.pmu else ('dcyc%', 'spread%')) for _ in cols))
geo = {c: [] for c, _ in cols}; gtext = {c: [] for c, _ in cols}
for b in base:
    row = '%-15s %9.0f %5s' % (b, base[b]['cyc'], '%d' % base[b]['n'])
    for c, d in cols:
        if b not in d: row += ' %21s' % '-'; continue
        r = d[b]['cyc'] / base[b]['cyc']; geo[c].append(r)
        if base[b]['text'] and d[b]['text']: gtext[c].append(d[b]['text'] / base[b]['text'])
        if a.pmu:
            m0, m1 = base[b]['pmu'][0], d[b]['pmu'][0]
            second = '%+10.0f' % (m1 - m0) if m0 is not None and m1 is not None else '%10s' % ''
        else:
            second = '%9.1f%%' % (d[b]['spread'] * 100)
        row += ' %+9.2f%%%s%s' % ((r - 1) * 100, '!' if d[b]['bad'] else ' ', second)
    print(row)
def gm(x): return (math.exp(sum(map(math.log, x)) / len(x)) - 1) * 100 if x else 0
print('%-15s %9s %5s' % ('GEOMEAN', '', '') + ''.join(' %+9.2f%% %10s' % (gm(geo[c]), '') for c, _ in cols))
print('%-15s %9s %5s' % ('text geomean', '', '') + ''.join(' %+9.2f%% %10s' % (gm(gtext[c]), '') for c, _ in cols))
