#!/usr/bin/env bash
# run.sh - build CoreMark and the Embench-IoT benchmarks for one ISA and set of compiler flags,
# run each on a Verilated VeeR EH1 model, and write one CSV row per benchmark:
#   bench,cycles,inst,text,mispred,fetchstall,decstall,misalign
# cycles/inst cover the measured benchmark() call only (CoreMark: ticks per iteration). A
# cycles value ending in '?' means the run failed its self-check. Normally driven by bench.sh.
#
# Required environment:
#   MODEL     directory holding a Verilator build (obj_dir/Vtb_top, snapshots/default)
#   LIBS      libraries built by build-libs.sh for RTMARCH: include/, lib/libc.a,
#             lib/libclang_rt.builtins.a
#   EMBENCH   Embench-IoT checkout (src/, support/)
#   OUT       directory for objects, ELFs and logs;  CSV: results file to write
# Optional:
#   TAG       name printed in the log (default: basename of OUT)
#   CLANG     clang to use (default: clang on PATH)
#   CPU       -mcpu (default generic-rv32);  TUNE: tuning flags (default -mtune=sifive-7-series)
#   OPT       optimization level (default O3);  ITERS: CoreMark iterations (default 10)
#   EXTRA     extra flags for the benchmark code, e.g. -march=rv32imc_zba_zicsr_zifencei
#   LAYOUT    code-placement flags, e.g. -falign-functions=8
#   RTMARCH   -march for everything that is not benchmark code: crt0, printf, the Embench
#             support code and board main (default rv32imc_zicsr_zifencei). Match LIBS.
#   RV_ROOT   tree whose testbench/asm provides crt0.s, printf.c, cmark.c (default: this repo)
#   APPEND=1  add rows to an existing CSV instead of starting a new one
# Needs riscv64-unknown-elf-{gcc,objcopy,size} for crt0, linking and the hex file.
#
# Usage: run.sh [bench...]   (default: coremark and every benchmark in $EMBENCH/src)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${MODEL:?set MODEL}" "${LIBS:?set LIBS}" "${EMBENCH:?set EMBENCH}" "${OUT:?set OUT}" "${CSV:?set CSV}"
CLANG="${CLANG:-clang}"
CPU="${CPU:-generic-rv32}"
TUNE="${TUNE:--mtune=sifive-7-series}"
OPT="${OPT:-O3}"
ITERS="${ITERS:-10}"
EXTRA="${EXTRA:-}"
LAYOUT="${LAYOUT:-}"
RTMARCH="${RTMARCH:-rv32imc_zicsr_zifencei}"
RV_ROOT="${RV_ROOT:-$(cd "$HERE/../.." && pwd)}"
TAG="${TAG:-$(basename "$OUT")}"
ASM="$RV_ROOT/testbench/asm"
BOARD="$HERE/board"
G=riscv64-unknown-elf
ARCH="-march=rv32imc_zicsr_zifencei -mabi=ilp32"
BUILD_DIR="$MODEL/snapshots/default"
mkdir -p "$OUT" "$(dirname "$CSV")"
OUT="$(cd "$OUT" && pwd)"
[ -n "${APPEND:-}" ] || echo "bench,cycles,inst,text,mispred,fetchstall,decstall,misalign" > "$CSV"

LINKLIBS="-L$LIBS/lib -lc $LIBS/lib/libclang_rt.builtins.a"
COMMON="--target=riscv32-unknown-elf $ARCH -ffreestanding -nostdlib -I$BUILD_DIR -isystem $LIBS/include"
CFLAGS="$COMMON -mcpu=$CPU $TUNE -$OPT $EXTRA $LAYOUT"
# Runtime (support code, CoreMark glue): fixed flags, ISA from RTMARCH.
RTFLAGS="$COMMON -march=$RTMARCH -O3 -mtune=generic"

run_elf() { # $1=elf $2=log ; sets TEXT. Simulates in $OUT so several runs can go in parallel.
  local elf="$1" log="$2"
  (cd "$OUT" && ln -sf /dev/null exec.log && ln -sf /dev/null trace_port.csv \
   && "$G-objcopy" -O verilog "$elf" program.hex && timeout 1200 "$MODEL/obj_dir/Vtb_top" > "$log" 2>&1; rm -f program.hex)
  TEXT=$("$G-size" "$elf" | awk 'NR==2{print $1}')
}

benches="${*:-coremark $(ls "$EMBENCH/src")}"

cd "$OUT"
"$G-gcc" -march=$RTMARCH -mabi=ilp32 -I"$BUILD_DIR" -x assembler-with-cpp -c "$ASM/crt0.s" -o crt0.o
"$CLANG" $RTFLAGS -w -c "$ASM/printf.c" -o printf.o
"$CLANG" $RTFLAGS -I"$EMBENCH/support" -I"$BOARD" -DWARMUP_HEAT=1 -DBENCH_NAME='"x"' -c "$EMBENCH/support/beebsc.c" -o beebsc.o

echo "== $TAG"
for b in $benches; do
  CYC=NA; INST=NA; TEXT=NA; PMU=",,,"
  if [ "$b" = coremark ]; then
    # The testbench's cmark.c hard-codes ITERATIONS 1; run ITERS iterations, report ticks per iteration.
    sed "s/^#define ITERATIONS 1\$/#define ITERATIONS $ITERS/" "$ASM/cmark.c" > "$OUT/cmark.c"
    "$CLANG" $CFLAGS -w -include stdint.h -c "$OUT/cmark.c" -o "$OUT/cmark.o" \
    && "$G-gcc" $ARCH -nostdlib -T"$ASM/cmark_iccm.ld" -nostartfiles -o "$OUT/coremark.elf" crt0.o printf.o "$OUT/cmark.o" "$LIBS/lib/libclang_rt.builtins.a" \
    && run_elf "$OUT/coremark.elf" "$OUT/coremark.log" \
    && CYC=$(( $(grep -E "^Total ticks" "$OUT/coremark.log" | awk '{print $4}') / ITERS )) \
    && INST=$(grep -E "Finished : minstret" "$OUT/coremark.log" | sed -E 's/.*minstret = ([0-9]+).*/\1/')
    grep -q "Correct operation validated" "$OUT/coremark.log" 2>/dev/null || CYC="${CYC}?"
  elif [ -d "$EMBENCH/src/$b" ]; then
    objs=""; ok=1
    for src in "$EMBENCH/src/$b"/*.c; do
      o="$OUT/${b}_$(basename "$src" .c).o"
      "$CLANG" $CFLAGS -I"$EMBENCH/support" -I"$BOARD" -DWARMUP_HEAT=1 -DGLOBAL_SCALE_FACTOR=1 -w -c "$src" -o "$o" || { ok=0; break; }
      objs="$objs $o"
    done
    [ $ok = 1 ] && "$CLANG" $RTFLAGS -I"$EMBENCH/support" -I"$BOARD" -DWARMUP_HEAT=1 -DBENCH_NAME="\"$b\"" -c "$BOARD/support_main.c" -o "$OUT/${b}_main.o" || ok=0
    [ $ok = 1 ] \
    && "$G-gcc" $ARCH -nostdlib -T"$BOARD/eh1.ld" -nostartfiles -o "$OUT/$b.elf" crt0.o $objs "$OUT/${b}_main.o" beebsc.o $LINKLIBS \
    && run_elf "$OUT/$b.elf" "$OUT/$b.log" \
    && CYC=$(grep -E " cyc=" "$OUT/$b.log" | sed -E 's/.* cyc=([0-9]+).*/\1/') \
    && INST=$(grep -E " cyc=" "$OUT/$b.log" | sed -E 's/.* inst=([0-9]+).*/\1/')
    grep -q "VERIFY_OK" "$OUT/$b.log" 2>/dev/null || CYC="${CYC}?"
    PMU=$(grep -E " cyc=" "$OUT/$b.log" | sed -E 's/.* mispred=([0-9]+) fetchstall=([0-9]+) decstall=([0-9]+) misalign=([0-9]+).*/\1,\2,\3,\4/')
  else
    echo "unknown bench $b" >&2; continue
  fi
  [ -n "$CYC" ] || CYC=NA
  printf '%-16s %10s %10s %7s  %s\n' "$b" "$CYC" "$INST" "$TEXT" "$PMU"
  echo "$b,$CYC,$INST,$TEXT,$PMU" >> "$CSV"
done
