// qr8_upstream starting point: the VERBATIM logikbench upstream CORDIC QR
// (qr.v + qr_array.v + qr_boundary.v + qr_cordic.v + qr_internal.v,
// concatenated byte-identically, ZERO edits -- N and DW are proper module
// parameters, set to 8/16 at testbench instantiation). RTLScout: no
// modifications of any kind in this file. Native interface:
// start + 1024-bit a_in, done pulse + 1024-bit r_out.
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
module qr
  #(parameter N  = 16,           // matrix dimension (N x N)
    parameter DW = 16)          // signed I/O word width
  (
   input               clk,
   input               reset_n,
   input               start,
   input [N*N*DW-1:0]  a_in, // row-major, signed
   output reg          done,
   output [N*N*DW-1:0] r_out // row-major upper-triangular R
   );

   localparam IW  = DW + 16;    // internal width (guard + fractional)
   localparam M   = 16;         // CORDIC micro-rotations
   localparam F   = 8;          // fractional bits for CORDIC precision
   localparam IDW = 8;          // pivot/row index width (ample for N up to 256)

   // working matrix, row-major, IW-bit signed
   reg signed [IW-1:0] W [0:N*N-1];

   reg [IDW:0] jr;              // pivot row / column
   reg [IDW:0] ir;              // row being annihilated

   localparam IDLE = 2'd0,
              RUN  = 2'd1,
              DONE = 2'd2;
   reg [1:0] state;

   integer k;

   // gather the two active rows for the combinational array
   reg  [N*IW-1:0] row_j_flat;
   reg  [N*IW-1:0] row_i_flat;
   always @* begin
      row_j_flat = {(N*IW){1'b0}};
      row_i_flat = {(N*IW){1'b0}};
      for (k = 0; k < N; k = k + 1) begin
         row_j_flat[k*IW +: IW] = W[jr*N + k];
         row_i_flat[k*IW +: IW] = W[ir*N + k];
      end
   end

   wire [N*IW-1:0] row_j_upd;
   wire [N*IW-1:0] row_i_upd;

   qr_array #(.N(N), .IW(IW), .M(M), .IDW(IDW)) u_array
     (.row_j_in  (row_j_flat),
      .row_i_in  (row_i_flat),
      .pivot     (jr[IDW-1:0]),
      .row_j_out (row_j_upd),
      .row_i_out (row_i_upd));

   always @(posedge clk or negedge reset_n) begin
      if (!reset_n) begin
         state <= IDLE;
         done  <= 1'b0;
         jr    <= {(IDW+1){1'b0}};
         ir    <= {(IDW+1){1'b0}};
      end
      else begin
         done <= 1'b0;
         case (state)
           IDLE:
             if (start) begin
                // load A, sign-extended and shifted into the fractional domain
                for (k = 0; k < N*N; k = k + 1)
                  W[k] <= $signed(a_in[k*DW +: DW]) <<< F;
                jr    <= {(IDW+1){1'b0}};
                ir    <= {{(IDW){1'b0}}, 1'b1};   // i = 1
                state <= RUN;
             end

           RUN: begin
              // commit this Givens rotation
              for (k = 0; k < N; k = k + 1) begin
                 W[jr*N + k] <= row_j_upd[k*IW +: IW];
                 W[ir*N + k] <= row_i_upd[k*IW +: IW];
              end
              // advance over (pivot, row) pairs
              if (ir == (N-1)) begin
                 if (jr == (N-2)) begin
                    state <= DONE;
                    done  <= 1'b1;
                 end
                 else begin
                    jr <= jr + 1'b1;
                    ir <= jr + 2'd2;
                 end
              end
              else
                ir <= ir + 1'b1;
           end

           DONE:
             if (start) state <= IDLE;   // accept a new job

           default: state <= IDLE;
         endcase
      end
   end

   // drain R: shift out of the fractional domain, low DW bits, row-major
   genvar g;
   generate
      for (g = 0; g < N*N; g = g + 1) begin: outmap
         assign r_out[g*DW +: DW] = W[g] >>> F;
      end
   endgenerate

endmodule

//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
/******************************************************************************
 * qr_array: one Givens-rotation update across two matrix rows.
 *
 * The boundary cell derives the rotation 'sigma' from the pivot column PIVOT of
 * the two rows (row_j = pivot row, row_i = row being annihilated). An internal
 * cell then applies that rotation to every column. Columns k < PIVOT are already
 * settled (row_i is zero there from earlier steps) and pass through unchanged;
 * columns k >= PIVOT take the rotated result, driving row_i[PIVOT] to zero.
 *
 * This is the Gentleman-Kung triangular QR mapped so that one array pass
 * annihilates one sub-diagonal element while updating the trailing columns in
 * parallel; the top-level FSM sequences the passes over all (pivot, row) pairs.
 ******************************************************************************/
module qr_array
  #(parameter N   = 4,
    parameter IW  = 32,
    parameter M   = 16,
    parameter IDW = 2)          // ceil(log2(N)) index width
  (
   input [N*IW-1:0]  row_j_in,
   input [N*IW-1:0]  row_i_in,
   input [IDW-1:0]   pivot,
   output [N*IW-1:0] row_j_out,
   output [N*IW-1:0] row_i_out
   );

   // pivot-column pair (variable indexed part-select)
   wire signed [IW-1:0] px = row_j_in[pivot*IW +: IW];
   wire signed [IW-1:0] py = row_i_in[pivot*IW +: IW];

   wire [M-1:0] sigma;
   qr_boundary #(.IW(IW), .M(M)) u_bnd (.x(px), .y(py), .sigma(sigma));

   genvar k;
   generate
      for (k = 0; k < N; k = k + 1) begin: col
         wire signed [IW-1:0] aj = row_j_in[k*IW +: IW];
         wire signed [IW-1:0] ai = row_i_in[k*IW +: IW];
         wire signed [IW-1:0] rj;
         wire signed [IW-1:0] ri;

         qr_internal #(.IW(IW), .M(M)) u_int
           (.sigma(sigma), .aj(aj), .ai(ai), .oj(rj), .oi(ri));

         // columns before the pivot are untouched; pivot and beyond rotate
         assign row_j_out[k*IW +: IW] = (k < pivot) ? aj : rj;
         assign row_i_out[k*IW +: IW] = (k < pivot) ? ai : ri;
      end
   endgenerate

endmodule

//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
/******************************************************************************
 * qr_boundary: diagonal (boundary) cell of the systolic QR array.
 *
 * Runs the CORDIC in vectoring mode on the pivot pair (the diagonal element x
 * and the sub-diagonal element y to be annihilated), producing the rotation
 * direction sequence 'sigma'. That angle is passed east to the internal cells,
 * which apply the same rotation to the rest of the two rows.
 ******************************************************************************/
module qr_boundary
  #(parameter IW = 32,
    parameter M  = 16)
  (
   input  wire signed [IW-1:0] x,        // pivot diagonal element
   input  wire signed [IW-1:0] y,        // element to annihilate
   output wire        [M-1:0]  sigma     // rotation angle (direction bits)
   );

   qr_cordic #(.IW(IW), .M(M)) u_vec
     (.vectoring (1'b1),
      .xin       (x),
      .yin       (y),
      .sigma_in  ({M{1'b0}}),
      .xout      (),
      .yout      (),
      .sigma_out (sigma));

endmodule

//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
/******************************************************************************
 * qr_cordic: unrolled circular-mode CORDIC engine (combinational).
 *
 * Two modes select how the per-iteration rotation direction is chosen:
 *   vectoring = 1 : drive yin toward 0 (choose d = -sign(y) each step) and
 *                   record the direction bits in sigma_out. xout is then the
 *                   CORDIC-gain-scaled magnitude, yout ~ 0.
 *   vectoring = 0 : replay a recorded direction sequence sigma_in, i.e. rotate
 *                   (xin,yin) by the same angle a prior vectoring pass found.
 *
 * Both modes apply the same M micro-rotations, so a vectoring pass on a pivot
 * pair and rotation passes on the other column pairs form one Givens rotation.
 * The output carries the CORDIC gain K = prod sqrt(1+2^-2i); callers divide it
 * out (see qr_internal). Datapath is signed and assumes a first-quadrant pivot
 * (x >= 0), which holds for the diagonally dominant, positive-diagonal inputs
 * this block targets.
 ******************************************************************************/
module qr_cordic
  #(parameter IW = 32,          // datapath width (signed)
    parameter M  = 16)          // number of CORDIC micro-rotations
  (
   input  wire                vectoring,     // 1 = compute angle, 0 = apply it
   input  wire signed [IW-1:0] xin,
   input  wire signed [IW-1:0] yin,
   input  wire        [M-1:0]  sigma_in,      // applied when !vectoring
   output wire signed [IW-1:0] xout,
   output wire signed [IW-1:0] yout,
   output wire        [M-1:0]  sigma_out      // recorded when vectoring
   );

   integer i;
   reg signed [IW-1:0] x [0:M];
   reg signed [IW-1:0] y [0:M];
   reg        [M-1:0]  sig;
   reg                 dpos;                  // 1 if this step rotates by +angle

   always @* begin
      x[0] = xin;
      y[0] = yin;
      sig  = {M{1'b0}};
      for (i = 0; i < M; i = i + 1) begin
         if (vectoring)
           // reduce |y|: rotate by -angle when y >= 0, by +angle when y < 0
           dpos = y[i][IW-1];                 // sign bit: 1 = negative -> +angle
         else
           dpos = sigma_in[i];
         sig[i] = dpos;
         if (dpos) begin
            x[i+1] = x[i] - (y[i] >>> i);
            y[i+1] = y[i] + (x[i] >>> i);
         end
         else begin
            x[i+1] = x[i] + (y[i] >>> i);
            y[i+1] = y[i] - (x[i] >>> i);
         end
      end
   end

   assign xout      = x[M];
   assign yout      = y[M];
   assign sigma_out = sig;

endmodule

//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
/******************************************************************************
 * qr_internal: off-diagonal (internal) cell of the systolic QR array.
 *
 * Applies the Givens rotation 'sigma' (from the boundary cell) to a column
 * pair (aj from the pivot row, ai from the row being annihilated) using the
 * CORDIC in rotation mode, then divides out the CORDIC gain K so the rotation
 * is a true (norm-preserving) orthogonal transform. KINV = round(2^15 / K) for
 * the M = 16 configuration; adjust if M changes.
 ******************************************************************************/
module qr_internal
  #(parameter IW = 32,
    parameter M  = 16)
  (
   input  wire        [M-1:0]  sigma,
   input  wire signed [IW-1:0] aj,      // pivot-row element
   input  wire signed [IW-1:0] ai,      // annihilated-row element
   output wire signed [IW-1:0] oj,
   output wire signed [IW-1:0] oi
   );

   // CORDIC gain compensation: 1/K in Q15 (K = prod sqrt(1+2^-2i), M = 16)
   localparam signed [16:0] KINV = 17'sd19898;

   wire signed [IW-1:0] xr, yr;

   qr_cordic #(.IW(IW), .M(M)) u_rot
     (.vectoring (1'b0),
      .xin       (aj),
      .yin       (ai),
      .sigma_in  (sigma),
      .xout      (xr),
      .yout      (yr),
      .sigma_out ());

   wire signed [IW+16:0] pj = xr * KINV;
   wire signed [IW+16:0] pi = yr * KINV;

   assign oj = pj >>> 15;
   assign oi = pi >>> 15;

endmodule
