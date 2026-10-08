// RTLScout provenance note (jpeg_idct_2d_1r1w_verilog): the IDCT pipeline below
// (jpeg_idct_x / jpeg_idct_transpose / jpeg_idct_transpose_ram / jpeg_idct_y) is
// the ultraembedded/core_jpeg upstream (Apache 2.0); jpeg_idct_2d_1r1w and
// jpeg_idct_2d are the study's 1R/1W controller. RTLScout-marked edits, the same
// as in the upstream comparison measurement (jpeg_idct_2d_upstream): the shared
// transpose read pointer is replicated per RAM bank (its clk->Q fanout into the
// flop-mapped RAM read muxes measured ~2.3 ns, an artifact the flow cannot
// buffer away), and jpeg_idct_transpose_ram is reduced to the port subset this
// composition uses (behavior identical); in the study controller the PLAY beat
// counter is replicated per lane for the same fanout reason. Everything else is
// upstream-verbatim.
//-----------------------------------------------------------------
// jpeg_idct_2d_1r1w — flexible-schedule 8x8 2-D IDCT with harness-owned
// memories. This starting point is the SIMPLEST correct schedule: fully load
// the 64 coefficients into a register buffer (one read/cycle, 1-cycle
// registered read), then play the fixed 64-beat schedule into the verbatim
// core_jpeg pipeline (jpeg_idct_2d below), and write each pixel as it streams
// out (row-major position from outport_idx_o). NO overlap of load with
// compute, NO read reuse tricks — discovering better schedules is exactly
// what the benchmark measures. Modify it.
//-----------------------------------------------------------------
module jpeg_idct_2d_1r1w
(
     input          clk
    ,input          rst          // synchronous active-high (harness-driven)
    ,output         rd_en
    ,output [ 5:0]  rd_addr
    ,input  [15:0]  rd_data
    ,output         wr_en
    ,output [ 5:0]  wr_addr
    ,output [31:0]  wr_data
    ,output         done
);

localparam ST_LOAD = 2'd0;
localparam ST_PLAY = 2'd1;
localparam ST_WAIT = 2'd2;

reg [1:0]  state_q;
reg [6:0]  ld_q;                  // read-issue counter 0..64
reg [5:0]  play_q;                // beat counter during PLAY
reg [6:0]  wcnt_q;                // pixels written
reg        done_q;
reg [15:0] buf_q [0:63];          // coefficient buffer

// ---- load phase: one read per cycle, capture with 1-cycle lag ----
wire ld_issue = (state_q == ST_LOAD) && (ld_q < 7'd64);
assign rd_en   = ld_issue;
assign rd_addr = ld_q[5:0];

reg       cap_val_q;
reg [5:0] cap_idx_q;
always @(posedge clk)
if (rst) begin
    cap_val_q <= 1'b0;
    cap_idx_q <= 6'd0;
end else begin
    cap_val_q <= ld_issue;
    cap_idx_q <= ld_q[5:0];
end

always @(posedge clk)
if (cap_val_q)
    buf_q[cap_idx_q] <= rd_data;

// ---- FSM ----
always @(posedge clk)
if (rst) begin
    state_q <= ST_LOAD;
    ld_q    <= 7'd0;
    play_q  <= 6'd0;
end else begin
    case (state_q)
    ST_LOAD:
    begin
        if (ld_issue)
            ld_q <= ld_q + 7'd1;
        if (ld_q == 7'd64 && !cap_val_q)   // last capture retired
            state_q <= ST_PLAY;
    end
    ST_PLAY:
    begin
        play_q <= play_q + 6'd1;
        if (play_q == 6'd63)
            state_q <= ST_WAIT;
    end
    default: ;
    endcase
end

// ---- play phase: the fixed serializer schedule (even/odd lane sets) ----
wire       in_play   = (state_q == ST_PLAY);
wire [2:0] pk        = play_q[2:0];

// RTLScout structural fix (fairness; same treatment as the upstream comparison
// measurements): the beat counter's row/parity bits select the four lane words
// out of the 64-entry coefficient buffer, i.e. they fan out into ~900 mux
// selects; the shared register's clk->Q alone measured ~1.4 ns (the flow runs
// no buffering, synthesis cannot duplicate a register). The counter is
// replicated per lane as whole counters (each D-cone references its own Q, so
// opt_merge cannot re-merge them); play_q keeps the FSM and inport_idx uses.
reg [5:0] rtls_play_rep_q [0:3];
integer   rtls_l;
always @(posedge clk)
for (rtls_l = 0; rtls_l < 4; rtls_l = rtls_l + 1)
    if (rst)
        rtls_play_rep_q[rtls_l] <= 6'd0;
    else if (state_q == ST_PLAY)
        rtls_play_rep_q[rtls_l] <= rtls_play_rep_q[rtls_l] + 6'd1;

wire [2:0] pr0 = rtls_play_rep_q[0][5:3], pr1 = rtls_play_rep_q[1][5:3],
           pr2 = rtls_play_rep_q[2][5:3], pr3 = rtls_play_rep_q[3][5:3];
function even_of; input [2:0] k;
    even_of = (k == 3'd0) || (k == 3'd2) || (k == 3'd5) || (k == 3'd7);
endfunction
wire even0 = even_of(rtls_play_rep_q[0][2:0]), even1 = even_of(rtls_play_rep_q[1][2:0]),
     even2 = even_of(rtls_play_rep_q[2][2:0]), even3 = even_of(rtls_play_rep_q[3][2:0]);

wire [15:0] lane0 = even0 ? buf_q[{pr0, 3'd0}] : buf_q[{pr0, 3'd1}];
wire [15:0] lane1 = even1 ? buf_q[{pr1, 3'd2}] : buf_q[{pr1, 3'd3}];
wire [15:0] lane2 = even2 ? buf_q[{pr2, 3'd4}] : buf_q[{pr2, 3'd5}];
wire [15:0] lane3 = even3 ? buf_q[{pr3, 3'd6}] : buf_q[{pr3, 3'd7}];

wire        p_valid;
wire [31:0] p_data;
wire [ 5:0] p_idx;

jpeg_idct_2d
u_core
(
     .clk_i(clk)
    ,.rst_i(rst)
    ,.img_start_i(1'b0)
    ,.img_end_i(1'b0)
    ,.inport_valid_i(in_play)
    ,.inport_data0_i(in_play ? lane0 : 16'd0)
    ,.inport_data1_i(in_play ? lane1 : 16'd0)
    ,.inport_data2_i(in_play ? lane2 : 16'd0)
    ,.inport_data3_i(in_play ? lane3 : 16'd0)
    ,.inport_idx_i(in_play ? pk : 3'd0)
    ,.outport_valid_o(p_valid)
    ,.outport_data_o(p_data)
    ,.outport_idx_o(p_idx)
);

// ---- write-through of streamed pixels + done ----
assign wr_en   = p_valid;
assign wr_addr = p_idx;
assign wr_data = p_data;

always @(posedge clk)
if (rst) begin
    wcnt_q <= 7'd0;
    done_q <= 1'b0;
end else begin
    if (p_valid)
        wcnt_q <= wcnt_q + 7'd1;
    if (p_valid && wcnt_q == 7'd63)
        done_q <= 1'b1;
end

assign done = done_q;

endmodule
//-----------------------------------------------------------------
// jpeg_idct_2d — full 8x8 2-D IDCT of the baseline JPEG decoder
// (ultraembedded/core_jpeg, Apache 2.0): the verbatim composition of
// jpeg_idct_x -> jpeg_idct_transpose (double-buffered corner-turn RAMs)
// -> jpeg_idct_y, exactly as instantiated in the original jpeg_idct.v
// (downstream ready tied high; the x-pass accepts no backpressure).
// The submodules below are the ORIGINAL core_jpeg sources, unmodified.
//-----------------------------------------------------------------
module jpeg_idct_2d
(
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 15:0]  inport_data0_i
    ,input  [ 15:0]  inport_data1_i
    ,input  [ 15:0]  inport_data2_i
    ,input  [ 15:0]  inport_data3_i
    ,input  [  2:0]  inport_idx_i
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data_o
    ,output [  5:0]  outport_idx_o
);

wire          idct_x_valid_w;
wire [ 31:0]  idct_x_data_w;
wire [  5:0]  idct_x_idx_w;
wire          idct_x_accept_w;

jpeg_idct_x
u_idct_x
(
     .clk_i(clk_i)
    ,.rst_i(rst_i)
    ,.img_start_i(img_start_i)
    ,.img_end_i(img_end_i)
    ,.inport_valid_i(inport_valid_i)
    ,.inport_data0_i(inport_data0_i)
    ,.inport_data1_i(inport_data1_i)
    ,.inport_data2_i(inport_data2_i)
    ,.inport_data3_i(inport_data3_i)
    ,.inport_idx_i(inport_idx_i)
    ,.outport_valid_o(idct_x_valid_w)
    ,.outport_data_o(idct_x_data_w)
    ,.outport_idx_o(idct_x_idx_w)
);

wire          transpose_valid_w;
wire [ 31:0]  transpose_data0_w;
wire [ 31:0]  transpose_data1_w;
wire [ 31:0]  transpose_data2_w;
wire [ 31:0]  transpose_data3_w;
wire [  2:0]  transpose_idx_w;
wire          transpose_ready_w = 1'b1;

jpeg_idct_transpose
u_transpose
(
     .clk_i(clk_i)
    ,.rst_i(rst_i)
    ,.img_start_i(img_start_i)
    ,.img_end_i(img_end_i)
    ,.inport_valid_i(idct_x_valid_w)
    ,.inport_data_i(idct_x_data_w)
    ,.inport_idx_i(idct_x_idx_w)
    ,.inport_accept_o(idct_x_accept_w)
    ,.outport_valid_o(transpose_valid_w)
    ,.outport_data0_o(transpose_data0_w)
    ,.outport_data1_o(transpose_data1_w)
    ,.outport_data2_o(transpose_data2_w)
    ,.outport_data3_o(transpose_data3_w)
    ,.outport_idx_o(transpose_idx_w)
    ,.outport_ready_i(transpose_ready_w)
);

jpeg_idct_y
u_idct_y
(
     .clk_i(clk_i)
    ,.rst_i(rst_i)
    ,.img_start_i(img_start_i)
    ,.img_end_i(img_end_i)
    ,.inport_valid_i(transpose_valid_w)
    ,.inport_data0_i(transpose_data0_w)
    ,.inport_data1_i(transpose_data1_w)
    ,.inport_data2_i(transpose_data2_w)
    ,.inport_data3_i(transpose_data3_w)
    ,.inport_idx_i(transpose_idx_w)
    ,.outport_valid_o(outport_valid_o)
    ,.outport_data_o(outport_data_o)
    ,.outport_idx_o(outport_idx_o)
);

endmodule
//-----------------------------------------------------------------
//                      Baseline JPEG Decoder
//                             V0.1
//                       Ultra-Embedded.com
//                        Copyright 2020
//
//                   admin@ultra-embedded.com
//-----------------------------------------------------------------
//                      License: Apache 2.0
// This IP can be freely used in commercial projects, however you may
// want access to unreleased materials such as verification environments,
// or test vectors, as well as changes to the IP for integration purposes.
// If this is the case, contact the above address.
// I am interested to hear how and where this IP is used, so please get
// in touch!
//-----------------------------------------------------------------
// Copyright 2020 Ultra-Embedded.com
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//-----------------------------------------------------------------

module jpeg_idct_x
//-----------------------------------------------------------------
// Params
//-----------------------------------------------------------------
#(
     parameter OUT_SHIFT        = 11
    ,parameter INPUT_WIDTH      = 16
)
//-----------------------------------------------------------------
// Ports
//-----------------------------------------------------------------
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 15:0]  inport_data0_i
    ,input  [ 15:0]  inport_data1_i
    ,input  [ 15:0]  inport_data2_i
    ,input  [ 15:0]  inport_data3_i
    ,input  [  2:0]  inport_idx_i

    // Outputs
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data_o
    ,output [  5:0]  outport_idx_o
);




localparam [15:0] C1_16 = 4017; // cos( pi/16) x4096
localparam [15:0] C2_16 = 3784; // cos(2pi/16) x4096
localparam [15:0] C3_16 = 3406; // cos(3pi/16) x4096
localparam [15:0] C4_16 = 2896; // cos(4pi/16) x4096
localparam [15:0] C5_16 = 2276; // cos(5pi/16) x4096
localparam [15:0] C6_16 = 1567; // cos(6pi/16) x4096
localparam [15:0] C7_16 = 799;  // cos(7pi/16) x4096

wire signed [31:0] block_in_0_1 = {{16{inport_data0_i[15]}}, inport_data0_i};
wire signed [31:0] block_in_2_3 = {{16{inport_data1_i[15]}}, inport_data1_i};
wire signed [31:0] block_in_4_5 = {{16{inport_data2_i[15]}}, inport_data2_i};
wire signed [31:0] block_in_6_7 = {{16{inport_data3_i[15]}}, inport_data3_i};

//-----------------------------------------------------------------
// IDCT
//-----------------------------------------------------------------
reg signed [31:0] i0;
reg signed [31:0] mul0_a;
reg signed [31:0] mul0_b;
reg signed [31:0] mul1_a;
reg signed [31:0] mul1_b;
reg signed [31:0] mul2_a;
reg signed [31:0] mul2_b;
reg signed [31:0] mul3_a;
reg signed [31:0] mul3_b;
reg signed [31:0] mul4_a;
reg signed [31:0] mul4_b;

always @ (posedge clk_i )
if (rst_i)
begin
    i0     <= 32'b0;
    mul0_a <= 32'b0;
    mul0_b <= 32'b0;
    mul1_a <= 32'b0;
    mul1_b <= 32'b0;
    mul2_a <= 32'b0;
    mul2_b <= 32'b0;
    mul3_a <= 32'b0;
    mul3_b <= 32'b0;
    mul4_a <= 32'b0;
    mul4_b <= 32'b0;
end
else
begin
    /* verilator lint_off WIDTH */
    case (inport_idx_i)
    3'd0:
    begin
        i0     <= block_in_0_1 + block_in_4_5;
        mul0_a <= block_in_2_3;
        mul0_b <= C2_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C6_16;
    end
    3'd1:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C1_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C7_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C5_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C3_16;
        mul4_a <= i0;
        mul4_b <= C4_16;
    end
    3'd2:
    begin
        i0     <= block_in_0_1 - block_in_4_5;
    end
    3'd3:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C7_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C1_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C3_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C5_16;
    end
    3'd4:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C7_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C1_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C3_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C5_16;
    end
    3'd5:
    begin
        mul0_a <= block_in_2_3;
        mul0_b <= C6_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C2_16;
        mul4_a <= i0;
        mul4_b <= C4_16;
    end
    default:
        ;
    endcase
    /* verilator lint_on WIDTH */
end

reg signed [31:0] mul0_q;
reg signed [31:0] mul1_q;
reg signed [31:0] mul2_q;
reg signed [31:0] mul3_q;
reg signed [31:0] mul4_q;

always @ (posedge clk_i )
if (rst_i)
begin
    mul0_q <= 32'b0;
    mul1_q <= 32'b0;
    mul2_q <= 32'b0;
    mul3_q <= 32'b0;
    mul4_q <= 32'b0;
end
else
begin
    mul0_q <= mul0_a * mul0_b;
    mul1_q <= mul1_a * mul1_b;
    mul2_q <= mul2_a * mul2_b;
    mul3_q <= mul3_a * mul3_b;
    mul4_q <= mul4_a * mul4_b;
end

reg signed [31:0] mul0;
reg signed [31:0] mul1;
reg signed [31:0] mul2;
reg signed [31:0] mul3;
reg signed [31:0] mul4;

always @ (posedge clk_i )
if (rst_i)
begin
    mul0 <= 32'b0;
    mul1 <= 32'b0;
    mul2 <= 32'b0;
    mul3 <= 32'b0;
    mul4 <= 32'b0;
end
else
begin
    mul0 <= mul0_q;
    mul1 <= mul1_q;
    mul2 <= mul2_q;
    mul3 <= mul3_q;
    mul4 <= mul4_q;
end

reg        out_stg0_valid_q;
reg [2:0]  out_stg0_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg0_valid_q <= 1'b0;
    out_stg0_idx_q   <= 3'b0;
end
else
begin
    out_stg0_valid_q <= inport_valid_i;
    out_stg0_idx_q   <= inport_idx_i;
end

reg        out_stg1_valid_q;
reg [2:0]  out_stg1_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg1_valid_q <= 1'b0;
    out_stg1_idx_q   <= 3'b0;
end
else
begin
    out_stg1_valid_q <= out_stg0_valid_q;
    out_stg1_idx_q   <= out_stg0_idx_q;
end

reg        out_stg2_valid_q;
reg [2:0]  out_stg2_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg2_valid_q <= 1'b0;
    out_stg2_idx_q   <= 3'b0;
end
else
begin
    out_stg2_valid_q <= out_stg1_valid_q;
    out_stg2_idx_q   <= out_stg1_idx_q;
end

reg signed [31:0] o_s5;
reg signed [31:0] o_s6;
reg signed [31:0] o_s7;
reg signed [31:0] o_t0;
reg signed [31:0] o_t1;
reg signed [31:0] o_t2;
reg signed [31:0] o_t3;
reg signed [31:0] o_t4;
reg signed [31:0] o_t5;
reg signed [31:0] o_t6;
reg signed [31:0] o_t7;
reg signed [31:0] o_t6_5;
reg signed [31:0] o_t5_6;

always @ (posedge clk_i )
if (rst_i)
begin
    o_s5   <= 32'b0;
    o_s6   <= 32'b0;
    o_s7   <= 32'b0;
    o_t0   <= 32'b0;
    o_t1   <= 32'b0;
    o_t2   <= 32'b0;
    o_t3   <= 32'b0;
    o_t4   <= 32'b0;
    o_t5   <= 32'b0;
    o_t6   <= 32'b0;
    o_t7   <= 32'b0;
    o_t6_5 <= 32'b0;
    o_t5_6 <= 32'b0;
end
else
begin
    case (out_stg2_idx_q)
    3'd0:
    begin
        o_t3 <= mul0 + mul1; // s3
    end
    3'd1:
    begin
        o_s7 <= mul0 + mul1;
        o_s6 <= mul2 + mul3;
        o_t0 <= mul4;        // s0
    end
    3'd2:
    begin
        o_t0 <= o_t0 + o_t3; // t0
        o_t3 <= o_t0 - o_t3; // t3
        o_t7 <= o_s6 + o_s7;
    end
    3'd3:
    begin
        o_t4 <= (mul0 - mul1) + (mul2 - mul3);
    end
    3'd4:
    begin
        o_t0 <= mul0 - mul1; // s4
        o_s5 <= mul2 - mul3;    
    end
    3'd5:
    begin
        o_t3 <= mul0 - mul1; // s2
        o_t4 <= mul4; // s1
        o_t5 <= o_t0 - o_s5;
        o_t6 <= o_s7 - o_s6;
    end
    3'd6:
    begin
        o_t1 <= o_t4 + o_t3;
        o_t2 <= o_t4 - o_t3;
        o_t6_5 <= o_t6 - o_t5;
        o_t5_6 <= o_t5 + o_t6;
    end
    default:
    begin
        o_s5 <= (o_t6_5 * 181) / 256; // 1/sqrt(2)
        o_s6 <= (o_t5_6 * 181) / 256; // 1/sqrt(2)
    end
    endcase
end

reg        out_stg3_valid_q;
reg [2:0]  out_stg3_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg3_valid_q <= 1'b0;
    out_stg3_idx_q   <= 3'b0;
end
else
begin
    out_stg3_valid_q <= out_stg2_valid_q;
    out_stg3_idx_q   <= out_stg2_idx_q;
end

reg signed [31:0] block_out[0:7];
reg signed [31:0] block_out_tmp;

always @ (posedge clk_i )
if (rst_i)
begin
    block_out[0] <= 32'b0;
    block_out[1] <= 32'b0;
    block_out[2] <= 32'b0;
    block_out[3] <= 32'b0;
    block_out[4] <= 32'b0;
    block_out[5] <= 32'b0;
    block_out[6] <= 32'b0;
    block_out[7] <= 32'b0;
    block_out_tmp <= 32'b0;
end
else if (out_stg3_valid_q)
begin
    if (out_stg3_idx_q == 3'd3)
    begin
        block_out[0] <= ((o_t0 + o_t7) >>> OUT_SHIFT);
        block_out_tmp <= ((o_t0 - o_t7) >>> OUT_SHIFT); // block_out[7]
        block_out[3] <= ((o_t3 + o_t4) >>> OUT_SHIFT);
        block_out[4] <= ((o_t3 - o_t4) >>> OUT_SHIFT);
    end

    if (out_stg3_idx_q == 3'd6)
        block_out[7] <= block_out_tmp;

    if (out_stg3_idx_q == 3'd7)
    begin
        block_out[2] <= ((o_t2 + o_s5) >>> OUT_SHIFT);
        block_out[5] <= ((o_t2 - o_s5) >>> OUT_SHIFT);
        block_out[1] <= ((o_t1 + o_s6) >>> OUT_SHIFT);
        block_out[6] <= ((o_t1 - o_s6) >>> OUT_SHIFT);
    end
end

reg [7:0] valid_q;

always @ (posedge clk_i )
if (rst_i)
    valid_q  <= 8'b0;
else if (img_start_i)
    valid_q  <= 8'b0;
else
    valid_q <= {valid_q[6:0], out_stg3_valid_q};

reg [5:0] ptr_q;

always @ (posedge clk_i )
if (rst_i)
    ptr_q <= 6'd0;
else if (img_start_i)
    ptr_q <= 6'd0;
else if (outport_valid_o)
    ptr_q <= ptr_q + 6'd1;

assign outport_valid_o = valid_q[6];
assign outport_data_o  = block_out[ptr_q[2:0]];

assign outport_idx_o   = ptr_q;



endmodule
//-----------------------------------------------------------------
//                      Baseline JPEG Decoder
//                             V0.1
//                       Ultra-Embedded.com
//                        Copyright 2020
//
//                   admin@ultra-embedded.com
//-----------------------------------------------------------------
//                      License: Apache 2.0
// This IP can be freely used in commercial projects, however you may
// want access to unreleased materials such as verification environments,
// or test vectors, as well as changes to the IP for integration purposes.
// If this is the case, contact the above address.
// I am interested to hear how and where this IP is used, so please get
// in touch!
//-----------------------------------------------------------------
// Copyright 2020 Ultra-Embedded.com
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//-----------------------------------------------------------------

module jpeg_idct_transpose
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 31:0]  inport_data_i
    ,input  [  5:0]  inport_idx_i
    ,input           outport_ready_i

    // Outputs
    ,output          inport_accept_o
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data0_o
    ,output [ 31:0]  outport_data1_o
    ,output [ 31:0]  outport_data2_o
    ,output [ 31:0]  outport_data3_o
    ,output [  2:0]  outport_idx_o
);



reg         block_wr_q;
reg         block_rd_q;
reg [5:0]   rd_idx_q;
reg [3:0]   rd_addr_q;

wire [4:0]  wr_ptr_w = {block_wr_q, inport_idx_i[3:0]};

wire [31:0] outport_data0_w;
wire [31:0] outport_data1_w;
wire [31:0] outport_data2_w;
wire [31:0] outport_data3_w;

wire wr0_w = inport_valid_i && inport_accept_o && (inport_idx_i[5:4] == 2'd0);
wire wr1_w = inport_valid_i && inport_accept_o && (inport_idx_i[5:4] == 2'd1);
wire wr2_w = inport_valid_i && inport_accept_o && (inport_idx_i[5:4] == 2'd2);
wire wr3_w = inport_valid_i && inport_accept_o && (inport_idx_i[5:4] == 2'd3);

jpeg_idct_transpose_ram
u_ram0
(
     .clk0_i(clk_i)
    ,.rst0_i(rst_i)
    ,.clk1_i(clk_i)
    ,.rst1_i(rst_i)

    ,.addr0_i(wr_ptr_w)
    ,.data0_i(inport_data_i)
    ,.wr0_i(wr0_w)
    ,.data0_o()

    ,.addr1_i({rtls_block_rd_rep_q[0], rtls_rd_addr_rep_q[0]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(32'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data0_w)
);

jpeg_idct_transpose_ram
u_ram1
(
     .clk0_i(clk_i)
    ,.rst0_i(rst_i)
    ,.clk1_i(clk_i)
    ,.rst1_i(rst_i)

    ,.addr0_i(wr_ptr_w)
    ,.data0_i(inport_data_i)
    ,.wr0_i(wr1_w)
    ,.data0_o()

    ,.addr1_i({rtls_block_rd_rep_q[1], rtls_rd_addr_rep_q[1]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(32'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data1_w)
);

jpeg_idct_transpose_ram
u_ram2
(
     .clk0_i(clk_i)
    ,.rst0_i(rst_i)
    ,.clk1_i(clk_i)
    ,.rst1_i(rst_i)

    ,.addr0_i(wr_ptr_w)
    ,.data0_i(inport_data_i)
    ,.wr0_i(wr2_w)
    ,.data0_o()

    ,.addr1_i({rtls_block_rd_rep_q[2], rtls_rd_addr_rep_q[2]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(32'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data2_w)
);

jpeg_idct_transpose_ram
u_ram3
(
     .clk0_i(clk_i)
    ,.rst0_i(rst_i)
    ,.clk1_i(clk_i)
    ,.rst1_i(rst_i)

    ,.addr0_i(wr_ptr_w)
    ,.data0_i(inport_data_i)
    ,.wr0_i(wr3_w)
    ,.data0_o()

    ,.addr1_i({rtls_block_rd_rep_q[3], rtls_rd_addr_rep_q[3]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(32'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data3_w)
);

//-----------------------------------------------------------------
// Input Buffer
//-----------------------------------------------------------------
reg [1:0] block_ready_q;

always @ (posedge clk_i )
if (rst_i)
begin
    block_ready_q     <= 2'b0;
    block_wr_q        <= 1'b0;
    block_rd_q        <= 1'b0;
end
else if (img_start_i)
begin
    block_ready_q     <= 2'b0;
    block_wr_q        <= 1'b0;
    block_rd_q        <= 1'b0;
end
else
begin
    if (inport_valid_i && inport_idx_i == 6'd63 && inport_accept_o)
    begin
        block_ready_q[block_wr_q] <= 1'b1;
        block_wr_q                <= ~block_wr_q;
    end

    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        block_ready_q[block_rd_q] <= 1'b0;
        block_rd_q <= ~block_rd_q;
    end
end

assign inport_accept_o = ~block_ready_q[block_wr_q];

//-----------------------------------------------------------------
// FSM
//-----------------------------------------------------------------
localparam STATE_W           = 2;
localparam STATE_IDLE        = 2'd0;
localparam STATE_SETUP       = 2'd1;
localparam STATE_ACTIVE      = 2'd2;

reg [STATE_W-1:0] state_q;
reg [STATE_W-1:0] next_state_r;

always @ *
begin
    next_state_r = state_q;

    case (state_q)
    STATE_IDLE:
    begin
        if (block_ready_q[block_rd_q] && outport_ready_i)
            next_state_r = STATE_SETUP;
    end
    STATE_SETUP:
    begin
        next_state_r = STATE_ACTIVE;
    end
    STATE_ACTIVE:
    begin
        if (outport_valid_o && rd_idx_q == 6'd63)
            next_state_r = STATE_IDLE;
    end
    default: ;
    endcase

    if (img_start_i)
        next_state_r = STATE_IDLE;
end

always @ (posedge clk_i )
if (rst_i)
    state_q <= STATE_IDLE;
else
    state_q <= next_state_r;

always @ (posedge clk_i )
if (rst_i)
    rd_idx_q <= 6'b0;
else if (img_start_i)
    rd_idx_q <= 6'b0;
else if (state_q == STATE_ACTIVE)
    rd_idx_q <= rd_idx_q + 6'd1;

always @ (posedge clk_i )
if (rst_i)
    rd_addr_q <= 4'b0;
else if (state_q == STATE_IDLE)
    rd_addr_q <= 4'b0;
else if (state_q == STATE_SETUP)
    rd_addr_q <= 4'd8;
else if (state_q == STATE_ACTIVE)
begin
    case (rd_idx_q[2:0])
    3'd0: rd_addr_q <= rd_addr_q - 4'd8;
    3'd1: rd_addr_q <= rd_addr_q + 4'd8;
    3'd2: ;
    3'd3: rd_addr_q <= rd_addr_q - 4'd8;
    3'd4: rd_addr_q <= rd_addr_q + 4'd8;
    3'd5: rd_addr_q <= rd_addr_q - 4'd8;
    3'd6: rd_addr_q <= rd_addr_q + 4'd1;
    3'd7: rd_addr_q <= rd_addr_q + 4'd8;
    endcase
end

// RTLScout structural fix (fairness): the shared read pointer
// {block_rd_q, rd_addr_q} fed the read address of all four flop-mapped
// corner-turn RAMs; each pointer bit fans out into hundreds of mux-select
// sinks and the shared register's clk->Q alone measured ~2.3 ns. The flow
// runs no repair_design/buffer insertion and synthesis cannot duplicate a
// register, so the pointer is replicated per RAM instance here (the recast
// starting point avoids the artifact the same way, structurally at RTL).
// Each replica is a whole counter whose D-cone references its own Q, so
// opt_merge cannot re-merge them; the update logic mirrors the original
// blocks above exactly. Originals left in place for control uses.
reg [3:0] rtls_rd_addr_rep_q  [0:3];
reg       rtls_block_rd_rep_q [0:3];
integer   rtls_k;
always @ (posedge clk_i )
for (rtls_k = 0; rtls_k < 4; rtls_k = rtls_k + 1)
begin
    if (rst_i)
    begin
        rtls_rd_addr_rep_q[rtls_k]  <= 4'b0;
        rtls_block_rd_rep_q[rtls_k] <= 1'b0;
    end
    else
    begin
        if (img_start_i)
            rtls_block_rd_rep_q[rtls_k] <= 1'b0;
        else if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
            rtls_block_rd_rep_q[rtls_k] <= ~rtls_block_rd_rep_q[rtls_k];

        if (state_q == STATE_IDLE)
            rtls_rd_addr_rep_q[rtls_k] <= 4'b0;
        else if (state_q == STATE_SETUP)
            rtls_rd_addr_rep_q[rtls_k] <= 4'd8;
        else if (state_q == STATE_ACTIVE)
        begin
            case (rd_idx_q[2:0])
            3'd0: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 4'd8;
            3'd1: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 4'd8;
            3'd2: ;
            3'd3: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 4'd8;
            3'd4: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 4'd8;
            3'd5: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 4'd8;
            3'd6: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 4'd1;
            3'd7: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 4'd8;
            endcase
        end
    end
end

assign outport_valid_o = (state_q == STATE_ACTIVE);
assign outport_idx_o   = rd_idx_q[2:0];
assign outport_data0_o = outport_data0_w;
assign outport_data1_o = outport_data1_w;
assign outport_data2_o = outport_data2_w;
assign outport_data3_o = outport_data3_w;


endmodule
//-----------------------------------------------------------------
//                      Baseline JPEG Decoder
//                             V0.1
//                       Ultra-Embedded.com
//                        Copyright 2020
//
//                   admin@ultra-embedded.com
//-----------------------------------------------------------------
//                      License: Apache 2.0
// This IP can be freely used in commercial projects, however you may
// want access to unreleased materials such as verification environments,
// or test vectors, as well as changes to the IP for integration purposes.
// If this is the case, contact the above address.
// I am interested to hear how and where this IP is used, so please get
// in touch!
//-----------------------------------------------------------------
// Copyright 2020 Ultra-Embedded.com
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//-----------------------------------------------------------------

module jpeg_idct_transpose_ram
//-----------------------------------------------------------------
// RTLScout mechanical edit (jpeg_idct_2d_1r1w): reduced to the port subset actually used in this
// composition — port0 writes, port1 does a registered read-first read (the
// original's port1 write and port0 read are tied off by jpeg_idct_transpose).
// Written as a single always block so synthesis maps it to flops; the original
// two-always multi-driven form is kept behavioral by yosys and breaks the
// PPA (STA) flow this benchmark is scored with. Behavior is identical here.
//-----------------------------------------------------------------
(
    // Inputs
     input           clk0_i
    ,input           rst0_i
    ,input  [  4:0]  addr0_i
    ,input  [ 31:0]  data0_i
    ,input           wr0_i
    ,input           clk1_i
    ,input           rst1_i
    ,input  [  4:0]  addr1_i
    ,input  [ 31:0]  data1_i
    ,input           wr1_i

    // Outputs
    ,output [ 31:0]  data0_o
    ,output [ 31:0]  data1_o
);

reg [31:0]   ram [31:0];
reg [31:0]   ram_read1_q;

always @ (posedge clk0_i)
begin
    if (wr0_i)
        ram[addr0_i] <= data0_i;

    ram_read1_q <= ram[addr1_i];    // read-first (old value on same-address write)
end

assign data0_o = 32'b0;
assign data1_o = ram_read1_q;

endmodule
//-----------------------------------------------------------------
//                      Baseline JPEG Decoder
//                             V0.1
//                       Ultra-Embedded.com
//                        Copyright 2020
//
//                   admin@ultra-embedded.com
//-----------------------------------------------------------------
//                      License: Apache 2.0
// This IP can be freely used in commercial projects, however you may
// want access to unreleased materials such as verification environments,
// or test vectors, as well as changes to the IP for integration purposes.
// If this is the case, contact the above address.
// I am interested to hear how and where this IP is used, so please get
// in touch!
//-----------------------------------------------------------------
// Copyright 2020 Ultra-Embedded.com
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//-----------------------------------------------------------------

module jpeg_idct_y
//-----------------------------------------------------------------
// Params
//-----------------------------------------------------------------
#(
     parameter OUT_SHIFT        = 15
    ,parameter INPUT_WIDTH      = 32
)
//-----------------------------------------------------------------
// Ports
//-----------------------------------------------------------------
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 31:0]  inport_data0_i
    ,input  [ 31:0]  inport_data1_i
    ,input  [ 31:0]  inport_data2_i
    ,input  [ 31:0]  inport_data3_i
    ,input  [  2:0]  inport_idx_i

    // Outputs
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data_o
    ,output [  5:0]  outport_idx_o
);




localparam [15:0] C1_16 = 4017; // cos( pi/16) x4096
localparam [15:0] C2_16 = 3784; // cos(2pi/16) x4096
localparam [15:0] C3_16 = 3406; // cos(3pi/16) x4096
localparam [15:0] C4_16 = 2896; // cos(4pi/16) x4096
localparam [15:0] C5_16 = 2276; // cos(5pi/16) x4096
localparam [15:0] C6_16 = 1567; // cos(6pi/16) x4096
localparam [15:0] C7_16 = 799;  // cos(7pi/16) x4096

wire signed [31:0] block_in_0_1 = inport_data0_i;
wire signed [31:0] block_in_2_3 = inport_data1_i;
wire signed [31:0] block_in_4_5 = inport_data2_i;
wire signed [31:0] block_in_6_7 = inport_data3_i;

//-----------------------------------------------------------------
// IDCT
//-----------------------------------------------------------------
reg signed [31:0] i0;
reg signed [31:0] mul0_a;
reg signed [31:0] mul0_b;
reg signed [31:0] mul1_a;
reg signed [31:0] mul1_b;
reg signed [31:0] mul2_a;
reg signed [31:0] mul2_b;
reg signed [31:0] mul3_a;
reg signed [31:0] mul3_b;
reg signed [31:0] mul4_a;
reg signed [31:0] mul4_b;

always @ (posedge clk_i )
if (rst_i)
begin
    i0     <= 32'b0;
    mul0_a <= 32'b0;
    mul0_b <= 32'b0;
    mul1_a <= 32'b0;
    mul1_b <= 32'b0;
    mul2_a <= 32'b0;
    mul2_b <= 32'b0;
    mul3_a <= 32'b0;
    mul3_b <= 32'b0;
    mul4_a <= 32'b0;
    mul4_b <= 32'b0;
end
else
begin
    /* verilator lint_off WIDTH */
    case (inport_idx_i)
    3'd0:
    begin
        i0     <= block_in_0_1 + block_in_4_5;
        mul0_a <= block_in_2_3;
        mul0_b <= C2_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C6_16;
    end
    3'd1:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C1_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C7_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C5_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C3_16;
        mul4_a <= i0;
        mul4_b <= C4_16;
    end
    3'd2:
    begin
        i0     <= block_in_0_1 - block_in_4_5;
    end
    3'd3:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C7_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C1_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C3_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C5_16;
    end
    3'd4:
    begin
        mul0_a <= block_in_0_1;
        mul0_b <= C7_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C1_16;
        mul2_a <= block_in_4_5;
        mul2_b <= C3_16;
        mul3_a <= block_in_2_3;
        mul3_b <= C5_16;
    end
    3'd5:
    begin
        mul0_a <= block_in_2_3;
        mul0_b <= C6_16;
        mul1_a <= block_in_6_7;
        mul1_b <= C2_16;
        mul4_a <= i0;
        mul4_b <= C4_16;
    end
    default:
        ;
    endcase
    /* verilator lint_on WIDTH */
end

reg signed [31:0] mul0_q;
reg signed [31:0] mul1_q;
reg signed [31:0] mul2_q;
reg signed [31:0] mul3_q;
reg signed [31:0] mul4_q;

always @ (posedge clk_i )
if (rst_i)
begin
    mul0_q <= 32'b0;
    mul1_q <= 32'b0;
    mul2_q <= 32'b0;
    mul3_q <= 32'b0;
    mul4_q <= 32'b0;
end
else
begin
    mul0_q <= mul0_a * mul0_b;
    mul1_q <= mul1_a * mul1_b;
    mul2_q <= mul2_a * mul2_b;
    mul3_q <= mul3_a * mul3_b;
    mul4_q <= mul4_a * mul4_b;
end

reg signed [31:0] mul0;
reg signed [31:0] mul1;
reg signed [31:0] mul2;
reg signed [31:0] mul3;
reg signed [31:0] mul4;

always @ (posedge clk_i )
if (rst_i)
begin
    mul0 <= 32'b0;
    mul1 <= 32'b0;
    mul2 <= 32'b0;
    mul3 <= 32'b0;
    mul4 <= 32'b0;
end
else
begin
    mul0 <= mul0_q;
    mul1 <= mul1_q;
    mul2 <= mul2_q;
    mul3 <= mul3_q;
    mul4 <= mul4_q;
end

reg        out_stg0_valid_q;
reg [2:0]  out_stg0_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg0_valid_q <= 1'b0;
    out_stg0_idx_q   <= 3'b0;
end
else
begin
    out_stg0_valid_q <= inport_valid_i;
    out_stg0_idx_q   <= inport_idx_i;
end

reg        out_stg1_valid_q;
reg [2:0]  out_stg1_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg1_valid_q <= 1'b0;
    out_stg1_idx_q   <= 3'b0;
end
else
begin
    out_stg1_valid_q <= out_stg0_valid_q;
    out_stg1_idx_q   <= out_stg0_idx_q;
end

reg        out_stg2_valid_q;
reg [2:0]  out_stg2_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg2_valid_q <= 1'b0;
    out_stg2_idx_q   <= 3'b0;
end
else
begin
    out_stg2_valid_q <= out_stg1_valid_q;
    out_stg2_idx_q   <= out_stg1_idx_q;
end

reg signed [31:0] o_s5;
reg signed [31:0] o_s6;
reg signed [31:0] o_s7;
reg signed [31:0] o_t0;
reg signed [31:0] o_t1;
reg signed [31:0] o_t2;
reg signed [31:0] o_t3;
reg signed [31:0] o_t4;
reg signed [31:0] o_t5;
reg signed [31:0] o_t6;
reg signed [31:0] o_t7;
reg signed [31:0] o_t6_5;
reg signed [31:0] o_t5_6;

always @ (posedge clk_i )
if (rst_i)
begin
    o_s5   <= 32'b0;
    o_s6   <= 32'b0;
    o_s7   <= 32'b0;
    o_t0   <= 32'b0;
    o_t1   <= 32'b0;
    o_t2   <= 32'b0;
    o_t3   <= 32'b0;
    o_t4   <= 32'b0;
    o_t5   <= 32'b0;
    o_t6   <= 32'b0;
    o_t7   <= 32'b0;
    o_t6_5 <= 32'b0;
    o_t5_6 <= 32'b0;
end
else
begin
    case (out_stg2_idx_q)
    3'd0:
    begin
        o_t3 <= mul0 + mul1; // s3
    end
    3'd1:
    begin
        o_s7 <= mul0 + mul1;
        o_s6 <= mul2 + mul3;
        o_t0 <= mul4;        // s0
    end
    3'd2:
    begin
        o_t0 <= o_t0 + o_t3; // t0
        o_t3 <= o_t0 - o_t3; // t3
        o_t7 <= o_s6 + o_s7;
    end
    3'd3:
    begin
        o_t4 <= (mul0 - mul1) + (mul2 - mul3);
    end
    3'd4:
    begin
        o_t0 <= mul0 - mul1; // s4
        o_s5 <= mul2 - mul3;    
    end
    3'd5:
    begin
        o_t3 <= mul0 - mul1; // s2
        o_t4 <= mul4; // s1
        o_t5 <= o_t0 - o_s5;
        o_t6 <= o_s7 - o_s6;
    end
    3'd6:
    begin
        o_t1 <= o_t4 + o_t3;
        o_t2 <= o_t4 - o_t3;
        o_t6_5 <= o_t6 - o_t5;
        o_t5_6 <= o_t5 + o_t6;
    end
    default:
    begin
        o_s5 <= (o_t6_5 * 181) / 256; // 1/sqrt(2)
        o_s6 <= (o_t5_6 * 181) / 256; // 1/sqrt(2)
    end
    endcase
end

reg        out_stg3_valid_q;
reg [2:0]  out_stg3_idx_q;

always @ (posedge clk_i )
if (rst_i)
begin
    out_stg3_valid_q <= 1'b0;
    out_stg3_idx_q   <= 3'b0;
end
else
begin
    out_stg3_valid_q <= out_stg2_valid_q;
    out_stg3_idx_q   <= out_stg2_idx_q;
end

reg signed [31:0] block_out[0:7];
reg signed [31:0] block_out_tmp;

always @ (posedge clk_i )
if (rst_i)
begin
    block_out[0] <= 32'b0;
    block_out[1] <= 32'b0;
    block_out[2] <= 32'b0;
    block_out[3] <= 32'b0;
    block_out[4] <= 32'b0;
    block_out[5] <= 32'b0;
    block_out[6] <= 32'b0;
    block_out[7] <= 32'b0;
    block_out_tmp <= 32'b0;
end
else if (out_stg3_valid_q)
begin
    if (out_stg3_idx_q == 3'd3)
    begin
        block_out[0] <= ((o_t0 + o_t7) >>> OUT_SHIFT);
        block_out_tmp <= ((o_t0 - o_t7) >>> OUT_SHIFT); // block_out[7]
        block_out[3] <= ((o_t3 + o_t4) >>> OUT_SHIFT);
        block_out[4] <= ((o_t3 - o_t4) >>> OUT_SHIFT);
    end

    if (out_stg3_idx_q == 3'd6)
        block_out[7] <= block_out_tmp;

    if (out_stg3_idx_q == 3'd7)
    begin
        block_out[2] <= ((o_t2 + o_s5) >>> OUT_SHIFT);
        block_out[5] <= ((o_t2 - o_s5) >>> OUT_SHIFT);
        block_out[1] <= ((o_t1 + o_s6) >>> OUT_SHIFT);
        block_out[6] <= ((o_t1 - o_s6) >>> OUT_SHIFT);
    end
end

reg [7:0] valid_q;

always @ (posedge clk_i )
if (rst_i)
    valid_q  <= 8'b0;
else if (img_start_i)
    valid_q  <= 8'b0;
else
    valid_q <= {valid_q[6:0], out_stg3_valid_q};

reg [5:0] ptr_q;

always @ (posedge clk_i )
if (rst_i)
    ptr_q <= 6'd0;
else if (img_start_i)
    ptr_q <= 6'd0;
else if (outport_valid_o)
    ptr_q <= ptr_q + 6'd1;

assign outport_valid_o = valid_q[6];
assign outport_data_o  = block_out[ptr_q[2:0]];



function [5:0] ptr_conv;
    input [5:0] idx;
    reg [5:0] out_idx;
begin
    case (idx)
    6'd0:  out_idx = 6'd0;
    6'd1:  out_idx = 6'd8;
    6'd2:  out_idx = 6'd16;
    6'd3:  out_idx = 6'd24;
    6'd4:  out_idx = 6'd32;
    6'd5:  out_idx = 6'd40;
    6'd6:  out_idx = 6'd48;
    6'd7:  out_idx = 6'd56;
    6'd8:  out_idx = 6'd1;
    6'd9:  out_idx = 6'd9;
    6'd10:  out_idx = 6'd17;
    6'd11:  out_idx = 6'd25;
    6'd12:  out_idx = 6'd33;
    6'd13:  out_idx = 6'd41;
    6'd14:  out_idx = 6'd49;
    6'd15:  out_idx = 6'd57;
    6'd16:  out_idx = 6'd2;
    6'd17:  out_idx = 6'd10;
    6'd18:  out_idx = 6'd18;
    6'd19:  out_idx = 6'd26;
    6'd20:  out_idx = 6'd34;
    6'd21:  out_idx = 6'd42;
    6'd22:  out_idx = 6'd50;
    6'd23:  out_idx = 6'd58;
    6'd24:  out_idx = 6'd3;
    6'd25:  out_idx = 6'd11;
    6'd26:  out_idx = 6'd19;
    6'd27:  out_idx = 6'd27;
    6'd28:  out_idx = 6'd35;
    6'd29:  out_idx = 6'd43;
    6'd30:  out_idx = 6'd51;
    6'd31:  out_idx = 6'd59;
    6'd32:  out_idx = 6'd4;
    6'd33:  out_idx = 6'd12;
    6'd34:  out_idx = 6'd20;
    6'd35:  out_idx = 6'd28;
    6'd36:  out_idx = 6'd36;
    6'd37:  out_idx = 6'd44;
    6'd38:  out_idx = 6'd52;
    6'd39:  out_idx = 6'd60;
    6'd40:  out_idx = 6'd5;
    6'd41:  out_idx = 6'd13;
    6'd42:  out_idx = 6'd21;
    6'd43:  out_idx = 6'd29;
    6'd44:  out_idx = 6'd37;
    6'd45:  out_idx = 6'd45;
    6'd46:  out_idx = 6'd53;
    6'd47:  out_idx = 6'd61;
    6'd48:  out_idx = 6'd6;
    6'd49:  out_idx = 6'd14;
    6'd50:  out_idx = 6'd22;
    6'd51:  out_idx = 6'd30;
    6'd52:  out_idx = 6'd38;
    6'd53:  out_idx = 6'd46;
    6'd54:  out_idx = 6'd54;
    6'd55:  out_idx = 6'd62;
    6'd56:  out_idx = 6'd7;
    6'd57:  out_idx = 6'd15;
    6'd58:  out_idx = 6'd23;
    6'd59:  out_idx = 6'd31;
    6'd60:  out_idx = 6'd39;
    6'd61:  out_idx = 6'd47;
    6'd62:  out_idx = 6'd55;
    default:  out_idx = 6'd63;
    endcase

    ptr_conv = out_idx;
end
endfunction


assign outport_idx_o   = ptr_conv(ptr_q);



endmodule
