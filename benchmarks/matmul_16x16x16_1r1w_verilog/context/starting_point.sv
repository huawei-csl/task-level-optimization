// Starting point for matmul/matmul_16x16x16_1r1w_verilog -- the DUMBEST correct design.
//
// Single multiplier, single 20-bit accumulator, FSM walking (i, j, l). Honors
// the harness's 1-cycle registered read. NO operand buffering, NO parallelism,
// NO tiling -- discovering those is exactly what the benchmark measures. This is
// the correct reference and the cost baseline (~12.5k cycles per matrix, tiny
// area). Synchronous active-high reset. Modify it to optimize.
//
// Address maps (row-major, flat): A[i][l] at i*16+l = {i,l}; B[l][j] at
// 256+l*16+j = {1'b1,l,j}; C[i][j] at i*16+j = {i,j}.
module matmul_core (
  input  wire        clk,
  input  wire        rst,        // synchronous, active-high
  output wire        rd_en,
  output wire [8:0]  rd_addr,
  input  wire [7:0]  rd_data,    // valid exactly 1 cycle after rd_en (SRAM-like)
  output wire        wr_en,
  output wire [7:0]  wr_addr,
  output wire [19:0] wr_data,
  output wire        done
);
  localparam [2:0] S_A = 3'd0, S_B = 3'd1, S_MAC = 3'd2, S_W = 3'd3, S_D = 3'd4;
  localparam integer K = 16, N = 16, M = 16;

  reg [2:0]  state;
  reg [3:0]  i, j, l;   // 0..15
  reg [7:0]  a_q;       // captured A operand across the 1-cycle read skew
  reg [19:0] acc;

  // Combinational outputs decoded from the current state. The {.,.} forms are
  // exact (i,l,j are 4-bit, so i*16+l == {i,l}), avoiding width truncation.
  assign rd_en   = (state == S_A) || (state == S_B);
  assign rd_addr = (state == S_A) ? {1'b0, i, l}    // A[i][l]
                 : (state == S_B) ? {1'b1, l, j}    // B[l][j] (base 256)
                 : 9'd0;
  assign wr_en   = (state == S_W);
  assign wr_addr = {i, j};                          // C[i][j]
  assign wr_data = acc;
  assign done    = (state == S_D);                  // sticky once in S_D

  always @(posedge clk) begin
    if (rst) begin
      state <= S_A; i <= 4'd0; j <= 4'd0; l <= 4'd0; acc <= 20'd0; a_q <= 8'd0;
    end else begin
      case (state)
        S_A: state <= S_B;                                   // A read issued
        S_B: begin a_q <= rd_data; state <= S_MAC; end       // capture A, issue B
        S_MAC: begin
          acc <= acc + a_q * rd_data;                        // capture B, accumulate
          if (l == K-1) state <= S_W;
          else begin l <= l + 4'd1; state <= S_A; end
        end
        S_W: begin
          acc <= 20'd0; l <= 4'd0;
          if (j == N-1) begin
            j <= 4'd0;
            if (i == M-1) state <= S_D;
            else begin i <= i + 4'd1; state <= S_A; end
          end else begin
            j <= j + 4'd1; state <= S_A;
          end
        end
        default: state <= S_D;                               // S_D: hold (done sticky)
      endcase
    end
  end
endmodule
