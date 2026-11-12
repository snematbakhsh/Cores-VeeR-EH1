// SPDX-License-Identifier: Apache-2.0
// Copyright 2019 Western Digital Corporation or its affiliates.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.


module exu_alu_ctl
   import veer_types::*;
(
   input logic  clk,                      // Top level clock
   input logic  active_clk,               // Level 1 free clock
   input logic  rst_l,                    // Reset
   input logic  scan_mode,                // Scan control

   input predict_pkt_t  predict_p,        // Predicted branch structure

   input logic freeze,                    // Clock enable for valid

   input logic [31:0] a,                  // A operand
   input logic [31:0] b,                  // B operand
   input logic [1:0]  a_sh,               // Zba: shift A left by a_sh as it is flopped (packet of the incoming instruction)
   input logic [31:1] pc,                 // for pc=pc+2,4 calculations

   input logic valid,                     // Valid
   input logic flush,                     // Flush pipeline

   input logic [12:1] brimm,              // Branch offset

   input alu_pkt_t ap,                    // {valid,predecodes}

   input logic  enable,                   // Clock enable


   output logic [31:0] out,               // final result

   output logic        flush_upper,       // Branch flush
   output logic [31:1] flush_path,        // Branch flush PC

   output logic [31:1] pc_ff,             // flopped PC

   output logic pred_correct,             // NPC control
   output predict_pkt_t predict_p_ff      // Predicted branch structure

  );




   logic        [31:0]  aout,bm;
   logic                cout;

   logic        [3:1]   logic_sel;

   logic        [31:0]  lout;
   logic        [31:0]  sout;
   logic                sel_logic,sel_shift,sel_adder;

   logic                slt_one;

   logic                actual_taken;

   logic signed [31:0]  a_ff;

   logic [31:0]         b_ff;

   logic [12:1]         brimm_ff;

   logic [31:1]         pcout;

   logic                valid_ff;

   logic                cond_mispredict;
   logic                target_mispredict;

   logic                eq, ne, lt, ge;

   logic [31:0]         a_in;


   rvdffs #(1)  validff (.*, .clk(active_clk), .en(~freeze), .din(valid & ~flush), .dout(valid_ff));

   // Zba (sh1add/sh2add/sh3add): rs1 is shifted on its way into the operand flop. For these
   // instructions a_ff only feeds the adder, so the shift costs nothing on the EX-stage adder,
   // branch-compare and flush paths; the select comes from decode, ahead of the operand data.
   assign a_in[31:0] = ({32{a_sh[1:0] == 2'd0}} &  a[31:0]        ) |
                       ({32{a_sh[1:0] == 2'd1}} & {a[30:0], 1'b0} ) |
                       ({32{a_sh[1:0] == 2'd2}} & {a[29:0], 2'b0} ) |
                       ({32{a_sh[1:0] == 2'd3}} & {a[28:0], 3'b0} );

   rvdffe #(32) aff (.*, .en(enable & valid), .din(a_in[31:0]), .dout(a_ff[31:0]));

   rvdffe #(32) bff (.*, .en(enable & valid), .din(b[31:0]), .dout(b_ff[31:0]));

   // any PC is run through here - doesn't have to be alu
   rvdffe #(31) pcff (.*, .en(enable), .din(pc[31:1]), .dout(pc_ff[31:1]));

   rvdffe #(12) brimmff (.*, .en(enable), .din(brimm[12:1]), .dout(brimm_ff[12:1]));

   predict_pkt_t pp_ff;

   rvdffe #($bits(predict_pkt_t)) predictpacketff (.*,
                           .en(enable),
                           .din(predict_p),
                           .dout(pp_ff)
                           );


   // immediates are just muxed into rs2

   // add => add=1;
   // sub => add=1; sub=1;

   // and => lctl=3
   // or  => lctl=2
   // xor => lctl=1

   // sll => sctl=3
   // srl => sctl=2
   // sra => sctl=1

   // slt => slt

   // lui   => lctl=2; or x0, imm20 previously << 12
   // auipc => add;  add pc, imm20 previously << 12

   // beq  => bctl=4; add; add x0, pc, sext(offset[12:1])
   // bne  => bctl=3; add; add x0, pc, sext(offset[12:1])
   // blt  => bctl=2; add; add x0, pc, sext(offset[12:1])
   // bge  => bctl=1; add; add x0, pc, sext(offset[12:1])

   // jal  => rs1=pc {pc[31:1],1'b0},  rs2=sext(offset20:1]);    rd=pc+[2,4]
   // jalr => rs1=rs1, rs2=sext(offset20:1]);                    rd=pc+[2,4]

   // Bit manipulation (Zba/Zbb/Zbs) reuses these units where it can:
   //   sh1add/sh2add/sh3add  adder, with A pre-shifted (above)
   //   andn/orn/xnor         logic unit with B inverted (negb)
   //   bset/bclr/binv        logic unit (or/andn/xor) with B = 1 << b[4:0] (onehot)
   //   rol/ror/rori          shifter (sll/srl) in rotate mode (rot)
   //   bext/bexti            shifter (srl), bit 0 only (bext)
   //   min/max/minu/maxu     adder computes a-b (sub, unsign); lt selects a or b
   //   unary ops             dedicated logic below (unary)


   assign bm[31:0] = ( ap.sub ) ? ~b_ff[31:0] : b_ff[31:0];


   assign {cout, aout[31:0]} = {1'b0, a_ff[31:0]} + {1'b0, bm[31:0]} + {32'b0, ap.sub};

   assign eq = a_ff[31:0] == b_ff[31:0];

   assign ne = ~eq;


   // Thermometer code of the shift amount: b_therm[i] = (i >= b_ff[4:0]). It is the mask
   // for left shifts and, differentiated, the one-hot bit select for bset/bclr/binv.
   logic [31:0] b_therm, b_onehot, lb;

   assign b_therm[31:0]  = 32'hffffffff << b_ff[4:0];
   assign b_onehot[31:0] = b_therm[31:0] & ~{b_therm[30:0], 1'b0};

   assign lb[31:0] = ( (ap.onehot) ? b_onehot[31:0] : b_ff[31:0] ) ^ {32{ap.negb}};

   assign logic_sel[3] = ap.land | ap.lor;
   assign logic_sel[2] = ap.lor | ap.lxor;
   assign logic_sel[1] = ap.lor | ap.lxor;



   assign lout[31:0] =  (  a_ff[31:0] &  lb[31:0] & {32{logic_sel[3]}} ) |
                        (  a_ff[31:0] & ~lb[31:0] & {32{logic_sel[2]}} ) |
                        ( ~a_ff[31:0] &  lb[31:0] & {32{logic_sel[1]}} );


   // One funnel shifter for sll/srl/sra/rol/ror/bext: {hi[30:0], a} >> amt, low 32 bits.
   //   srl: hi = 0           sra: hi = {31{a[31]}}      ror: hi = a[30:0]
   //   sll/rol: hi = a[30:0], amt = -b (mod 32); sll also clears the low b bits (b_therm).
   logic [4:0]  shamt;
   logic [62:0] sh_in, sh_long;

   assign shamt[4:0]    = (ap.sll) ? (5'd0 - b_ff[4:0]) : b_ff[4:0];

   assign sh_in[31:0]   = a_ff[31:0];
   assign sh_in[62:32]  = ({31{ap.sll | ap.rot}} & a_ff[30:0]) |
                          ({31{ap.sra}}          & {31{a_ff[31]}});

   assign sh_long[62:0] = sh_in[62:0] >> shamt[4:0];

   assign sout[31:0]    = sh_long[31:0] & ( b_therm[31:0] | {32{~ap.sll | ap.rot}} );


   // ---------------------------------------------------------------------------------------
   // Zbb unary operations. They decode their instruction bits [31:20] as imm12, so b_ff
   // holds that immediate in every ALU (primary and secondary) and selects the operation:
   //
   //   clz    0x600   ctz    0x601   cpop   0x602   sext.b 0x604   sext.h 0x605
   //   zext.h 0x080   orc.b  0x287   rev8   0x698
   // ---------------------------------------------------------------------------------------
   logic u_clz, u_ctz, u_cpop, u_sextb, u_sexth, u_zexth, u_orcb, u_rev8;

   assign u_clz   = ap.unary & (b_ff[2:0] == 3'b000) & ~b_ff[7];
   assign u_ctz   = ap.unary & (b_ff[2:0] == 3'b001);
   assign u_cpop  = ap.unary & (b_ff[2:0] == 3'b010);
   assign u_sextb = ap.unary & (b_ff[2:0] == 3'b100);
   assign u_sexth = ap.unary & (b_ff[2:0] == 3'b101);
   assign u_orcb  = ap.unary & (b_ff[2:0] == 3'b111);
   assign u_zexth = ap.unary & (b_ff[2:0] == 3'b000) &  b_ff[7] & ~b_ff[4];
   assign u_rev8  = ap.unary & (b_ff[2:0] == 3'b000) &  b_ff[4];

   // clz/ctz: one leading-zero counter, fed the bit-reversed operand for ctz. Log-depth tree:
   // each level merges two halves as {zero_hi ? (1, cnt_lo) : (0, cnt_hi)}.
   logic [31:0]      lz_in;
   logic [15:0]      lz_z1;   logic [15:0]      lz_c1;
   logic [7:0]       lz_z2;   logic [7:0][1:0]  lz_c2;
   logic [3:0]       lz_z3;   logic [3:0][2:0]  lz_c3;
   logic [1:0]       lz_z4;   logic [1:0][3:0]  lz_c4;
   logic             lz_z5;   logic [4:0]       lz_c5;
   logic [5:0]       lz_cnt;

   always_comb begin
      for (int i=0; i<32; i++) lz_in[i] = (u_clz & a_ff[i]) | (u_ctz & a_ff[31-i]);

      for (int i=0; i<16; i++) begin
         lz_z1[i] = ~(lz_in[2*i+1] | lz_in[2*i]);
         lz_c1[i] = ~lz_in[2*i+1];
      end
      for (int i=0; i<8; i++) begin
         lz_z2[i] = lz_z1[2*i+1] & lz_z1[2*i];
         lz_c2[i] = (lz_z1[2*i+1]) ? {1'b1, lz_c1[2*i]} : {1'b0, lz_c1[2*i+1]};
      end
      for (int i=0; i<4; i++) begin
         lz_z3[i] = lz_z2[2*i+1] & lz_z2[2*i];
         lz_c3[i] = (lz_z2[2*i+1]) ? {1'b1, lz_c2[2*i]} : {1'b0, lz_c2[2*i+1]};
      end
      for (int i=0; i<2; i++) begin
         lz_z4[i] = lz_z3[2*i+1] & lz_z3[2*i];
         lz_c4[i] = (lz_z3[2*i+1]) ? {1'b1, lz_c3[2*i]} : {1'b0, lz_c3[2*i+1]};
      end
      lz_z5 = lz_z4[1] & lz_z4[0];
      lz_c5 = (lz_z4[1]) ? {1'b1, lz_c4[0]} : {1'b0, lz_c4[1]};
      // all zero: 32 (lz_c5 is 31 then)
      lz_cnt = {lz_z5, lz_c5 & {5{~lz_z5}}};
   end

   // cpop: balanced adder tree (2-bit, 3-bit, 4-bit, 5-bit, 6-bit sums); synthesis tools turn
   // it into a carry-save tree. The input is gated with the unary flop bit (one gate level,
   // not the full sub-op decode) so the tree does not toggle for other instructions.
   logic [31:0]      pc_in;
   logic [15:0][1:0] pc_s1;
   logic [7:0][2:0]  pc_s2;
   logic [3:0][3:0]  pc_s3;
   logic [1:0][4:0]  pc_s4;
   logic [5:0]       pc_cnt;

   always_comb begin
      pc_in = a_ff[31:0] & {32{ap.unary}};
      for (int i=0; i<16; i++) pc_s1[i] = {1'b0, pc_in[2*i+1]}  + {1'b0, pc_in[2*i]};
      for (int i=0; i<8;  i++) pc_s2[i] = {1'b0, pc_s1[2*i+1]}  + {1'b0, pc_s1[2*i]};
      for (int i=0; i<4;  i++) pc_s3[i] = {1'b0, pc_s2[2*i+1]}  + {1'b0, pc_s2[2*i]};
      for (int i=0; i<2;  i++) pc_s4[i] = {1'b0, pc_s3[2*i+1]}  + {1'b0, pc_s3[2*i]};
      pc_cnt = {1'b0, pc_s4[1]} + {1'b0, pc_s4[0]};
   end

   logic [31:0] unary_out;

   assign unary_out[31:0] = ({32{u_clz | u_ctz}} & {26'b0, lz_cnt[5:0]}                     ) |
                            ({32{u_cpop}}        & {26'b0, pc_cnt[5:0]}                     ) |
                            ({32{u_sextb}}       & {{24{a_ff[7]}},  a_ff[7:0]}              ) |
                            ({32{u_sexth}}       & {{16{a_ff[15]}}, a_ff[15:0]}             ) |
                            ({32{u_zexth}}       & {16'b0,          a_ff[15:0]}             ) |
                            ({32{u_orcb}}        & {{8{|a_ff[31:24]}}, {8{|a_ff[23:16]}},
                                                    {8{|a_ff[15:8]}},  {8{|a_ff[7:0]}}}      ) |
                            ({32{u_rev8}}        & {a_ff[7:0], a_ff[15:8], a_ff[23:16], a_ff[31:24]});


   assign sel_logic = |{ap.land,ap.lor,ap.lxor};

   assign sel_shift = |{ap.sll,ap.srl,ap.sra} & ~ap.bext;

   assign sel_adder = (ap.add | ap.sub) & ~ap.slt & ~ap.min & ~ap.max;




   // a < b from the carry out of a + ~b + 1 alone: unsigned, or signed with equal signs,
   // is ~cout; signed with different signs is a[31]. Equivalent to the sum-sign/overflow
   // form, but the adder's sum bit 31 and the overflow logic are no longer on this path
   // (branches, slt, min/max).
   assign lt = (~ap.unsign & (a_ff[31] ^ b_ff[31]) & a_ff[31]) |
               ((ap.unsign | ~(a_ff[31] ^ b_ff[31])) & ~cout);

   assign ge = ~lt;


   assign slt_one = (ap.slt & lt);

   // min/max: rs1 if (rs1 < rs2) for min, if (rs1 >= rs2) for max
   logic minmax_a;

   assign minmax_a = lt ^ ap.max;

   assign out[31:0] = ({32{sel_logic}} & lout[31:0]) |
                      ({32{sel_shift}} & sout[31:0]) |
                      ({32{sel_adder}} & aout[31:0]) |
                      ({32{ap.jal | pp_ff.pcall | pp_ff.pja | pp_ff.pret}} & {pcout[31:1],1'b0}) |
                      ({32{ap.csr_write}} & ((ap.csr_imm) ? b_ff[31:0] : a_ff[31:0])) |                // csr_write: if csr_imm rs2 else rs1
                      ({31'b0, slt_one}) |
                      ({31'b0, ap.bext & sh_long[0]}) |
                      ({32{(ap.min | ap.max) &  minmax_a}} & a_ff[31:0]) |
                      ({32{(ap.min | ap.max) & ~minmax_a}} & b_ff[31:0]) |
                      unary_out[31:0];

   // branch handling

   logic                any_jal;

   assign any_jal =       ap.jal |
                          pp_ff.pcall |
                          pp_ff.pja   |
                          pp_ff.pret;


   assign actual_taken = (ap.beq & eq) |
                         (ap.bne & ne) |
                         (ap.blt & lt) |
                         (ap.bge & ge) |
                         (any_jal);

   // for a conditional br pcout[] will be the opposite of the branch prediction
   // for jal or pcall, it will be the link address pc+2 or pc+4

   rvbradder ibradder (
                     .pc(pc_ff[31:1]),
                     .offset(brimm_ff[12:1]),
                     .dout(pcout[31:1])
                      );

   // pred_correct is for the npc logic
   // pred_correct indicates not to use the flush_path
   // for any_jal pred_correct==0

   assign pred_correct = ((ap.predict_nt & ~actual_taken) |
                          (ap.predict_t  &  actual_taken)) & ~any_jal;


   // for any_jal adder output is the flush path
   assign flush_path[31:1] = (any_jal) ? aout[31:1] : pcout[31:1];


   // pcall and pret are included here
   assign cond_mispredict = (ap.predict_t & ~actual_taken) |
                            (ap.predict_nt & actual_taken);

   // target mispredicts on ret's

   assign target_mispredict = pp_ff.pret & (pp_ff.prett[31:1] != aout[31:1]);

   assign flush_upper = ( ap.jal | cond_mispredict | target_mispredict) & valid_ff & ~flush & ~freeze;


   // .i 3
   // .o 2
   // .ilb hist[1] hist[0] taken
   // .ob newhist[1] newhist[0]
   // .type fd
   //
   // 00 0 01
   // 01 0 01
   // 10 0 00
   // 11 0 10
   // 00 1 10
   // 01 1 00
   // 10 1 11
   // 11 1 11

   logic [1:0]          newhist;

   assign newhist[1] = (pp_ff.hist[1]&pp_ff.hist[0]) | (!pp_ff.hist[0]&actual_taken);

   assign newhist[0] = (!pp_ff.hist[1]&!actual_taken) | (pp_ff.hist[1]&actual_taken);



   always_comb begin
      predict_p_ff = pp_ff;

      predict_p_ff.misp    = (valid_ff) ? (cond_mispredict | target_mispredict) & ~flush : pp_ff.misp;
      predict_p_ff.ataken  = (valid_ff) ? actual_taken : pp_ff.ataken;
      predict_p_ff.hist[1] = (valid_ff) ? newhist[1] : pp_ff.hist[1];
      predict_p_ff.hist[0] = (valid_ff) ? newhist[0] : pp_ff.hist[0];

   end



endmodule // exu_alu_ctl
