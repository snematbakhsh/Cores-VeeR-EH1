/* Derived from Cores-VeeR-EH1 testbench/asm/cmark_iccm.ld.
 * Changes: section wildcards widened so clang-emitted .text.*, .data.*,
 * .sbss.*, .bss.* and .rodata.* land in ICCM/DCCM; runtime code placed first in
 * ICCM (fixed layout across configurations); stack placed at the top
 * of DCCM after .bss; DCCM preload end set to the last word of DCCM (the
 * testbench preload loop is inclusive). */
OUTPUT_ARCH( "riscv" )
ENTRY(_start)
MEMORY {
    EXTCODE : ORIGIN = 0,          LENGTH = 0x10000
    EXTDATA : ORIGIN = 0x10000,    LENGTH = 0x10000
    ICCM    : ORIGIN = 0xee000000, LENGTH = 0x80000
    DCCM    : ORIGIN = 0xf0040000, LENGTH = 0x10000
    CTL     : ORIGIN = 0xfffffff0, LENGTH = 16
    IO      : ORIGIN = 0xd0580000, LENGTH = 0x1000
}
SECTIONS {
  .text.init : {*(.text.init)} > EXTCODE
  init_end = .;
  .data.io  : { *(.data.io) } > IO
  /* Runtime code (board main, beebsc, libc, libgcc/compiler-rt builtins, libm) first so its placement is fixed
     across compiler configurations; the benchmark objects follow. Otherwise a few bytes of
     benchmark code-size change shift every hot library loop and add several % of noise. */
  .text : {
    *main.o(.text .text.* .text.startup)
    *beebsc.o(.text .text.*)
    *libc.a:*(.text .text.*)
    *libm.a:*(.text .text.*)
    *libgcc.a:*(.text .text.*)
    *libclang_rt.builtins.a:*(.text .text.*)
    *(.text) *(.text.*) *(.text.startup)
  } > ICCM
   text_end = .;
  .data  :  { *(.*data .*data.*) *(.rodata*) *(.srodata*) } > DCCM
  .bss : { *(.sbss*) *(.bss*) *(COMMON)} > DCCM
  STACK = ORIGIN(DCCM) + LENGTH(DCCM);
  .data.ctl :  { LONG(ADDR(.text)); LONG(text_end); LONG(0xf0040000); LONG(STACK - 4)}>CTL
  /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) *(.comment) *(.riscv.attributes) }
}
