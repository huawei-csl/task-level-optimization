// RTLScout provenance note (qr8_1r1w_verilog): Verilog twin of the task-level
// study's Spire qr8_1r1w recast, DERIVED FROM the upstream file
// qr8_upstream/context/starting_point.v (logikbench qr.v + qr_array.v +
// qr_boundary.v + qr_cordic.v + qr_internal.v) by the same serialization the
// Spire recast applies, mirrored cycle for cycle. RTLScout edits (every edited
// region is marked "RTLScout" below; everything else is the upstream's):
//  (1) interface: the native start / 1024-bit a_in / done / 1024-bit r_out bus
//      becomes the study's 1R/1W memory port, with LOAD (64 reads, one element
//      per cycle) and STORE (64 writes) states; reset becomes the harness's
//      synchronous active-high `rst`.
//  (2) datapath: the upstream computes one full Givens rotation per cycle in a
//      single combinational cone (qr_array = boundary CORDIC + 8 internal
//      CORDICs x 16 micro-rotations + gain multiply). Here that cone is
//      time-multiplexed onto ONE shift-add micro-rotation stage: per (jr, ir)
//      pair a 16-cycle vectoring pass (VEC) records sigma, then every column
//      k = jr..N-1 gets 16 replay cycles (ROT) and one gain-compensation /
//      write-back cycle (GAIN). The step arithmetic is the loop body of
//      qr_cordic, the gain compensation (KINV, >>> 15) is qr_internal's, the
//      load/drain scaling (<<< F, >>> F) and the (jr, ir) annihilation order
//      are the upstream FSM's -- so R is bit-identical to the upstream.
//  (3) qr_array / qr_boundary / qr_internal / qr_cordic are therefore no longer
//      instantiated and were removed from this file.
// Schedule: ~3.4k cycles/case, NO parallel columns, NO chained micro-rotations,
// NO load/compute/store overlap -- recovering that parallelism is what the
// benchmark measures.
//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
/******************************************************************************
 * qr: fixed-point QR decomposition (A = Q*R) by CORDIC Givens rotations.
 *
 * A is loaded in parallel as a flattened N*N word matrix (row-major, signed
 * DW-bit); 'start' begins the triangularization and 'done' pulses when the
 * flattened upper-triangular R is valid on r_out. Internally the matrix is
 * carried at IW-bit precision with F fractional bits (the CORDIC needs
 * fractional headroom to converge on integer-scale inputs).
 *
 * A small FSM sequences one Givens rotation per cycle over the sub-diagonal
 * (pivot, row) pairs; each cycle feeds the two affected rows through the
 * combinational qr_array (boundary + internal CORDIC cells), which annihilates
 * one sub-diagonal element and updates the trailing columns in parallel. After
 * the last pair the working matrix is upper-triangular R.
 *
 * The CORDIC datapath assumes first-quadrant pivots (positive diagonal), which
 * holds for the well-conditioned, diagonally dominant matrices this benchmark
 * targets.
 ******************************************************************************/
module qr8_core                    // RTLScout: was `module qr` (native bus interface)
  #(parameter N  = 8,            // matrix dimension (N x N)
    parameter DW = 16)           // signed I/O word width
  (
   input               clk,
   input               rst,       // RTLScout: synchronous active-high (was reset_n, async active-low)
   // RTLScout: 1R/1W memory port replaces start / a_in / done(pulse) / r_out
   output              rd_en,
   output [5:0]        rd_addr,   // A element index, row-major
   input  [DW-1:0]     rd_data,   // valid one cycle after rd_en
   output              wr_en,
   output [5:0]        wr_addr,   // R element index, row-major
   output [DW-1:0]     wr_data,
   output              done       // sticky
   );

   localparam IW  = DW + 16;    // internal width (guard + fractional)
   localparam M   = 16;         // CORDIC micro-rotations
   localparam F   = 8;          // fractional bits for CORDIC precision
   localparam IDW = 8;          // pivot/row index width (ample for N up to 256)
   localparam NN  = N * N;      // RTLScout: element count (load/store counters)
   // CORDIC gain compensation: 1/K in Q15 (K = prod sqrt(1+2^-2i), M = 16)
   localparam signed [16:0] KINV = 17'sd19898;   // RTLScout: moved here from qr_internal

   // working matrix, row-major, IW-bit signed
   reg signed [IW-1:0] W [0:N*N-1];

   reg [IDW:0] jr;              // pivot row / column
   reg [IDW:0] ir;              // row being annihilated

   // RTLScout: the IDLE/RUN/DONE FSM becomes LOAD / VEC / ROT / GAIN / STORE / DONE
   localparam LOAD  = 3'd0,
              VEC   = 3'd1,     // vectoring pass on the pivot pair (16 cycles)
              ROT   = 3'd2,     // replay pass on column kc (16 cycles)
              GAIN  = 3'd3,     // gain compensation + write-back (1 cycle)
              STORE = 3'd4,
              DONE  = 3'd5;
   reg [2:0] state;

   integer k;

   // ---- RTLScout: serialization bookkeeping -------------------------------
   reg [6:0]           lc;      // load counter 0..NN
   reg [6:0]           sc;      // store counter 0..NN-1
   reg [IDW:0]         kc;      // column being rotated
   reg [3:0]           mi;      // micro-rotation step 0..M-1
   reg [M-1:0]         sig;     // recorded direction bits (sigma)
   reg signed [IW-1:0] cx;      // CORDIC x accumulator
   reg signed [IW-1:0] cy;      // CORDIC y accumulator

   // RTLScout structural fix (fairness; same treatment as the upstream comparison
   // measurements): the single `state` register fed the enables of all 64x32
   // working-matrix flops and the seed-select muxes of cx/cy; its clk->Q alone
   // measured ~1.3 ns (the flow runs no buffering, and synthesis cannot
   // duplicate a register). The state register is therefore replicated: one
   // whole-FSM replica per matrix row for that row's W enables, one for the
   // seed-select logic. Each replica's next state references its own Q (so
   // opt_merge cannot re-merge them) and mirrors the FSM below exactly.
   reg [2:0] state_rep [0:N];      // 0..N-1: W row enables, N: seed select
   function [2:0] fsm_next;
      input [2:0] st;
      begin
         case (st)
           LOAD:  fsm_next = (lc == NN) ? VEC : LOAD;
           VEC:   fsm_next = (mi == M - 1) ? ROT : VEC;
           ROT:   fsm_next = (mi == M - 1) ? GAIN : ROT;
           GAIN:  fsm_next = (kc != N - 1) ? ROT :
                             !((ir == N - 1) && (jr == N - 2)) ? VEC : STORE;
           STORE: fsm_next = (sc == NN - 1) ? DONE : STORE;
           DONE:  fsm_next = DONE;
           default: fsm_next = LOAD;
         endcase
      end
   endfunction
   integer rr;
   always @(posedge clk)
      for (rr = 0; rr <= N; rr = rr + 1)
         state_rep[rr] <= rst ? LOAD : fsm_next(state_rep[rr]);

   wire in_load  = (state == LOAD);
   wire in_vec   = (state == VEC);
   wire in_rot   = (state == ROT);
   wire in_gain  = (state == GAIN);
   wire in_store = (state == STORE);

   // ---- RTLScout: memory-port outputs (combinational from state) ----------
   assign rd_en   = in_load && (lc < NN);
   assign rd_addr = lc[5:0];
   assign wr_en   = in_store;
   assign wr_addr = sc[5:0];
   wire signed [IW-1:0] w_store = W[sc[5:0]];
   assign wr_data = w_store[F +: DW];      // drain R: shift out of the fractional domain, low DW bits
   assign done    = (state == DONE);

   // ---- RTLScout: ONE micro-rotation step = iteration i = mi of qr_cordic ---
   // vectoring: reduce |y|: rotate by -angle when y >= 0, by +angle when y < 0
   // rotation : replay the recorded direction sigma[mi]
   wire                dpos_vec = cy[IW-1];          // sign bit: 1 = negative -> +angle
   wire                dpos     = in_vec ? dpos_vec : sig[mi];
   wire signed [IW-1:0] sx = cx >>> mi;
   wire signed [IW-1:0] sy = cy >>> mi;
   wire signed [IW-1:0] nx = dpos ? (cx - sy) : (cx + sy);
   wire signed [IW-1:0] ny = dpos ? (cy + sx) : (cy - sx);

   // ---- RTLScout: gain compensation, as qr_internal (xr * KINV) >>> 15 ------
   wire signed [IW+16:0] px = cx * KINV;
   wire signed [IW+16:0] py = cy * KINV;
   wire signed [IW-1:0]  gx = px >>> 15;
   wire signed [IW-1:0]  gy = py >>> 15;

   // ---- RTLScout: FSM edges and the next (pivot, row, column) --------------
   wire step_last = (mi == M - 1);
   wire vec_done  = in_vec && step_last;
   wire rot_done  = in_rot && step_last;
   wire load_last = in_load && (lc == NN);
   // RTLScout structural fix: seed-select decode from the dedicated replica
   wire in_gain_s   = (state_rep[N] == GAIN);
   wire vec_done_s  = (state_rep[N] == VEC) && step_last;
   wire load_last_s = (state_rep[N] == LOAD) && (lc == NN);
   wire col_last  = (kc == N - 1);
   wire ir_last   = (ir == N - 1);
   wire last_pair = ir_last && (jr == N - 2);
   // advance over (pivot, row) pairs exactly as the upstream RUN state
   wire [IDW:0] next_jr = ir_last ? jr + 9'd1 : jr;
   wire [IDW:0] next_ir = ir_last ? jr + 9'd2 : ir + 9'd1;
   // CORDIC seeding: read W with the NEXT phase's indices on the phase edge
   wire [IDW:0] seed_col = (in_gain_s && !col_last) ? kc + 9'd1 :   // next column, same pair
                           vec_done_s               ? jr          :   // ROT starts at the pivot column
                                                      next_jr;        // next pair's pivot column
   wire [IDW:0] seed_rj  = (in_gain_s && col_last) ? next_jr : jr;
   wire [IDW:0] seed_ri  = (in_gain_s && col_last) ? next_ir : ir;
   wire         seed_now = load_last_s || vec_done_s || in_gain_s;

   always @(posedge clk) begin
      if (rst) begin
         state <= LOAD;
         lc    <= 7'd0;
         sc    <= 7'd0;
         kc    <= {(IDW+1){1'b0}};
         mi    <= 4'd0;
         sig   <= {M{1'b0}};
         cx    <= {IW{1'b0}};
         cy    <= {IW{1'b0}};
         jr    <= {(IDW+1){1'b0}};
         ir    <= {{(IDW){1'b0}}, 1'b1};   // i = 1
         for (k = 0; k < N*N; k = k + 1)
           W[k] <= {IW{1'b0}};
      end
      else begin
         // CORDIC accumulators: (re)seed on every phase boundary, step in VEC/ROT
         if (seed_now) begin
            cx <= W[seed_rj*N + seed_col];
            cy <= W[seed_ri*N + seed_col];
         end
         else if (in_vec || in_rot) begin
            cx <= nx;
            cy <= ny;
         end
         if (vec_done || rot_done || in_gain) mi <= 4'd0;
         else if (in_vec || in_rot)           mi <= mi + 4'd1;
         if (in_vec) sig[mi] <= dpos_vec;     // record sigma during vectoring

         case (state)
           LOAD: begin
              // issue read lc while lc < NN; the word lands one cycle later:
              // load A, sign-extended and shifted into the fractional domain
              if (lc < NN) lc <= lc + 7'd1;
              // (W load capture: see the replicated-enable block below)
              if (lc == NN) begin
                 jr    <= {(IDW+1){1'b0}};
                 ir    <= {{(IDW){1'b0}}, 1'b1};   // i = 1
                 state <= VEC;
              end
           end

           VEC:
             if (step_last) begin
                kc    <= jr;                 // the pivot column rotates too
                state <= ROT;
             end

           ROT:
             if (step_last) state <= GAIN;

           GAIN: begin
              // (W write-back: see the replicated-enable block below)
              if (!col_last) begin
                 kc    <= kc + 9'd1;
                 state <= ROT;
              end
              else if (!last_pair) begin
                 jr    <= next_jr;
                 ir    <= next_ir;
                 state <= VEC;
              end
              else
                state <= STORE;
           end

           STORE: begin
              sc <= sc + 7'd1;
              if (sc == NN - 1) state <= DONE;
           end

           DONE: ;                           // sticky

           default: state <= LOAD;
         endcase
         // RTLScout structural fix: working-matrix writes, enabled from the
         // row's own state replica (same conditions as the LOAD / GAIN states)
         for (k = 0; k < N*N; k = k + 1) begin
            // load A, sign-extended and shifted into the fractional domain
            if ((state_rep[k / N] == LOAD) && (lc >= 7'd1) && (lc - 7'd1 == k))
              W[k] <= $signed({{(IW-DW){rd_data[DW-1]}}, rd_data}) <<< F;
            // commit this column of the Givens rotation
            if ((state_rep[k / N] == GAIN) && (jr == k / N) && (kc == k % N))
              W[k] <= gx;
            if ((state_rep[k / N] == GAIN) && (ir == k / N) && (kc == k % N))
              W[k] <= gy;
         end
      end
   end

endmodule
