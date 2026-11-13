# Benchmark harness

These scripts produce the before/after numbers in
[docs/bitmanip-performance.md](../../docs/bitmanip-performance.md). They run CoreMark and 19
Embench-IoT benchmarks on Verilator models of the upstream core and of this one, with every
piece of code (benchmark, runtime, C library, compiler builtins) built for the ISA under test.

## Requirements

- clang/LLVM with RISC-V support. The report used the LLVM 23.1.2 release.
- Verilator (the report used 5.020)
- `riscv64-unknown-elf` binutils and gcc, used for `crt0`, linking and the hex file
- git, python3, curl, meson, ninja, cmake
- a clone of this repository with history, so that upstream commit `d04b1c7` is available

## Running

From any directory:

    CLANG=/path/to/clang /path/to/Cores-VeeR-EH1/tools/bench/bench.sh

Everything is written under `./bench-work` (set `WORK` to change that):

| Path | Contents |
| --- | --- |
| `libsrc/`, `libs/<isa>/` | picolibc 1.8.10 and compiler-rt sources, and both built once per ISA |
| `embench-iot/` | Embench-IoT, cloned at commit `09c2ed8` |
| `stock/` | the upstream core, exported from git history |
| `model-stock/`, `model-new/` | Verilator builds of the two cores |
| `out/<tag>/` | objects, ELFs and simulation logs per configuration |
| `results/<tag>.csv` | cycles, instructions, code size and four performance counters per benchmark |
| `report/` | the results table (`table.md`) and chart (`bitmanip-cycles.svg`) used in the report |

The script prints `after` against `before` (averaged over the layouts), the other configurations
against `before`'s default layout, and a count of the new instructions per benchmark.
It skips steps that are already done, so an interrupted run can be restarted.

A full run is 12 configurations of 20 programs and takes about an hour on two cores,
including the library and Verilator builds. Use `JOBS` to run configurations in parallel
(default: one per CPU). To try something quickly, narrow it down. For example, only the default
layout of before and after:

    CONFIGS="before after" LAYOUTS=default tools/bench/bench.sh

## Configurations

| Tag | Core | Code built for |
| --- | --- | --- |
| `before` | upstream `d04b1c7` | `rv32imc_zicsr_zifencei` |
| `after` | this core | `rv32imc_zba_zbb_zbs_zicsr_zifencei` |
| `ctrl` | this core | `rv32imc_zicsr_zifencei`; should match `before` cycle for cycle |
| `zba`, `zbb`, `zbs` | this core | `rv32imc` plus one extension |

All use `-O3 -mcpu=generic-rv32 -mtune=sifive-7-series`. `before` and `after` also run with
`-falign-functions=8` and `=32` (tags `+fn8` and `+fn32`), and the report averages the three
layouts. This damps the effect of code placement on fetch and branch prediction, which on its own
moves some benchmarks by several percent.

## Scripts

- `bench.sh` runs everything above.
- `build-libs.sh` builds picolibc and the compiler-rt builtins for one or more ISAs.
- `run.sh` builds and runs every benchmark for one configuration and writes a CSV. Use it
  directly to try other compiler flags; its header lists the inputs.
- `report.py` compares configurations, averaging over layouts.
- `zbcount.py` counts the new instructions in the benchmark code and in the linked image.
- `mkdoc.py` writes the table and chart for the report.
- `board/` holds the EH1 support for Embench. Its `main` times one call of the benchmark with
  `mcycle`/`minstret` and four performance counters, and reports through the testbench mailbox.
  `eh1.ld` puts code in ICCM and data in DCCM, with the runtime and library code first so their
  placement doesn't change between configurations.

## Notes

- Measurements are deterministic: a configuration gives the same cycle count on every run.
- If clang's own `ld.lld` doesn't run on your system, point `LDPATH` at another `ld.lld`. It is
  only used while building picolibc.
- Embench-IoT is GPL-3.0 and is downloaded, not included here.
