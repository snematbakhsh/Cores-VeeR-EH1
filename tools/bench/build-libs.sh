#!/usr/bin/env bash
# build-libs.sh - build the C library and the compiler builtins once per ISA variant, so a
# benchmark built for an ISA links against library code built for the same ISA:
#   $WORK/libs/<variant>/include, lib/libc.a            picolibc 1.8.10
#   $WORK/libs/<variant>/lib/libclang_rt.builtins.a     compiler-rt builtins (in place of libgcc)
# Variants: imc, all (zba_zbb_zbs), zba, zbb, zbs. Every variant uses the same flags except
# -march: -O3 -mtune=sifive-7-series, ilp32. (picolibc's own default is -Os, which turns memcpy
# into a byte loop; these benchmarks measure speed.)
#
# Environment:
#   WORK      work directory (default ./bench-work)
#   CLANG     clang to use (default: clang on PATH); the report used LLVM 23.1.2
#   LLVM_VER  compiler-rt source release (default 23.1.2; should match CLANG)
#   AR        archiver (default: llvm-ar next to CLANG, else llvm-ar on PATH); llvm-ranlib and
#             llvm-nm are taken from beside it
#   LDPATH    optional --ld-path for clang, if its own ld.lld is unusable
# Needs: curl, meson, ninja, cmake, riscv64-unknown-elf-{nm,strip}.
# Usage: build-libs.sh [variant...]   (default: all five; existing ones are skipped)
set -euo pipefail
WORK=${WORK:-$PWD/bench-work}
CLANG=$(command -v "${CLANG:-clang}")
LLVM_VER=${LLVM_VER:-23.1.2}
PICO_VER=1.8.10
AR=${AR:-$( [ -x "$(dirname "$CLANG")/llvm-ar" ] && echo "$(dirname "$CLANG")/llvm-ar" || command -v llvm-ar )}
RANLIB=${AR%ar}ranlib; NM=${AR%ar}nm   # .../llvm-ar -> .../llvm-ranlib, .../llvm-nm
[ -x "$RANLIB" ] || RANLIB=$(command -v llvm-ranlib); [ -x "$NM" ] || NM=$(command -v llvm-nm)
LD=()
[ -n "${LDPATH:-}" ] && LD=("--ld-path=$LDPATH")
SRC=$WORK/libsrc
mkdir -p "$SRC" "$WORK/libs"
[ -d "$SRC/picolibc-$PICO_VER" ] || curl -fsSL "https://github.com/picolibc/picolibc/releases/download/$PICO_VER/picolibc-$PICO_VER.tar.xz" | tar -xJ -C "$SRC"
[ -d "$SRC/llvm-project-$LLVM_VER.src/compiler-rt" ] || curl -fsSL "https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VER/llvm-project-$LLVM_VER.src.tar.xz" \
  | tar -xJ -C "$SRC" --wildcards "llvm-project-$LLVM_VER.src/compiler-rt/*" "llvm-project-$LLVM_VER.src/cmake/*" "llvm-project-$LLVM_VER.src/llvm/cmake/*"

march() {
  case $1 in imc) echo rv32imc_zicsr_zifencei;; all) echo rv32imc_zba_zbb_zbs_zicsr_zifencei;;
             zba|zbb|zbs) echo "rv32imc_$1_zicsr_zifencei";; *) echo "unknown variant $1" >&2; exit 1;; esac
}

step() { # LOG CMD... : run CMD with all output in LOG; on failure show the end of LOG
  local log=$1; shift
  "$@" > "$log" 2>&1 || { echo "failed: $* (see $log)" >&2; tail -20 "$log" >&2; exit 1; }
}

build() { # $1 = variant
  local n=$1 m b p f ldq=""
  m=$(march "$n"); b=$WORK/libs/build-$n; p=$WORK/libs/$n
  if [ -f "$p/lib/libc.a" ] && [ -f "$p/lib/libclang_rt.builtins.a" ]; then echo "libs/$n: present"; return; fi
  f="--target=riscv32-unknown-elf -march=$m -mabi=ilp32 -mtune=sifive-7-series"
  [ ${#LD[@]} -gt 0 ] && ldq=", '${LD[0]}'"
  mkdir -p "$b"
  cat > "$b/cross.txt" <<X
[binaries]
c = ['$CLANG', '--target=riscv32-unknown-elf', '-march=$m', '-mabi=ilp32', '-mtune=sifive-7-series', '-nostdlib', '-fuse-ld=lld'$ldq]
ar = '$AR'
nm = 'riscv64-unknown-elf-nm'
strip = 'riscv64-unknown-elf-strip'
[host_machine]
system = 'none'
cpu_family = 'riscv32'
cpu = 'riscv'
endian = 'little'
[built-in options]
c_args = ['-Werror=double-promotion', '-fshort-enums', '-Wno-unused-command-line-argument']
[properties]
skip_sanity_check = true
X
  step "$b/pico-setup.log" meson setup --wipe "$b/pico" "$SRC/picolibc-$PICO_VER" --cross-file "$b/cross.txt" --buildtype=release -Ddebug=false \
    -Dmultilib=false -Dtests=false -Dsemihost=false -Dpicocrt=false -Dpicolib=false -Dspecsdir=none \
    -Dprefix="$p" -Dincludedir=include -Dlibdir=lib
  step "$b/pico-build.log" ninja -C "$b/pico" install
  rm -rf "$b/rt"
  step "$b/rt-cmake.log" cmake -G Ninja -S "$SRC/llvm-project-$LLVM_VER.src/compiler-rt" -B "$b/rt" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_SYSTEM_NAME=Generic -DCMAKE_SYSTEM_PROCESSOR=riscv32 \
    -DCMAKE_C_COMPILER="$CLANG" -DCMAKE_ASM_COMPILER="$CLANG" -DCMAKE_CXX_COMPILER="$CLANG" \
    -DCMAKE_C_COMPILER_TARGET=riscv32-unknown-elf -DCMAKE_ASM_COMPILER_TARGET=riscv32-unknown-elf \
    -DCMAKE_C_FLAGS="$f" -DCMAKE_ASM_FLAGS="$f" -DCMAKE_C_FLAGS_RELEASE=-O3 -DCMAKE_AR="$AR" -DCMAKE_RANLIB="$RANLIB" -DCMAKE_NM="$NM" \
    -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY -DCOMPILER_RT_BAREMETAL_BUILD=ON -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON \
    -DCOMPILER_RT_BUILD_BUILTINS=ON -DCOMPILER_RT_BUILD_SANITIZERS=OFF -DCOMPILER_RT_BUILD_XRAY=OFF \
    -DCOMPILER_RT_BUILD_LIBFUZZER=OFF -DCOMPILER_RT_BUILD_PROFILE=OFF -DCOMPILER_RT_BUILD_MEMPROF=OFF \
    -DCOMPILER_RT_BUILD_ORC=OFF -DCOMPILER_RT_BUILD_CTX_PROFILE=OFF -DCOMPILER_RT_BUILD_CRT=OFF \
    -DCOMPILER_RT_EXCLUDE_ATOMIC_BUILTIN=ON -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF
  step "$b/rt-build.log" ninja -C "$b/rt" builtins
  cp "$(find "$b/rt" -name 'libclang_rt.builtins*.a' | head -1)" "$p/lib/libclang_rt.builtins.a"
  echo "libs/$n: $m"
}

for v in "${@:-imc all zba zbb zbs}"; do for n in $v; do build "$n"; done; done
