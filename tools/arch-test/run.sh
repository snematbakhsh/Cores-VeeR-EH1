#!/usr/bin/env bash
# run.sh - run the RISC-V Architectural Certification Tests (riscv-arch-test, ACT4 framework)
# on this core, with Zba, Zbb and Zbs enabled. The config in veer-eh1-rv32imcb/ is the
# riscv-arch-test VeeR EH1 config with the bit-manipulation extensions added.
#
# Steps: fetch riscv-arch-test at a pinned commit and install its framework into a venv;
# fetch the Sail reference model; build a Verilator model of this repository (default
# configuration: all three extensions); generate the tests and build them into self-checking
# ELFs, with expected results computed by Sail; run every ELF on the model.
#
# Environment:
#   WORK        work directory (default ./arch-test-work)
#   JOBS        parallel jobs (default: nproc)
#   EXTENSIONS  comma-separated subset of extensions to test, e.g. Zba,Zbb,Zbs
#               (default: every extension the config implements)
#   CC          compiler for the tests: GCC 15+ or Clang 20+ (default riscv64-unknown-elf-gcc)
#   OBJDUMP     matching objdump (default: llvm-objdump for Clang, else riscv64-unknown-elf-objdump)
#   EXCLUDE     extensions to skip (default: Zifencei,I-fence, as riscv-arch-test's own CI does
#               for upstream EH1: the testbench's instruction memory ignores stores, so fence.i
#               cannot see modified code; and the decoder rejects fence.tso)
# Needs: git, python3 (3.10+, with venv), Ruby 3.2+ with bundler (the framework installs its
# config gems from rubygems.org), make, curl, Verilator, riscv64-unknown-elf-objcopy, and the
# compiler (with lld, for Clang).
# Results: a pass/fail line per test, a summary, and $WORK/results.txt.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK=${WORK:-$PWD/arch-test-work}
mkdir -p "$WORK"; WORK="$(cd "$WORK" && pwd)"
JOBS=${JOBS:-$(nproc)}
EXCLUDE=${EXCLUDE-Zifencei,I-fence}
EXTENSIONS=${EXTENSIONS:-}
CC=${CC:-riscv64-unknown-elf-gcc}
case "$CC" in *clang*) OBJDUMP=${OBJDUMP:-llvm-objdump};; *) OBJDUMP=${OBJDUMP:-riscv64-unknown-elf-objdump};; esac
ACT_REPO=https://github.com/riscv-non-isa/riscv-arch-test
ACT_REV=3d5cc368e64565360dd57e9811ad863cf325beca
SAIL_VER=0.16
CFG="$HERE/veer-eh1-rv32imcb"
NAME=veer-eh1-rv32imcb

for t in git python3 ruby bundle make curl verilator riscv64-unknown-elf-objcopy "$CC" "$OBJDUMP"; do
  command -v "$t" > /dev/null || { echo "missing: $t" >&2; exit 1; }
done

ACT="$WORK/riscv-arch-test"
if [ ! -d "$ACT/.git" ]; then
  git init -q "$ACT"
  git -C "$ACT" remote add origin "$ACT_REPO"
fi
if [ "$(git -C "$ACT" rev-parse -q --verify HEAD || true)" != "$ACT_REV" ]; then
  git -C "$ACT" fetch -q --depth 1 origin "$ACT_REV"
  git -C "$ACT" checkout -q FETCH_HEAD
fi

if [ ! -x "$WORK/venv/bin/act" ]; then
  python3 -m venv "$WORK/venv"
  "$WORK/venv/bin/pip" install -q -e "$ACT/framework" -e "$ACT/generators/testgen" -e "$ACT/generators/coverage"
fi
# shellcheck disable=SC1091
. "$WORK/venv/bin/activate"

if [ ! -x "$WORK/sail/bin/sail_riscv_sim" ]; then
  mkdir -p "$WORK/sail"
  curl -fsSL "https://github.com/riscv/sail-riscv/releases/download/$SAIL_VER/sail-riscv-$(uname)-$(uname -m).tar.gz" \
    | tar -xz -C "$WORK/sail" --strip-components=1
fi
export PATH="$WORK/sail/bin:$PATH"
# Keep the framework's gems in the work directory: a system Ruby's gem directory is not writable
export BUNDLE_PATH=${BUNDLE_PATH:-$WORK/gems}

MODEL="$WORK/model"
if [ ! -x "$MODEL/obj_dir/Vtb_top" ]; then
  echo "building Verilator model in $MODEL"
  mkdir -p "$MODEL"
  (cd "$MODEL" && make -f "$REPO/tools/Makefile" verilator-build target=default RV_ROOT="$REPO" -j"$JOBS" > build.log 2>&1) \
    || { echo "Verilator build failed, see $MODEL/build.log" >&2; exit 1; }
fi

# The config with the chosen compiler filled in
rm -rf "$WORK/config"; mkdir -p "$WORK/config"; cp -r "$CFG" "$WORK/config/$NAME"
sed -i -e "s|^compiler_exe:.*|compiler_exe: $CC|" -e "s|^objdump_exe:.*|objdump_exe: $OBJDUMP|" "$WORK/config/$NAME/test_config.yaml"

echo "building test ELFs (expected results from Sail $SAIL_VER)"
make -C "$ACT" elfs CONFIG_FILES="$WORK/config/$NAME/test_config.yaml" JOBS="$JOBS" \
  EXCLUDE_EXTENSIONS="$EXCLUDE" ${EXTENSIONS:+EXTENSIONS="$EXTENSIONS"}

echo "running on the Verilated core"
VEER_SNAPSHOT="$MODEL" "$ACT/run_tests.py" "$ACT/.github/scripts/run-veer-eh1.sh --elf" \
  "$ACT/work/$NAME/elfs" --jobs "$JOBS" | tee "$WORK/results.txt"
