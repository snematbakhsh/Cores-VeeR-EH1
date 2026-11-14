# Synthesis estimates

`synth.py` produces the synthesis tables in [`docs/bitmanip-performance.md`](../../docs/bitmanip-performance.md)
with [Yosys](https://github.com/YosysHQ/yosys).

    tools/synth/synth.py

It synthesizes one ALU instance (`exu_alu_ctl`) and one instruction decoder (`dec_dec_ctl`) for
three variants: the upstream base revision, this tree with Zba, Zbb and Zbs configured out, and
this tree with all three configured in. For each it prints

- the LUT6 count and the LUT6 levels from a register or input to the module's outputs, from a
  depth-oriented mapping (`lut6.abc`)
- the logic area in NAND2 equivalents, from a mapping to the generic cells in `cells.lib`
  (flip-flops are not included)

The numbers are for comparing the variants with each other. LUT levels depend on the mapping
recipe, and timing in a given technology needs a run with its cell library.

Requirements: Yosys (tested with 0.52), git, Perl. The upstream column needs the base commit
(`d04b1c7`) in the clone; `--base` selects another revision, and the column is skipped if the
revision is missing. Work files go to `./synth-work` (`--work`).

The Yosys Verilog frontend does not accept a few constructs in these modules, so the script
synthesizes copies with package types referenced by qualified name, `$bits` of a variable
instead of a type, and the elaboration-time `$error` of `rvdffe` removed. None of these changes
the logic.
