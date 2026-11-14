#!/usr/bin/env python3
"""synth.py - synthesis estimates for the bit-manipulation extensions, with Yosys.

Synthesizes one ALU instance (exu_alu_ctl) and one instruction decoder (dec_dec_ctl) for
three variants: the base revision (upstream), this tree with Zba, Zbb and Zbs configured
out, and this tree with all three configured in. For each it reports

  - LUT6 count and LUT6 levels, from a depth-oriented mapping (the ABC script lut6.abc).
    Levels are counted from a register or input to the named output.
  - logic area in NAND2 equivalents, from a mapping to the cells in cells.lib.
    Flip-flops are not included.

These are estimates for comparing the variants with each other. LUT levels in particular
depend on the mapping recipe; timing in a given technology needs a run with its library.

Usage: tools/synth/synth.py [--work DIR] [--base REV]
Needs: yosys (tested with 0.52), git, perl. --base defaults to the upstream commit the
extensions were developed on; if it is not in the clone, the upstream column is skipped.
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BASE = "d04b1c7ae675a63dc4307cacfd10547ec937b928"
OFF = ["-set=bitmanip_zba=0", "-set=bitmanip_zbb=0", "-set=bitmanip_zbs=0"]
FILES = ["design/exu/exu_alu_ctl.sv", "design/dec/dec_decode_ctl.sv",
         "design/include/veer_types.sv", "design/lib/beh_lib.sv"]
# module: (source file, outputs to report levels for; None = the deepest output)
MODULES = {"exu_alu_ctl": ("design/exu/exu_alu_ctl.sv", ["flush_upper", "flush_path", "out"]),
           "dec_dec_ctl": ("design/dec/dec_decode_ctl.sv", None)}


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def defines(work, name, params):
    """Generate common_defines.vh for a configuration of this tree."""
    d = os.path.join(work, "cfg-" + name)
    os.makedirs(d, exist_ok=True)
    env = dict(os.environ, RV_ROOT=REPO, BUILD_PATH=d)
    run([os.path.join(REPO, "configs/veer.config"), "-target=default", "-set", "iccm_enable"] + params,
        env=env, cwd=d)
    return os.path.join(d, "common_defines.vh")


def shim(root, module, out):
    """Copy a module with the constructs the Yosys Verilog frontend does not accept rewritten:
    package types are referenced by qualified name instead of a wildcard import, $bits takes
    a variable, and the elaboration-time $error in rvdffe is dropped. None changes the logic."""
    src = open(os.path.join(root, MODULES[module][0])).read()
    m = re.search(r"^module %s\b.*?^endmodule" % module, src, re.S | re.M)
    text = m.group(0)
    text = re.sub(r"^\s*import veer_types::\*;\n", "", text, flags=re.M)
    text = text.replace("$bits(predict_pkt_t)", "$bits(pp_ff)")
    text = re.sub(r"(?<![:\w])(\w+_pkt_t)\b", r"veer_types::\1", text)
    lib = open(os.path.join(root, "design/lib/beh_lib.sv")).read()
    lib = re.sub(r"\$error\(.*?\);", "begin end", lib)
    open(out + ".sv", "w").write(text + "\n")
    open(out + ".lib.sv", "w").write(lib)
    return [out + ".lib.sv", out + ".sv"]


def yosys(root, defs, srcs, module, script, log):
    inc = "-I%s/design/include -I%s/design/lib" % (root, root)
    cmd = "read_verilog -sv %s %s %s/design/include/veer_types.sv %s; synth -flatten -top %s; %s" % (
        inc, defs, root, " ".join(srcs), module, script)
    try:
        run(["yosys", "-q", "-l", log, "-p", cmd])
    except subprocess.CalledProcessError as e:
        sys.exit("yosys failed, see %s\n%s" % (log, e.stderr[-500:]))


def levels(netlist, module, ports):
    """LUT count and LUT levels from registers and inputs to the given output ports."""
    m = json.load(open(netlist))["modules"][module]
    cells = [c for c in m["cells"].values() if c["type"] != "$scopeinfo"]
    driver = {}
    for c in cells:
        for p, bits in c["connections"].items():
            if c["port_directions"][p] == "output":
                for b in bits:
                    driver[b] = c
    memo = {}

    def level(b):
        if not isinstance(b, int) or b not in driver or driver[b]["type"] != "$lut":
            return 0
        if b not in memo:
            memo[b] = 0
            memo[b] = 1 + max(level(x) for x in driver[b]["connections"]["A"])
        return memo[b]

    sys.setrecursionlimit(100000)
    outs = {n: max(level(b) for b in p["bits"]) for n, p in m["ports"].items() if p["direction"] == "output"}
    lev = [outs[p] for p in ports] if ports else [max(outs.values())]
    return sum(c["type"] == "$lut" for c in cells), lev


def measure(work, name, root, defs, module):
    out = os.path.join(work, "%s-%s" % (name, module))
    srcs = shim(root, module, out)
    yosys(root, defs, srcs, module,
          "abc -script %s; opt_clean; write_json %s.json" % (os.path.join(HERE, "lut6.abc"), out),
          out + ".lut.log")
    luts, lev = levels(out + ".json", module, MODULES[module][1])
    lib = os.path.join(HERE, "cells.lib")
    yosys(root, defs, srcs, module,
          "abc -liberty %s; opt_clean; tee -o %s.area stat -liberty %s" % (lib, out, lib), out + ".gate.log")
    area = float(re.search(r"Chip area for (?:top )?module.*?:\s*([\d.]+)", open(out + ".area").read()).group(1))
    return luts, lev, area


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default="synth-work")
    ap.add_argument("--base", default=BASE)
    a = ap.parse_args()
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)

    off, on = defines(work, "off", OFF), defines(work, "on", [])
    variants = []
    base = os.path.join(work, "base")
    try:
        os.makedirs(base, exist_ok=True)
        tar = subprocess.run(["git", "-C", REPO, "archive", a.base] + FILES, check=True, capture_output=True)
        subprocess.run(["tar", "-x", "-C", base], input=tar.stdout, check=True)
        variants.append(("Upstream", "base", base, off))
    except subprocess.CalledProcessError:
        print("base revision %s not found, skipping the upstream column" % a.base, file=sys.stderr)
    variants += [("Extensions configured out", "off", REPO, off), ("Extensions configured in", "on", REPO, on)]

    res = {(v[1], mod): measure(work, v[1], v[2], v[3], mod) for v in variants for mod in MODULES}
    row = lambda label, f: print("| %s | %s |" % (label, " | ".join(f(res[(v[1], mod)]) for v in variants)))
    for mod, (_, ports) in MODULES.items():
        print("\n| `%s` | %s |\n| --- | %s |" % (mod, " | ".join(v[0] for v in variants), " | ".join("---:" for _ in variants)))
        for i, p in enumerate(ports or ["deepest output"]):
            row("LUT6 levels to %s" % ("`%s`" % p if ports else p), lambda r: str(r[1][i]))
        row("LUT6 count", lambda r: "{:,}".format(r[0]))
        row("Logic area (NAND2 eq)", lambda r: "{:,.0f}".format(r[2]))


if __name__ == "__main__":
    main()
