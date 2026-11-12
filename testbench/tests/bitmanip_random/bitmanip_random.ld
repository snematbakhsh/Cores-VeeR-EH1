/* Code in ICCM and data in DCCM, both preloaded by the testbench through the CTL
 * words (see preload_iccm/preload_dccm in tb_top.sv), so the test runs on both the
 * AXI and the AHB-lite builds and its loads hit the DCCM (load-to-use operands then
 * go through the secondary ALUs). */
OUTPUT_ARCH( "riscv" )
ENTRY(_start)
MEMORY {
    EXTCODE : ORIGIN = 0,          LENGTH = 0x10000
    ICCM    : ORIGIN = 0xee000000, LENGTH = 0x80000
    DCCM    : ORIGIN = 0xf0040000, LENGTH = 0x10000
    CTL     : ORIGIN = 0xfffffff0, LENGTH = 16
}
SECTIONS {
  .text.init : { *(.text.init) } > EXTCODE
  .text : { *(.text) } > ICCM
  text_end = .;
  .data : { *(.data) *(.rodata*) . = ALIGN(4); } > DCCM
  data_end = .;
  .data.ctl : { LONG(ADDR(.text)); LONG(text_end); LONG(ADDR(.data)); LONG(data_end - 4) } > CTL
}
