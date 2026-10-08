// RTLScout provenance note (ofdm32_demod_1r1w_verilog): Verilog twin of the
// task-level study's Spire ofdm32_demod_1r1w recast. The compute core below
// (`module ofdm_rx` + `fft` and its submodules) is the upstream logikbench RX
// chain exactly as shipped in ofdm32_demod_upstream/context/starting_point.v,
// i.e. with the same two RTLScout-marked one-line localparam corrections for
// N=32 as the upstream comparison measurement (see that file's header). Only the
// `ofdm32_demod_core` wrapper FSM is ours: it reads mod and the 80 sample words
// back to back and presents one complex sample to the core every second cycle
// (the core's collect state tolerates gaps), then stores the 12 bit words.
module ofdm32_demod_core (
  input  wire        clk,
  input  wire        rst,        // active-high
  output reg         rd_en,
  output reg  [6:0]  rd_addr,    // 0 = mod, 1+2s = I, 2+2s = Q of sample s
  input  wire [15:0] rd_data,    // valid one cycle after rd_en
  output reg         wr_en,
  output reg  [3:0]  wr_addr,    // word k = out_bits[16k +: 16]
  output reg  [15:0] wr_data,
  output reg         done        // sticky
);
  localparam integer DW = 16, N = 32, CP = 8, NS = N + CP, NIN = 1 + 2*NS, NOUT = 12;
  localparam [1:0] S_LOAD = 2'd0, S_WAIT = 2'd1, S_STORE = 2'd2, S_DONE = 2'd3;

  reg [1:0]        state;
  reg [6:0]        cnt;
  reg              rd_pend;
  reg [6:0]        rd_pend_addr;
  reg [1:0]        mod_r;
  reg signed [DW-1:0] i_hold;
  reg              in_valid, in_last;
  reg signed [DW-1:0] in_r, in_i;
  wire             out_valid;
  wire [6*N-1:0]   out_bits;

  ofdm_rx #(.DW(DW), .N(N), .CP(CP)) u_rx (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_r(in_r), .in_i(in_i), .mod(mod_r),
    .out_valid(out_valid), .out_bits(out_bits));

  always @(posedge clk) begin
    if (rst) begin
      state <= S_LOAD; cnt <= 7'd0;
      rd_en <= 1'b0; rd_addr <= 7'd0; rd_pend <= 1'b0; rd_pend_addr <= 7'd0;
      wr_en <= 1'b0; wr_addr <= 4'd0; wr_data <= 16'd0;
      mod_r <= 2'd0; i_hold <= 16'sd0; in_valid <= 1'b0; in_last <= 1'b0;
      in_r <= 16'sd0; in_i <= 16'sd0; done <= 1'b0;
    end else begin
      rd_en <= 1'b0; wr_en <= 1'b0; in_valid <= 1'b0; in_last <= 1'b0;
      rd_pend      <= rd_en;
      rd_pend_addr <= rd_addr;
      if (rd_pend) begin
        if (rd_pend_addr == 7'd0)      mod_r  <= rd_data[1:0];
        else if (rd_pend_addr[0])      i_hold <= rd_data;             // I of sample s
        else begin                                                    // Q: present the sample
          in_r <= i_hold; in_i <= rd_data; in_valid <= 1'b1;
          in_last <= (rd_pend_addr == NIN - 1);
        end
      end
      case (state)
        S_LOAD: begin
          if (cnt < NIN) begin
            rd_en <= 1'b1; rd_addr <= cnt; cnt <= cnt + 7'd1;
          end else if (!rd_en && !rd_pend) begin
            cnt <= 7'd0; state <= S_WAIT;
          end
        end
        S_WAIT: if (out_valid) state <= S_STORE;   // out_bits stays stable
        S_STORE: begin
          wr_en <= 1'b1; wr_addr <= cnt[3:0]; wr_data <= out_bits[cnt[3:0]*16 +: 16];
          cnt <= cnt + 7'd1;
          if (cnt == NOUT - 1) state <= S_DONE;
        end
        S_DONE: done <= 1'b1;
      endcase
    end
  end
endmodule

// ---------------------------------------------------------------------------
// Upstream core: copy of ofdm32_demod_upstream/context/starting_point.v
// ---------------------------------------------------------------------------
// ofdm32_demod_upstream starting point: the VERBATIM logikbench upstream RX
// (ofdm_rx.v + fft.v concatenated, content otherwise byte-identical),
// instantiated at N=32/CP=8. The ONLY edits are the two RTLScout-marked
// one-line localparam corrections in ofdm_rx (LAT, LOG2N), which are
// hardcoded N=64 values upstream and wrong at N=32; the study's golden
// vectors were captured from exactly this parameter-corrected RTL.
//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
//
// OFDM receiver: streamed time samples (CP first) -> CP removal -> FFT
// (reuses fft) -> 1/N rescale -> QAM demap -> bits. Consumes N+CP samples per
// symbol (in_valid/in_last); mod selects the constellation. See ofdm.v.
//
//#############################################################################

module ofdm_rx
  #(parameter DW = 16,
    parameter N = 64,
    parameter CP = 16)
   (
    input		  clk,
    input		  rst,
    input		  in_valid, // time-sample stream valid
    input		  in_last,  // last sample of the symbol
    input signed [DW-1:0] in_r,
    input signed [DW-1:0] in_i,
    input [1:0]		  mod,
    output reg		  out_valid,
    output reg [6*N-1:0]  out_bits
    );

   localparam LOG2N = $clog2(N);              // RTLScout fix: was hardcoded 6 (N=64); scrambles the bit-reversed collection at N=32
   localparam LAT   = 37;                       // RTLScout fix: fft latency at N=32, measured by impulse (upstream hardcoded 70, the N=64 value)
   localparam signed [DW-1:0] AMP = 16'sd2048;  // base PAM level
   localparam		      S_IDLE = 0, S_COLLECT = 1, S_RX = 2, S_OUT = 3;
   localparam		      SCW = $clog2(N+CP);

   reg signed [DW-1:0]	      rr [0:N-1];   // received time samples (CP removed)
   reg signed [DW-1:0]	      ri [0:N-1];
   reg signed [DW-1:0]	      xr [0:N-1];   // recovered freq bins
   reg signed [DW-1:0]	      xi [0:N-1];
   reg [1:0]		      mod_r;
   reg [2:0]		      state;
   reg [9:0]		      cnt;
   integer		      i;

   wire			      fft_rst;
   reg			      rxiv;
   reg signed [DW-1:0]	      rxir, rxii;
   wire			      rxov;
   wire signed [DW-1:0]	      rxor, rxoi;
   wire [LOG2N-1:0]	      m;

   // Per-axis QAM demap (replaces the pam_demap function): one combinational
   // unit per subcarrier built with generate. The rescaled level is offset and
   // shifted to a level index, clamped to [0,lim], then binary-to-gray encoded
   // and masked to B=mod_r+1 bits (lim = 1,3,7 for QPSK/16-QAM/64-QAM).
   wire [2:0]		      dlim = (4'd1 << (mod_r + 2'd1)) - 4'd1;  // 1,3,7
   wire [3:0]		      d2b  = (4'd1 << (mod_r + 2'd1));          // 2,4,8
   wire signed [DW+1:0]	      doff = $signed({2'b0, d2b}) * AMP;   // (1<<B)*AMP
   wire [2:0]		      dem_r [0:N-1];
   wire [2:0]		      dem_i [0:N-1];
   // bit-reversed fft output index (replaces the bitrev function)
   wire [LOG2N-1:0]	      m_rev;

   assign fft_rst = rst | (state == S_IDLE) | (state == S_COLLECT);
   assign m = cnt - LAT;

   genvar gp, gb;
   generate
      for (gb = 0; gb < LOG2N; gb = gb + 1) begin : g_brev
	 assign m_rev[gb] = m[LOG2N-1-gb];
      end
      for (gp = 0; gp < N; gp = gp + 1) begin : g_dem
	 // real axis: rescale, offset, shift to index, clamp, binary->gray
	 wire signed [DW-1:0]  lr   = xr[gp] <<< LOG2N;
	 wire signed [DW+1:0]  sr   = lr + doff;
	 wire signed [DW+1:0]  shr  = sr >>> 12;
	 wire [2:0]	       idr  = (shr < 0) ? 3'd0 :
			       (shr > $signed({1'b0, dlim})) ? dlim :
			       shr[2:0];
	 assign dem_r[gp][2] = idr[2]            & dlim[2];
	 assign dem_r[gp][1] = (idr[1] ^ idr[2]) & dlim[1];
	 assign dem_r[gp][0] = (idr[0] ^ idr[1]) & dlim[0];
	 // imag axis
	 wire signed [DW-1:0]  li   = xi[gp] <<< LOG2N;
	 wire signed [DW+1:0]  si   = li + doff;
	 wire signed [DW+1:0]  shi  = si >>> 12;
	 wire [2:0]	       idi  = (shi < 0) ? 3'd0 :
			       (shi > $signed({1'b0, dlim})) ? dlim :
			       shi[2:0];
	 assign dem_i[gp][2] = idi[2]            & dlim[2];
	 assign dem_i[gp][1] = (idi[1] ^ idi[2]) & dlim[1];
	 assign dem_i[gp][0] = (idi[0] ^ idi[1]) & dlim[0];
      end
   endgenerate

   fft #(.DW(DW), .N(N)) u_fft
     (.clk      (clk),
      .rst      (fft_rst),
      .in_valid (rxiv),
      .in_real  (rxir),
      .in_imag  (rxii),
      .out_valid(rxov),
      .out_real (rxor),
      .out_imag (rxoi));

   always @(posedge clk) begin
      if (rst) begin
         state     <= S_IDLE;
         cnt       <= 0;
         out_valid <= 1'b0;
         rxiv      <= 1'b0;
      end
      else begin
         out_valid <= 1'b0;
         case (state)
           S_IDLE: begin
              rxiv <= 1'b0;
              cnt  <= 0;
              if (in_valid) begin
                 mod_r <= mod;
                 state <= S_COLLECT;
                 // first sample is CP[0]; just consume (dropped)
                 cnt   <= 1;
              end
           end
           S_COLLECT: begin
              if (in_valid) begin
                 if (cnt >= CP) begin
                    rr[cnt-CP] <= in_r;
                    ri[cnt-CP] <= in_i;
                 end
                 if (in_last) begin
                    cnt   <= 0;
                    state <= S_RX;
                 end
                 else cnt <= cnt + 1'b1;
              end
           end
           S_RX: begin
              rxiv <= 1'b1;
              rxir <= (cnt < N) ? rr[cnt[LOG2N-1:0]] : 16'sd0;
              rxii <= (cnt < N) ? ri[cnt[LOG2N-1:0]] : 16'sd0;
              if (cnt >= LAT) begin
                 xr[m_rev] <= rxor;
                 xi[m_rev] <= rxoi;
              end
              if (cnt == LAT + N - 1) begin
                 rxiv  <= 1'b0;
                 state <= S_OUT;
              end
              else cnt <= cnt + 1'b1;
           end
           S_OUT: begin
              for (i = 0; i < N; i = i + 1) begin
                 out_bits[i*6   +: 3] <= dem_r[i];
                 out_bits[i*6+3 +: 3] <= dem_i[i];
              end
              out_valid <= 1'b1;
              state     <= S_IDLE;
           end
         endcase
      end
   end

endmodule

//#############################################################################
// Copyright: Zero ASIC. All rights Reserved.
// Author: Andreas Olofsson
// License:  MIT (see LICENSE file in LogikBench repository)
//#############################################################################
//
// Parameterizable streaming FFT accelerator.
//
// Architecture: radix-2 decimation-in-frequency (DIF) single-path delay
// feedback (R2SDF). LOG2N pipeline stages, one complex sample in per clock,
// one complex sample out per clock after the pipeline fills. Each stage has a
// butterfly, a feedback delay line (depth N/2^(stage+1)), and a complex
// twiddle multiplier fed by a shared sine ROM.
//
// Scaling: every stage scales its butterfly results by 1/2, so the output is
// the true FFT divided by N (block-scaling that keeps Q1.15 from overflowing).
//
// Output order: natural R2SDF order, i.e. BIT-REVERSED frequency bins.
//
// Data format: Q1.15 signed fixed-point, real and imaginary.
//
// NOTE: This file is generated by genfft.py (twiddle ROM). N must be a power
// of two, 2 <= N <= 256 (the sine ROM has 256 entries).
//
//#############################################################################

module fft
  #(parameter DW = 16, // data width (Q1.15)
    parameter N = 64   // FFT size (power of two, <= 256)
    )
   (
    input		   clk,
    input		   rst,	// synchronous, active high
    input		   in_valid,
    input signed [DW-1:0]  in_real,
    input signed [DW-1:0]  in_imag,
    output		   out_valid,
    output signed [DW-1:0] out_real,
    output signed [DW-1:0] out_imag
    );

   localparam LOG2N = $clog2(N);

   // Per-stage streaming buses (LOG2N+1 boundaries: [0]=input .. [LOG2N]=out)
   wire signed [DW-1:0]	sr [0:LOG2N];
   wire signed [DW-1:0]	si [0:LOG2N];
   wire			sv [0:LOG2N];

   assign sr[0] = in_real;
   assign si[0] = in_imag;
   assign sv[0] = in_valid;

   genvar s;
   generate
      for (s = 0; s < LOG2N; s = s + 1) begin : g_stage
         fft_bf2sdf #(.DW   (DW),
                      .NP   (N),
                      .LOG2N(LOG2N),
                      .STAGE(s))
         u_stage (.clk      (clk),
                  .rst      (rst),
                  .in_valid (sv[s]),
                  .in_real  (sr[s]),
                  .in_imag  (si[s]),
                  .out_valid(sv[s+1]),
                  .out_real (sr[s+1]),
                  .out_imag (si[s+1]));
      end
   endgenerate

   assign out_real  = sr[LOG2N];
   assign out_imag  = si[LOG2N];
   assign out_valid = sv[LOG2N];

endmodule

//#############################################################################
// One R2SDF stage: butterfly + feedback delay line + twiddle multiply
//#############################################################################
module fft_bf2sdf
  #(parameter DW = 16,
    parameter NP = 64, // full FFT size
    parameter LOG2N = 6,
    parameter STAGE = 0
    )
   (
    input		       clk,
    input		       rst,
    input		       in_valid,
    input signed [DW-1:0]      in_real,
    input signed [DW-1:0]      in_imag,
    output reg		       out_valid,
    output reg signed [DW-1:0] out_real,
    output reg signed [DW-1:0] out_imag
    );

   // Feedback delay depth for this stage = NP / 2^(STAGE+1)
   localparam FBLOG = LOG2N - 1 - STAGE;   // log2(depth); MSB of period
   localparam FBDEPTH = (1 << FBLOG);
   // 256-entry ROM step so a twiddle index in [0,NP) maps into [0,256)
   localparam SHIFT256 = 8 - LOG2N;

   // sample counter (position within the N-point frame at this stage)
   reg [LOG2N-1:0] cnt;
   wire		   cb = cnt[FBLOG];               // 0=load,1=bfly
   wire [LOG2N-1:0] p = cnt & ((1 << FBLOG) - 1); // index within half

   // feedback shift register (complex), depth FBDEPTH
   reg signed [DW-1:0] fb_r [0:FBDEPTH-1];
   reg signed [DW-1:0] fb_i [0:FBDEPTH-1];
   wire signed [DW-1:0]	fbo_r = fb_r[FBDEPTH-1];
   wire signed [DW-1:0]	fbo_i = fb_i[FBDEPTH-1];

   integer		i;

   // butterfly (scaled by 1/2)
   wire signed [DW:0]	sum_r = fbo_r + in_real;    // one extra bit
   wire signed [DW:0]	sum_i = fbo_i + in_imag;
   wire signed [DW:0]	dif_r = fbo_r - in_real;
   wire signed [DW:0]	dif_i = fbo_i - in_imag;
   wire signed [DW-1:0]	sum_r2 = sum_r >>> 1;
   wire signed [DW-1:0]	sum_i2 = sum_i >>> 1;
   wire signed [DW-1:0]	dif_r2 = dif_r >>> 1;
   wire signed [DW-1:0]	dif_i2 = dif_i >>> 1;

   // twiddle for the difference path: W_N^(p<<STAGE)
   wire [7:0]		addr = (p << (STAGE + SHIFT256));
   wire signed [15:0]	cosv, sinv;
   fft_sinrom u_cos (.idx(addr + 8'd64), .val(cosv));   // cos = sin(x+90deg)
   fft_sinrom u_sin (.idx(addr),         .val(sinv));
   wire signed [DW-1:0] wr =  cosv;
   wire signed [DW-1:0]	wi = -sinv;
   // complex multiply (dif2 * W), Q1.15
   wire signed [2*DW-1:0] m_rr = dif_r2 * wr;
   wire signed [2*DW-1:0] m_ii = dif_i2 * wi;
   wire signed [2*DW-1:0] m_ri = dif_r2 * wi;
   wire signed [2*DW-1:0] m_ir = dif_i2 * wr;
   wire signed [DW-1:0]	  tw_r = (m_rr - m_ii) >>> 15;
   wire signed [DW-1:0]	  tw_i = (m_ri + m_ir) >>> 15;

   // next value written to the delay line
   wire signed [DW-1:0]	  nd_r = cb ? tw_r : in_real;
   wire signed [DW-1:0]	  nd_i = cb ? tw_i : in_imag;

   always @(posedge clk) begin
      if (rst) begin
         cnt       <= 0;
         out_valid <= 1'b0;
         for (i = 0; i < FBDEPTH; i = i + 1) begin
            fb_r[i] <= 0;
            fb_i[i] <= 0;
         end
         out_real <= 0;
         out_imag <= 0;
      end
      else begin
         out_valid <= in_valid;
         if (in_valid) begin
            cnt <= cnt + 1'b1;
            // shift the feedback line, inject nd at the head
            for (i = FBDEPTH-1; i > 0; i = i - 1) begin
               fb_r[i] <= fb_r[i-1];
               fb_i[i] <= fb_i[i-1];
            end
            fb_r[0] <= nd_r;
            fb_i[0] <= nd_i;
            // stage output
            if (cb) begin
               out_real <= sum_r2;
               out_imag <= sum_i2;
            end
            else begin
               out_real <= fbo_r;
               out_imag <= fbo_i;
            end
         end
      end
   end

endmodule

//#############################################################################
// Sine ROM module: val = round(sin(2*pi*idx/256)*32767). Combinational case
// (no function, no initial block); instantiated for the sin and cos lookups.
//#############################################################################
module fft_sinrom
  (
   input [7:0]		    idx,
   output reg signed [15:0] val
   );
   always @* begin
      case (idx)
        8'd0: val = 16'sd0;
        8'd1: val = 16'sd804;
        8'd2: val = 16'sd1608;
        8'd3: val = 16'sd2410;
        8'd4: val = 16'sd3212;
        8'd5: val = 16'sd4011;
        8'd6: val = 16'sd4808;
        8'd7: val = 16'sd5602;
        8'd8: val = 16'sd6393;
        8'd9: val = 16'sd7179;
        8'd10: val = 16'sd7962;
        8'd11: val = 16'sd8739;
        8'd12: val = 16'sd9512;
        8'd13: val = 16'sd10278;
        8'd14: val = 16'sd11039;
        8'd15: val = 16'sd11793;
        8'd16: val = 16'sd12539;
        8'd17: val = 16'sd13279;
        8'd18: val = 16'sd14010;
        8'd19: val = 16'sd14732;
        8'd20: val = 16'sd15446;
        8'd21: val = 16'sd16151;
        8'd22: val = 16'sd16846;
        8'd23: val = 16'sd17530;
        8'd24: val = 16'sd18204;
        8'd25: val = 16'sd18868;
        8'd26: val = 16'sd19519;
        8'd27: val = 16'sd20159;
        8'd28: val = 16'sd20787;
        8'd29: val = 16'sd21403;
        8'd30: val = 16'sd22005;
        8'd31: val = 16'sd22594;
        8'd32: val = 16'sd23170;
        8'd33: val = 16'sd23731;
        8'd34: val = 16'sd24279;
        8'd35: val = 16'sd24811;
        8'd36: val = 16'sd25329;
        8'd37: val = 16'sd25832;
        8'd38: val = 16'sd26319;
        8'd39: val = 16'sd26790;
        8'd40: val = 16'sd27245;
        8'd41: val = 16'sd27683;
        8'd42: val = 16'sd28105;
        8'd43: val = 16'sd28510;
        8'd44: val = 16'sd28898;
        8'd45: val = 16'sd29268;
        8'd46: val = 16'sd29621;
        8'd47: val = 16'sd29956;
        8'd48: val = 16'sd30273;
        8'd49: val = 16'sd30571;
        8'd50: val = 16'sd30852;
        8'd51: val = 16'sd31113;
        8'd52: val = 16'sd31356;
        8'd53: val = 16'sd31580;
        8'd54: val = 16'sd31785;
        8'd55: val = 16'sd31971;
        8'd56: val = 16'sd32137;
        8'd57: val = 16'sd32285;
        8'd58: val = 16'sd32412;
        8'd59: val = 16'sd32521;
        8'd60: val = 16'sd32609;
        8'd61: val = 16'sd32678;
        8'd62: val = 16'sd32728;
        8'd63: val = 16'sd32757;
        8'd64: val = 16'sd32767;
        8'd65: val = 16'sd32757;
        8'd66: val = 16'sd32728;
        8'd67: val = 16'sd32678;
        8'd68: val = 16'sd32609;
        8'd69: val = 16'sd32521;
        8'd70: val = 16'sd32412;
        8'd71: val = 16'sd32285;
        8'd72: val = 16'sd32137;
        8'd73: val = 16'sd31971;
        8'd74: val = 16'sd31785;
        8'd75: val = 16'sd31580;
        8'd76: val = 16'sd31356;
        8'd77: val = 16'sd31113;
        8'd78: val = 16'sd30852;
        8'd79: val = 16'sd30571;
        8'd80: val = 16'sd30273;
        8'd81: val = 16'sd29956;
        8'd82: val = 16'sd29621;
        8'd83: val = 16'sd29268;
        8'd84: val = 16'sd28898;
        8'd85: val = 16'sd28510;
        8'd86: val = 16'sd28105;
        8'd87: val = 16'sd27683;
        8'd88: val = 16'sd27245;
        8'd89: val = 16'sd26790;
        8'd90: val = 16'sd26319;
        8'd91: val = 16'sd25832;
        8'd92: val = 16'sd25329;
        8'd93: val = 16'sd24811;
        8'd94: val = 16'sd24279;
        8'd95: val = 16'sd23731;
        8'd96: val = 16'sd23170;
        8'd97: val = 16'sd22594;
        8'd98: val = 16'sd22005;
        8'd99: val = 16'sd21403;
        8'd100: val = 16'sd20787;
        8'd101: val = 16'sd20159;
        8'd102: val = 16'sd19519;
        8'd103: val = 16'sd18868;
        8'd104: val = 16'sd18204;
        8'd105: val = 16'sd17530;
        8'd106: val = 16'sd16846;
        8'd107: val = 16'sd16151;
        8'd108: val = 16'sd15446;
        8'd109: val = 16'sd14732;
        8'd110: val = 16'sd14010;
        8'd111: val = 16'sd13279;
        8'd112: val = 16'sd12539;
        8'd113: val = 16'sd11793;
        8'd114: val = 16'sd11039;
        8'd115: val = 16'sd10278;
        8'd116: val = 16'sd9512;
        8'd117: val = 16'sd8739;
        8'd118: val = 16'sd7962;
        8'd119: val = 16'sd7179;
        8'd120: val = 16'sd6393;
        8'd121: val = 16'sd5602;
        8'd122: val = 16'sd4808;
        8'd123: val = 16'sd4011;
        8'd124: val = 16'sd3212;
        8'd125: val = 16'sd2410;
        8'd126: val = 16'sd1608;
        8'd127: val = 16'sd804;
        8'd128: val = 16'sd0;
        8'd129: val = -16'sd804;
        8'd130: val = -16'sd1608;
        8'd131: val = -16'sd2410;
        8'd132: val = -16'sd3212;
        8'd133: val = -16'sd4011;
        8'd134: val = -16'sd4808;
        8'd135: val = -16'sd5602;
        8'd136: val = -16'sd6393;
        8'd137: val = -16'sd7179;
        8'd138: val = -16'sd7962;
        8'd139: val = -16'sd8739;
        8'd140: val = -16'sd9512;
        8'd141: val = -16'sd10278;
        8'd142: val = -16'sd11039;
        8'd143: val = -16'sd11793;
        8'd144: val = -16'sd12539;
        8'd145: val = -16'sd13279;
        8'd146: val = -16'sd14010;
        8'd147: val = -16'sd14732;
        8'd148: val = -16'sd15446;
        8'd149: val = -16'sd16151;
        8'd150: val = -16'sd16846;
        8'd151: val = -16'sd17530;
        8'd152: val = -16'sd18204;
        8'd153: val = -16'sd18868;
        8'd154: val = -16'sd19519;
        8'd155: val = -16'sd20159;
        8'd156: val = -16'sd20787;
        8'd157: val = -16'sd21403;
        8'd158: val = -16'sd22005;
        8'd159: val = -16'sd22594;
        8'd160: val = -16'sd23170;
        8'd161: val = -16'sd23731;
        8'd162: val = -16'sd24279;
        8'd163: val = -16'sd24811;
        8'd164: val = -16'sd25329;
        8'd165: val = -16'sd25832;
        8'd166: val = -16'sd26319;
        8'd167: val = -16'sd26790;
        8'd168: val = -16'sd27245;
        8'd169: val = -16'sd27683;
        8'd170: val = -16'sd28105;
        8'd171: val = -16'sd28510;
        8'd172: val = -16'sd28898;
        8'd173: val = -16'sd29268;
        8'd174: val = -16'sd29621;
        8'd175: val = -16'sd29956;
        8'd176: val = -16'sd30273;
        8'd177: val = -16'sd30571;
        8'd178: val = -16'sd30852;
        8'd179: val = -16'sd31113;
        8'd180: val = -16'sd31356;
        8'd181: val = -16'sd31580;
        8'd182: val = -16'sd31785;
        8'd183: val = -16'sd31971;
        8'd184: val = -16'sd32137;
        8'd185: val = -16'sd32285;
        8'd186: val = -16'sd32412;
        8'd187: val = -16'sd32521;
        8'd188: val = -16'sd32609;
        8'd189: val = -16'sd32678;
        8'd190: val = -16'sd32728;
        8'd191: val = -16'sd32757;
        8'd192: val = -16'sd32767;
        8'd193: val = -16'sd32757;
        8'd194: val = -16'sd32728;
        8'd195: val = -16'sd32678;
        8'd196: val = -16'sd32609;
        8'd197: val = -16'sd32521;
        8'd198: val = -16'sd32412;
        8'd199: val = -16'sd32285;
        8'd200: val = -16'sd32137;
        8'd201: val = -16'sd31971;
        8'd202: val = -16'sd31785;
        8'd203: val = -16'sd31580;
        8'd204: val = -16'sd31356;
        8'd205: val = -16'sd31113;
        8'd206: val = -16'sd30852;
        8'd207: val = -16'sd30571;
        8'd208: val = -16'sd30273;
        8'd209: val = -16'sd29956;
        8'd210: val = -16'sd29621;
        8'd211: val = -16'sd29268;
        8'd212: val = -16'sd28898;
        8'd213: val = -16'sd28510;
        8'd214: val = -16'sd28105;
        8'd215: val = -16'sd27683;
        8'd216: val = -16'sd27245;
        8'd217: val = -16'sd26790;
        8'd218: val = -16'sd26319;
        8'd219: val = -16'sd25832;
        8'd220: val = -16'sd25329;
        8'd221: val = -16'sd24811;
        8'd222: val = -16'sd24279;
        8'd223: val = -16'sd23731;
        8'd224: val = -16'sd23170;
        8'd225: val = -16'sd22594;
        8'd226: val = -16'sd22005;
        8'd227: val = -16'sd21403;
        8'd228: val = -16'sd20787;
        8'd229: val = -16'sd20159;
        8'd230: val = -16'sd19519;
        8'd231: val = -16'sd18868;
        8'd232: val = -16'sd18204;
        8'd233: val = -16'sd17530;
        8'd234: val = -16'sd16846;
        8'd235: val = -16'sd16151;
        8'd236: val = -16'sd15446;
        8'd237: val = -16'sd14732;
        8'd238: val = -16'sd14010;
        8'd239: val = -16'sd13279;
        8'd240: val = -16'sd12539;
        8'd241: val = -16'sd11793;
        8'd242: val = -16'sd11039;
        8'd243: val = -16'sd10278;
        8'd244: val = -16'sd9512;
        8'd245: val = -16'sd8739;
        8'd246: val = -16'sd7962;
        8'd247: val = -16'sd7179;
        8'd248: val = -16'sd6393;
        8'd249: val = -16'sd5602;
        8'd250: val = -16'sd4808;
        8'd251: val = -16'sd4011;
        8'd252: val = -16'sd3212;
        8'd253: val = -16'sd2410;
        8'd254: val = -16'sd1608;
        8'd255: val = -16'sd804;
        default: val = 16'sd0;
      endcase
   end
endmodule
