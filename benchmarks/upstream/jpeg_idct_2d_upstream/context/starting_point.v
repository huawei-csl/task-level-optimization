// jpeg_idct_2d_upstream starting point: the VERBATIM ultraembedded/core_jpeg
// 2-D IDCT at its FULL upstream top `jpeg_idct` -- including the upstream's
// own input stage jpeg_idct_ram (real ingress: ONE 16-bit coefficient per
// cycle with idx/eob and accept backpressure), NOT the internal 4-lane
// interface exposed by jpeg_idct_2d_verilog.
// The eight upstream files below originate from src_v/ at master (Apache 2.0).
// The only edits are explicitly RTLScout-marked. The study-written pass-through
// wrapper at the bottom renames clk_i/rst_i to the clk/rst port names required
// for clock discovery and ties off the id ports (the id FIFO is decoder-
// integration bookkeeping the task never observes, so synthesis sweeps it).
// The RTLScout-marked structural fixes inside jpeg_idct_ram and transpose are:
// the block pointers are pinned to zero, reducing the four-block-deep input
// buffer to the single block per-case metering can use; and the shared
// read pointers in jpeg_idct_ram and jpeg_idct_transpose are replicated
// per RAM bank (whole-counter replicas; the shared registers' clk->Q fanout
// into the flop-mapped RAM read muxes measured 2.3-2.6 ns, an artifact the
// flow cannot buffer away). Pristine upstream files: the upstream repository (THIRD_PARTY_LICENSES.md).
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

module jpeg_idct
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 15:0]  inport_data_i
    ,input  [  5:0]  inport_idx_i
    ,input           inport_eob_i
    ,input  [ 31:0]  inport_id_i
    ,input           outport_accept_i

    // Outputs
    ,output          inport_accept_o
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data_o
    ,output [  5:0]  outport_idx_o
    ,output [ 31:0]  outport_id_o
);




wire          input_valid_w;
wire [ 15:0]  input_data0_w;
wire [ 15:0]  input_data1_w;
wire [ 15:0]  input_data2_w;
wire [ 15:0]  input_data3_w;
wire [  2:0]  input_idx_w;
wire          input_ready_w;


jpeg_idct_ram
u_input
(
     .clk_i(clk_i)
    ,.rst_i(rst_i)

    ,.img_start_i(img_start_i)
    ,.img_end_i(img_end_i)

    ,.inport_valid_i(inport_valid_i)
    ,.inport_data_i(inport_data_i)
    ,.inport_idx_i(inport_idx_i)
    ,.inport_eob_i(inport_eob_i)
    ,.inport_accept_o(inport_accept_o)

    ,.outport_valid_o(input_valid_w)
    ,.outport_data0_o(input_data0_w)
    ,.outport_data1_o(input_data1_w)
    ,.outport_data2_o(input_data2_w)
    ,.outport_data3_o(input_data3_w)
    ,.outport_idx_o(input_idx_w)
    ,.outport_ready_i(outport_accept_i)
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

    ,.inport_valid_i(input_valid_w)
    ,.inport_data0_i(input_data0_w)
    ,.inport_data1_i(input_data1_w)
    ,.inport_data2_i(input_data2_w)
    ,.inport_data3_i(input_data3_w)
    ,.inport_idx_i(input_idx_w)

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


jpeg_idct_fifo
#(
     .WIDTH(32)
    ,.DEPTH(8)
    ,.ADDR_W(3)
)
u_id_fifo
(
     .clk_i(clk_i)
    ,.rst_i(rst_i)

    ,.flush_i(img_start_i)

    ,.push_i(inport_eob_i)
    ,.data_in_i(inport_id_i)
    ,.accept_o()

    ,.valid_o()
    ,.data_out_o(outport_id_o)
    ,.pop_i(outport_valid_o && outport_idx_o == 6'd63)
);

`ifdef verilator
function get_valid; /*verilator public*/
begin
    get_valid = outport_valid_o;
end
endfunction
function [5:0] get_sample_idx; /*verilator public*/
begin
    get_sample_idx = outport_idx_o;
end
endfunction
function [31:0] get_sample; /*verilator public*/
begin
    get_sample = outport_data_o;
end
endfunction
`endif


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

module jpeg_idct_ram
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 15:0]  inport_data_i
    ,input  [  5:0]  inport_idx_i
    ,input           inport_eob_i
    ,input           outport_ready_i

    // Outputs
    ,output          inport_accept_o
    ,output          outport_valid_o
    ,output [ 15:0]  outport_data0_o
    ,output [ 15:0]  outport_data1_o
    ,output [ 15:0]  outport_data2_o
    ,output [ 15:0]  outport_data3_o
    ,output [  2:0]  outport_idx_o
);



reg [1:0]   block_wr_q;
reg [1:0]   block_rd_q;
reg [5:0]   rd_idx_q;
reg [3:0]   rd_addr_q;

wire [5:0]  wr_ptr_w = {block_wr_q, inport_idx_i[5:3], inport_idx_i[0]};

wire [15:0] outport_data0_w;
wire [15:0] outport_data1_w;
wire [15:0] outport_data2_w;
wire [15:0] outport_data3_w;

wire wr0_w = inport_valid_i && inport_accept_o && (inport_idx_i[2:0] == 3'd0 || inport_idx_i[2:0] == 3'd1);
wire wr1_w = inport_valid_i && inport_accept_o && (inport_idx_i[2:0] == 3'd2 || inport_idx_i[2:0] == 3'd3);
wire wr2_w = inport_valid_i && inport_accept_o && (inport_idx_i[2:0] == 3'd4 || inport_idx_i[2:0] == 3'd5);
wire wr3_w = inport_valid_i && inport_accept_o && (inport_idx_i[2:0] == 3'd6 || inport_idx_i[2:0] == 3'd7);

jpeg_idct_ram_dp
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

    ,.addr1_i({block_rd_q, rtls_rd_addr_rep_q[0]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(16'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data0_w)
);

jpeg_idct_ram_dp
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

    ,.addr1_i({block_rd_q, rtls_rd_addr_rep_q[1]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(16'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data1_w)
);

jpeg_idct_ram_dp
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

    ,.addr1_i({block_rd_q, rtls_rd_addr_rep_q[2]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(16'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data2_w)
);

jpeg_idct_ram_dp
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

    ,.addr1_i({block_rd_q, rtls_rd_addr_rep_q[3]}) // RTLScout: replicated read pointer (see fix note)
    ,.data1_i(16'b0)
    ,.wr1_i(1'b0)
    ,.data1_o(outport_data3_w)
);

//-----------------------------------------------------------------
// Data Qualifiers
//-----------------------------------------------------------------
reg [63:0]        data_valid0_r;
reg [63:0]        data_valid0_q;

always @ *
begin
    data_valid0_r = data_valid0_q;

    // End of block read out - reset data valid state
    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        case (block_rd_q)
        2'd0:    data_valid0_r[15:0]  = 16'b0;
        2'd1:    data_valid0_r[31:16] = 16'b0;
        2'd2:    data_valid0_r[47:32] = 16'b0;
        default: data_valid0_r[63:48] = 16'b0;
        endcase
    end

    if (wr0_w)
        data_valid0_r[wr_ptr_w] = 1'b1;
end

always @ (posedge clk_i )
if (rst_i)
    data_valid0_q <= 64'b0;
else if (img_start_i)
    data_valid0_q <= 64'b0;
else
    data_valid0_q <= data_valid0_r;

reg [63:0]        data_valid1_r;
reg [63:0]        data_valid1_q;

always @ *
begin
    data_valid1_r = data_valid1_q;

    // End of block read out - reset data valid state
    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        case (block_rd_q)
        2'd0:    data_valid1_r[15:0]  = 16'b0;
        2'd1:    data_valid1_r[31:16] = 16'b0;
        2'd2:    data_valid1_r[47:32] = 16'b0;
        default: data_valid1_r[63:48] = 16'b0;
        endcase
    end

    if (wr1_w)
        data_valid1_r[wr_ptr_w] = 1'b1;
end

always @ (posedge clk_i )
if (rst_i)
    data_valid1_q <= 64'b0;
else if (img_start_i)
    data_valid1_q <= 64'b0;
else
    data_valid1_q <= data_valid1_r;

reg [63:0]        data_valid2_r;
reg [63:0]        data_valid2_q;

always @ *
begin
    data_valid2_r = data_valid2_q;

    // End of block read out - reset data valid state
    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        case (block_rd_q)
        2'd0:    data_valid2_r[15:0]  = 16'b0;
        2'd1:    data_valid2_r[31:16] = 16'b0;
        2'd2:    data_valid2_r[47:32] = 16'b0;
        default: data_valid2_r[63:48] = 16'b0;
        endcase
    end

    if (wr2_w)
        data_valid2_r[wr_ptr_w] = 1'b1;
end

always @ (posedge clk_i )
if (rst_i)
    data_valid2_q <= 64'b0;
else if (img_start_i)
    data_valid2_q <= 64'b0;
else
    data_valid2_q <= data_valid2_r;

reg [63:0]        data_valid3_r;
reg [63:0]        data_valid3_q;

always @ *
begin
    data_valid3_r = data_valid3_q;

    // End of block read out - reset data valid state
    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        case (block_rd_q)
        2'd0:    data_valid3_r[15:0]  = 16'b0;
        2'd1:    data_valid3_r[31:16] = 16'b0;
        2'd2:    data_valid3_r[47:32] = 16'b0;
        default: data_valid3_r[63:48] = 16'b0;
        endcase
    end

    if (wr3_w)
        data_valid3_r[wr_ptr_w] = 1'b1;
end

always @ (posedge clk_i )
if (rst_i)
    data_valid3_q <= 64'b0;
else if (img_start_i)
    data_valid3_q <= 64'b0;
else
    data_valid3_q <= data_valid3_r;


//-----------------------------------------------------------------
// Input Buffer
//-----------------------------------------------------------------
reg [3:0] block_ready_q;

always @ (posedge clk_i )
if (rst_i)
begin
    block_ready_q     <= 4'b0;
    block_wr_q        <= 2'b0;
    block_rd_q        <= 2'b0;
end
else if (img_start_i)
begin
    block_ready_q     <= 4'b0;
    block_wr_q        <= 2'b0;
    block_rd_q        <= 2'b0;
end
else
begin
    if (inport_eob_i && inport_accept_o)
    begin
        block_ready_q[block_wr_q] <= 1'b1;
        // RTLScout structural fix (fairness; see file header): block pointer
        // PINNED to zero -- reduces the four-block-deep input buffer to the
        // single block the study's per-case metering can ever use (cross-job
        // overlap is disabled for every design; the extra slots are never
        // written in any measured cycle, so synthesis sweeps them). Original:
        // block_wr_q                <= block_wr_q + 2'd1;
    end

    if (outport_valid_o && rd_idx_q[5:0] == 6'd63)
    begin
        block_ready_q[block_rd_q] <= 1'b0;
        // RTLScout structural fix: read-side block pointer pinned (see above).
        // Original:
        // block_rd_q                <= block_rd_q + 2'd1;
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
    rd_addr_q <= 4'd1;
else if (state_q == STATE_ACTIVE)
begin
    case (rd_idx_q[2:0])
    3'd0: rd_addr_q <= rd_addr_q - 1;
    3'd1: rd_addr_q <= rd_addr_q + 1;
    3'd2: ;
    3'd3: rd_addr_q <= rd_addr_q - 1;
    3'd4: rd_addr_q <= rd_addr_q + 1;
    3'd5: rd_addr_q <= rd_addr_q - 1;
    3'd6: rd_addr_q <= rd_addr_q + 2;
    3'd7: rd_addr_q <= rd_addr_q + 1;
    endcase
end

reg data_val0_q;
reg data_val1_q;
reg data_val2_q;
reg data_val3_q;

always @ (posedge clk_i )
if (rst_i)
begin
    data_val0_q <= 1'b0;
    data_val1_q <= 1'b0;
    data_val2_q <= 1'b0;
    data_val3_q <= 1'b0;
end
else
begin
    data_val0_q <= data_valid0_q[{block_rd_q, rtls_rd_addr_rep_q[0]}]; // RTLScout: replicated read pointer
    data_val1_q <= data_valid1_q[{block_rd_q, rtls_rd_addr_rep_q[1]}]; // RTLScout: replicated read pointer
    data_val2_q <= data_valid2_q[{block_rd_q, rtls_rd_addr_rep_q[2]}]; // RTLScout: replicated read pointer
    data_val3_q <= data_valid3_q[{block_rd_q, rtls_rd_addr_rep_q[3]}]; // RTLScout: replicated read pointer
end

// RTLScout structural fix (fairness): same pointer-fanout artifact and
// remedy as in jpeg_idct_transpose (see the fix note there) -- rd_addr_q
// fed the read address of all four flop-mapped banks and the per-word
// valid muxes; replicated per bank as whole counters mirroring the
// original update block above. block_rd_q stays the (pinned) original.
reg [3:0] rtls_rd_addr_rep_q [0:3];
integer   rtls_k;
always @ (posedge clk_i )
for (rtls_k = 0; rtls_k < 4; rtls_k = rtls_k + 1)
begin
    if (rst_i)
        rtls_rd_addr_rep_q[rtls_k] <= 4'b0;
    else if (state_q == STATE_IDLE)
        rtls_rd_addr_rep_q[rtls_k] <= 4'b0;
    else if (state_q == STATE_SETUP)
        rtls_rd_addr_rep_q[rtls_k] <= 4'd1;
    else if (state_q == STATE_ACTIVE)
    begin
        case (rd_idx_q[2:0])
        3'd0: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 1;
        3'd1: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 1;
        3'd2: ;
        3'd3: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 1;
        3'd4: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 1;
        3'd5: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] - 1;
        3'd6: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 2;
        3'd7: rtls_rd_addr_rep_q[rtls_k] <= rtls_rd_addr_rep_q[rtls_k] + 1;
        endcase
    end
end

assign outport_valid_o = (state_q == STATE_ACTIVE);
assign outport_idx_o   = rd_idx_q[2:0];
assign outport_data0_o = {16{data_val0_q}} & outport_data0_w;
assign outport_data1_o = {16{data_val1_q}} & outport_data1_w;
assign outport_data2_o = {16{data_val2_q}} & outport_data2_w;
assign outport_data3_o = {16{data_val3_q}} & outport_data3_w;


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

module jpeg_idct_ram_dp
(
    // Inputs
     input           clk0_i
    ,input           rst0_i
    ,input  [  5:0]  addr0_i
    ,input  [ 15:0]  data0_i
    ,input           wr0_i
    ,input           clk1_i
    ,input           rst1_i
    ,input  [  5:0]  addr1_i
    ,input  [ 15:0]  data1_i
    ,input           wr1_i

    // Outputs
    ,output [ 15:0]  data0_o
    ,output [ 15:0]  data1_o
);



//-----------------------------------------------------------------
// Dual Port RAM
// Mode: Read First
//-----------------------------------------------------------------
/* verilator lint_off MULTIDRIVEN */
reg [15:0]   ram [63:0] /*verilator public*/;
/* verilator lint_on MULTIDRIVEN */

reg [15:0] ram_read0_q;
reg [15:0] ram_read1_q;


// Synchronous write
always @ (posedge clk0_i)
begin
    if (wr0_i)
        ram[addr0_i][15:0] <= data0_i[15:0];

    ram_read0_q <= ram[addr0_i];
end

always @ (posedge clk1_i)
begin
    if (wr1_i)
        ram[addr1_i][15:0] <= data1_i[15:0];

    ram_read1_q <= ram[addr1_i];
end


assign data0_o = ram_read0_q;
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

module jpeg_idct_fifo
//-----------------------------------------------------------------
// Params
//-----------------------------------------------------------------
#(
     parameter WIDTH            = 8
    ,parameter DEPTH            = 4
    ,parameter ADDR_W           = 2
)
//-----------------------------------------------------------------
// Ports
//-----------------------------------------------------------------
(
    // Inputs
     input           clk_i
    ,input           rst_i
    ,input  [WIDTH-1:0]  data_in_i
    ,input           push_i
    ,input           pop_i
    ,input           flush_i

    // Outputs
    ,output [WIDTH-1:0]  data_out_o
    ,output          accept_o
    ,output          valid_o
);



//-----------------------------------------------------------------
// Local Params
//-----------------------------------------------------------------
localparam COUNT_W = ADDR_W + 1;

//-----------------------------------------------------------------
// Registers
//-----------------------------------------------------------------
reg [WIDTH-1:0]   ram_q[DEPTH-1:0];
reg [ADDR_W-1:0]  rd_ptr_q;
reg [ADDR_W-1:0]  wr_ptr_q;
reg [COUNT_W-1:0] count_q;

//-----------------------------------------------------------------
// Sequential
//-----------------------------------------------------------------
always @ (posedge clk_i or posedge rst_i)
if (rst_i)
begin
    count_q   <= {(COUNT_W) {1'b0}};
    rd_ptr_q  <= {(ADDR_W) {1'b0}};
    wr_ptr_q  <= {(ADDR_W) {1'b0}};
end
else if (flush_i)
begin
    count_q   <= {(COUNT_W) {1'b0}};
    rd_ptr_q  <= {(ADDR_W) {1'b0}};
    wr_ptr_q  <= {(ADDR_W) {1'b0}};
end
else
begin
    // Push
    if (push_i & accept_o)
    begin
        ram_q[wr_ptr_q] <= data_in_i;
        wr_ptr_q        <= wr_ptr_q + 1;
    end

    // Pop
    if (pop_i & valid_o)
        rd_ptr_q      <= rd_ptr_q + 1;

    // Count up
    if ((push_i & accept_o) & ~(pop_i & valid_o))
        count_q <= count_q + 1;
    // Count down
    else if (~(push_i & accept_o) & (pop_i & valid_o))
        count_q <= count_q - 1;
end

//-------------------------------------------------------------------
// Combinatorial
//-------------------------------------------------------------------
/* verilator lint_off WIDTH */
assign valid_o       = (count_q != 0);
assign accept_o      = (count_q != DEPTH);
/* verilator lint_on WIDTH */

assign data_out_o    = ram_q[rd_ptr_q];



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



//-----------------------------------------------------------------
// Dual Port RAM
// Mode: Read First
//-----------------------------------------------------------------
/* verilator lint_off MULTIDRIVEN */
reg [31:0]   ram [31:0] /*verilator public*/;
/* verilator lint_on MULTIDRIVEN */

reg [31:0] ram_read0_q;
reg [31:0] ram_read1_q;


// Synchronous write
always @ (posedge clk0_i)
begin
    if (wr0_i)
        ram[addr0_i][31:0] <= data0_i[31:0];

    ram_read0_q <= ram[addr0_i];
end

always @ (posedge clk1_i)
begin
    if (wr1_i)
        ram[addr1_i][31:0] <= data1_i[31:0];

    ram_read1_q <= ram[addr1_i];
end


assign data0_o = ram_read0_q;
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


//-----------------------------------------------------------------
// RTLScout pass-through wrapper (study-written; the ONLY non-upstream
// code in this file). See header note on the tied-off id ports.
//-----------------------------------------------------------------
module jpeg_idct_2d_upstream
(
     input           clk
    ,input           rst
    ,input           img_start_i
    ,input           img_end_i
    ,input           inport_valid_i
    ,input  [ 15:0]  inport_data_i
    ,input  [  5:0]  inport_idx_i
    ,input           inport_eob_i
    ,input           outport_accept_i
    ,output          inport_accept_o
    ,output          outport_valid_o
    ,output [ 31:0]  outport_data_o
    ,output [  5:0]  outport_idx_o
);
jpeg_idct u_core
(
     .clk_i(clk)
    ,.rst_i(rst)
    ,.img_start_i(img_start_i)
    ,.img_end_i(img_end_i)
    ,.inport_valid_i(inport_valid_i)
    ,.inport_data_i(inport_data_i)
    ,.inport_idx_i(inport_idx_i)
    ,.inport_eob_i(inport_eob_i)
    ,.inport_id_i(32'd0)
    ,.outport_accept_i(outport_accept_i)
    ,.inport_accept_o(inport_accept_o)
    ,.outport_valid_o(outport_valid_o)
    ,.outport_data_o(outport_data_o)
    ,.outport_idx_o(outport_idx_o)
    ,.outport_id_o()
);
endmodule
