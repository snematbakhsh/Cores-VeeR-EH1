/* Embench main for the stock VeeR EH1 Verilator testbench: times benchmark()
   with mcycle/minstret, reads four EH1 performance counters (PRM table of
   events: 25 branches mispredicted, 28 cycles fetch stalled, 30 cycles decode
   stalled, 13 misaligned loads) and reports through the testbench mailbox. */
#include "support.h"
#define STDOUT 0xd0580000
static volatile char *const mailbox = (char *)STDOUT;
static void emit(const char *s) { while (*s) *mailbox = *s++; }
static void emitnum(unsigned v) {
  char b[12]; int i = 0;
  if (!v) { *mailbox = '0'; return; }
  while (v) { b[i++] = '0' + v % 10; v /= 10; }
  while (i--) *mailbox = b[i];
}
#define RDCSR(name) ({ unsigned c; __asm__ volatile("csrr %0," name : "=r"(c)); c; })
static unsigned c0, c1, i0, i1, m0, m1, f0, f1, d0, d1, u0, u1;
void initialise_board(void) {
  /* Defaults: 25 branches mispredicted, 28 cycles fetch stalled, 30 cycles decode stalled,
     13 misaligned loads. Override with -DPMU_EV3=.. for diagnostics (PRM table 7-2). */
#ifndef PMU_EV3
#define PMU_EV3 25
#endif
#ifndef PMU_EV4
#define PMU_EV4 28
#endif
#ifndef PMU_EV5
#define PMU_EV5 30
#endif
#ifndef PMU_EV6
#define PMU_EV6 13
#endif
  /* Event numbers live in .rodata so the code size (and hence the code layout) does not
     depend on which events are selected. */
  static const volatile unsigned pmu_ev[4] = { PMU_EV3, PMU_EV4, PMU_EV5, PMU_EV6 };
  __asm__ volatile("csrw 0x323, %0" :: "r"(pmu_ev[0])); /* mhpmevent3 */
  __asm__ volatile("csrw 0x324, %0" :: "r"(pmu_ev[1])); /* mhpmevent4 */
  __asm__ volatile("csrw 0x325, %0" :: "r"(pmu_ev[2])); /* mhpmevent5 */
  __asm__ volatile("csrw 0x326, %0" :: "r"(pmu_ev[3])); /* mhpmevent6 */
  /* No mcountinhibit on EH1 (0x320 traps); mgpmc (0x7d0) resets to counters enabled. */
}
void __attribute__((noinline)) start_trigger(void) {
  m0 = RDCSR("0xb03"); f0 = RDCSR("0xb04"); d0 = RDCSR("0xb05"); u0 = RDCSR("0xb06");
  i0 = RDCSR("minstret"); c0 = RDCSR("mcycle");
}
void __attribute__((noinline)) stop_trigger(void) {
  c1 = RDCSR("mcycle"); i1 = RDCSR("minstret");
  m1 = RDCSR("0xb03"); f1 = RDCSR("0xb04"); d1 = RDCSR("0xb05"); u1 = RDCSR("0xb06");
}
int main(void) {
  volatile int result; int correct;
  initialise_board();
  initialise_benchmark();
  warm_caches(WARMUP_HEAT);
  start_trigger();
  result = benchmark();
  stop_trigger();
  correct = verify_benchmark(result);
  emit(BENCH_NAME);
  emit(" cyc="); emitnum(c1 - c0); emit(" inst="); emitnum(i1 - i0);
  emit(" mispred="); emitnum(m1 - m0); emit(" fetchstall="); emitnum(f1 - f0);
  emit(" decstall="); emitnum(d1 - d0); emit(" misalign="); emitnum(u1 - u0);
  emit(correct ? " VERIFY_OK\n" : " VERIFY_FAIL\n");
  *mailbox = 0xff;
  return !correct;
}
