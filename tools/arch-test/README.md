# RISC-V Architectural Certification Tests

`run.sh` runs the [RISC-V Architectural Certification Tests](https://github.com/riscv-non-isa/riscv-arch-test)
(riscv-arch-test, ACT4 framework) on this core with Zba, Zbb and Zbs enabled.

The tests are self-checking: the framework computes each test's expected results with the
[Sail](https://github.com/riscv/sail-riscv) RISC-V reference model, configured to match the
core, and compiles them into the test. Each test then runs on the Verilated core and reports
pass or fail on the testbench console.

## Configuration

`veer-eh1-rv32imcb/` is riscv-arch-test's own VeeR EH1 configuration
(`config/cores/veer-eh1/veer-eh1-rv32imc`, commit `3d5cc36`) with these changes:

- B, Zba, Zbb and Zbs added to the implemented extensions, and `MUTABLE_MISA_B: false`
  (`misa` is read-only and reads `0x4000_1106` with all three extensions configured)
- the same four extensions enabled in the Sail configuration (`sail.json`)
- renamed to `veer-eh1-rv32imcb`

The boot code, console and termination macros, linker script and memory map are unchanged.
The configuration matches the default build of this core, with all three extensions.

## Running

    tools/arch-test/run.sh

The script fetches riscv-arch-test at the pinned commit and installs its framework into a venv,
downloads Sail 0.16, builds a Verilator model of this repository, builds the test ELFs and runs
them all. Everything goes into `./arch-test-work` (set `WORK` to change it).

Requirements:

- git, make, curl, Python 3.10+ with the `venv` module (`python3-venv` on Debian and Ubuntu),
  Verilator, `riscv64-unknown-elf-objcopy`
- a compiler the framework accepts: GCC 15+ (the default, `riscv64-unknown-elf-gcc`) or
  Clang 20+ (`CC=clang`). With Clang the framework links with `lld` and disassembles with
  `llvm-objdump`, so both must be installed; set `OBJDUMP` if the objdump has another name
  (e.g. `OBJDUMP=llvm-objdump-21`)
- Ruby 3.2+ with bundler: the framework reads the configuration with the `udb` gem, which it
  installs from rubygems.org on first use (into the work directory)

To test only some extensions, set `EXTENSIONS`, e.g. `EXTENSIONS=Zba,Zbb,Zbs`.

### Extensions configured out

    BITMANIP=0 tools/arch-test/run.sh

builds the core with `-set=bitmanip_zba=0 -set=bitmanip_zbb=0 -set=bitmanip_zbs=0` and tests it
with riscv-arch-test's unmodified `veer-eh1-rv32imc` configuration. This checks that the core
is still a compliant RV32IMC core without the extensions, including the ALU changes that are
shared with the base instructions. The model goes into `model-rv32imc` and the results into
`results-rv32imc.txt` in the work directory, next to those of the default run.

## Result

With riscv-arch-test commit `3d5cc36` and Sail 0.16, all 160 tests pass (tests built with
Clang): 3 Zba, 18 Zbb and 8 Zbs tests, plus the I, M, Zmmul, Zca, Zicsr, Zicclsm, machine-mode
CSR, exception and interrupt tests.

With `BITMANIP=0`, all 131 tests of the RV32IMC configuration pass: the same list without the
Zba, Zbb and Zbs tests.

## CI

The `Test-EH1-Arch` job of the CI workflow (`.github/workflows/test-eh1-arch.yml`) runs both
variants, with GCC from the riscv-gnu-toolchain releases.

## Exclusions

Two tests are skipped by default (`EXCLUDE`), the same two riscv-arch-test's CI skips for the
upstream core. Both are properties of the upstream testbench and decoder, not of this change:

- `Zifencei`: the testbench's instruction memory never sees stores, so `fence.i` cannot pick up
  modified code
- `I-fence`: the decoder requires `fm = 0`, so `fence.tso` is illegal
