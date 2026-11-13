#!/usr/bin/env bash
# bench.sh - before/after benchmark of the Zba/Zbb/Zbs extensions on VeeR EH1, as reported in
# docs/bitmanip-performance.md. Every configuration builds ALL code for one ISA: the benchmark,
# the runtime (crt0, printf, Embench support, board main) and the libraries (build-libs.sh).
#
#   before    upstream core (d04b1c7),  rv32imc              (each layout in LAYOUTS)
#   after     this core,                rv32imc_zba_zbb_zbs  (each layout in LAYOUTS)
#   ctrl      this core,                rv32imc              (should match before)
#   zba, zbb, zbs                       this core, one extension (default layout)
# All with -O3 -mcpu=generic-rv32 -mtune=sifive-7-series.
#
# Environment:
#   WORK      work directory for everything generated (default ./bench-work)
#   CLANG     clang to use (default: clang on PATH); the report used LLVM 23.1.2
#   JOBS      configurations run in parallel (default: nproc)
#   CONFIGS   subset of "before after ctrl zba zbb zbs" (default: all)
#   LAYOUTS   layouts for before/after, from "default fn8 fn32" (default: all three;
#             fn8/fn32 = -falign-functions=8/32)
#   EMBENCH   existing Embench-IoT checkout at 09c2ed8 (default: cloned into $WORK)
#   LDPATH    passed to build-libs.sh
# Needs: git, python3, Verilator, riscv64-unknown-elf binutils + gcc, curl, meson, ninja, cmake.
# Outputs: $WORK/results/<config>[+layout].csv, a summary on stdout, and $WORK/report/
# (results table in Markdown and the SVG chart used in the report).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK=${WORK:-$PWD/bench-work}
mkdir -p "$WORK"; WORK="$(cd "$WORK" && pwd)"
CLANG=$(command -v "${CLANG:-clang}") || { echo "clang not found; set CLANG" >&2; exit 1; }
JOBS=${JOBS:-$(nproc)}
CONFIGS=${CONFIGS:-before after ctrl zba zbb zbs}
LAYOUTS=${LAYOUTS:-default fn8 fn32}
BASE=d04b1c7
EMBENCH_REV=09c2ed8c3b7008c95d08b038de4a3f6dc103ed70
export WORK CLANG

for t in git python3 verilator riscv64-unknown-elf-gcc riscv64-unknown-elf-objcopy riscv64-unknown-elf-size curl meson ninja cmake; do
  command -v "$t" > /dev/null || { echo "missing: $t" >&2; exit 1; }
done

# Embench-IoT at the commit the report used
EMBENCH=${EMBENCH:-$WORK/embench-iot}
if [ ! -d "$EMBENCH/src" ]; then
  git clone -q https://github.com/embench/embench-iot "$EMBENCH"
  git -C "$EMBENCH" checkout -q "$EMBENCH_REV"
fi
export EMBENCH

# Libraries for every ISA a selected configuration needs
variants=""
for c in $CONFIGS; do case $c in before|ctrl) variants+=" imc";; after) variants+=" all";; zba|zbb|zbs) variants+=" $c";; esac; done
"$HERE/build-libs.sh" $(echo $variants | tr ' ' '\n' | sort -u)

# Upstream core, exported from this repo's history
if [ ! -d "$WORK/stock/design" ]; then
  git -C "$REPO" cat-file -e "$BASE^{commit}" 2>/dev/null || { echo "upstream commit $BASE not in $REPO (shallow clone?)" >&2; exit 1; }
  mkdir -p "$WORK/stock" && git -C "$REPO" archive "$BASE" | tar -x -C "$WORK/stock"
fi

model() { # $1 = model dir, $2 = RTL tree
  mkdir -p "$1"
  [ -x "$1/obj_dir/Vtb_top" ] && return
  echo "building Verilator model in $1"
  (cd "$1" && make -f "$2/tools/Makefile" verilator-build target=default RV_ROOT="$2" \
      VERILATOR_MAKE_FLAGS='OPT_FAST="-O2"' \
      defines="snapshots/default/common_defines.vh $2/design/include/veer_types.sv -GMAX_CYCLES=200000000" > build.log 2>&1) \
    || { echo "Verilator build failed, see $1/build.log" >&2; exit 1; }
}
case " $CONFIGS " in *" before "*) model "$WORK/model-stock" "$WORK/stock";; esac
case " $CONFIGS " in *" after "*|*" ctrl "*|*" zb"*) model "$WORK/model-new" "$REPO";; esac

tags() { # TAG MODEL VARIANT LAYOUT
  for c in $CONFIGS; do
    case $c in
      before|after)
        local m=model-new v=all; [ $c = before ] && { m=model-stock; v=imc; }
        for l in $LAYOUTS; do
          case $l in default) echo "$c $m $v -";; fn8|fn32) echo "$c+$l $m $v -falign-functions=${l#fn}";;
                     *) echo "unknown layout $l" >&2; exit 1;; esac
        done;;
      ctrl) echo "ctrl model-new imc -";;
      zba|zbb|zbs) echo "$c model-new $c -";;
      *) echo "unknown config $c" >&2; exit 1;;
    esac
  done
}
one() { # TAG MODEL VARIANT LAYOUT
  local tag=$1 m lay=$4
  case $3 in imc) m=rv32imc_zicsr_zifencei;; all) m=rv32imc_zba_zbb_zbs_zicsr_zifencei;; *) m=rv32imc_$3_zicsr_zifencei;; esac
  [ "$lay" = - ] && lay=""
  local csv="$WORK/results/$tag.csv"
  [ -f "$csv" ] && [ "$(wc -l < "$csv")" -ge 21 ] && return 0
  mkdir -p "$WORK/results"
  TAG=$tag LAYOUT=$lay EXTRA="-march=$m" RTMARCH=$m LIBS=$WORK/libs/$3 MODEL=$WORK/$2 \
    OUT=$WORK/out/$tag CSV=$csv "$HERE/run.sh" > "$WORK/results/$tag.log" 2>&1
  echo "done: $tag"
}
export -f one; export HERE
tags | xargs -P "$JOBS" -L 1 bash -c 'one "$@"' _

R="$WORK/results"
# before vs after, averaged over the layouts; the other configurations only have the default
# layout, so they are compared with before's default layout
if [[ " $CONFIGS " == *" before "* ]]; then
  [[ " $CONFIGS " == *" after "* ]] && { echo; echo "== after vs before (mean over layouts)"; "$HERE/report.py" -r "$R" -b before after; }
  others=$(for c in $CONFIGS; do case $c in ctrl|zba|zbb|zbs) echo $c;; esac; done)
  [ -n "$others" ] && { echo; echo "== single configurations vs before (default layout)"; "$HERE/report.py" -r "$R" -l default -b before $others; }
fi
echo
[ -f "$R/after.csv" ] && "$HERE/zbcount.py" "$WORK/out/after"
[ -f "$R/before.csv" ] && [ -f "$R/after.csv" ] && "$HERE/mkdoc.py" "$R" "$WORK/report"
true
