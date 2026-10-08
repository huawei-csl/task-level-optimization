// Validation-v2 testbench for matmul/16x16x16_1r1w: self-contained, in-TB
// golden (C = A*B computed in SV -- exact for unsigned integer matmul), no
// vector files. 10,000 cases (~5.1M operand words): a directed edge-case
// block first, then xorshift-PRNG random matrices, deterministic in the case
// index. Port protocol, per-case reset/metering, watchdog and post-done
// window are identical to the campaign tb.sv. Override case count with
// +ncases=<n>.
`timescale 1ns/1ps
module tb;
  localparam int M = 16, K = 16, N = 16;
  localparam int MK = M*K, KN = K*N, MN = M*N;
  localparam int RD_SPAN = MK + KN;
  localparam int WATCHDOG_CYCLES = 50000;
  localparam int POST_DONE_WINDOW = 300;
  localparam int NDIRECTED = 24;

  int ncases = 10000;

  int total_checks, total_errors;
  longint total_cycles, total_reads, total_writes;

  logic        clk;
  logic        rst;
  logic        rd_en;
  logic [8:0]  rd_addr;
  logic [7:0]  rd_data;
  logic        wr_en;
  logic [7:0]  wr_addr;
  logic [19:0] wr_data;
  logic        done;

  matmul_core dut (
    .clk(clk), .rst(rst),
    .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(rd_data),
    .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data),
    .done(done)
  );

  logic [7:0]  Amat [0:MK-1];
  logic [7:0]  Bmat [0:KN-1];
  logic [19:0] Gold [0:MN-1];
  logic [19:0] mem_out [0:MN-1];
  logic        written [0:MN-1];
  logic        clear_case;
  logic        oob_read, oob_write, post_done_write;

  initial clk = 0;
  always #5 clk = ~clk;

  always @(posedge clk) begin
    if (!rst && !done) begin          // count only the operating window (reset release -> done)
      if (rd_en) total_reads  <= total_reads  + 1;
      if (wr_en) total_writes <= total_writes + 1;
    end
  end

  // registered read port (1-cycle latency, SRAM-like)
  always @(posedge clk) begin
    if (clear_case) begin
      oob_read <= 1'b0;
    end else if (rd_en) begin
      if (rd_addr >= RD_SPAN) oob_read <= 1'b1;
      else if (rd_addr < MK)  rd_data <= Amat[rd_addr[7:0]];
      else                    rd_data <= Bmat[rd_addr - MK];
    end
  end

  integer wk;
  always @(posedge clk) begin
    if (clear_case) begin
      oob_write       <= 1'b0;
      post_done_write <= 1'b0;
      for (wk = 0; wk < MN; wk = wk + 1) written[wk] <= 1'b0;
    end else if (wr_en) begin
      if (done)               post_done_write <= 1'b1;
      else if (wr_addr >= MN) oob_write <= 1'b1;
      else begin
        mem_out[wr_addr] <= wr_data;
        written[wr_addr] <= 1'b1;
      end
    end
  end

  // ---- deterministic PRNG (xorshift32) ----
  logic [31:0] prng;
  function automatic logic [7:0] next8();
    begin
      prng = prng ^ (prng << 13);
      prng = prng ^ (prng >> 17);
      prng = prng ^ (prng << 5);
      next8 = prng[7:0];
    end
  endfunction

  // ---- stimulus generation: directed cases, then random ----
  integer gi, gj, gl;
  task automatic gen_case(input int idx);
    begin
      prng = 32'h9E3779B9 ^ (idx * 32'h85EBCA6B) | 32'h1;
      case (idx)
        0: begin  // all zeros
          for (gi = 0; gi < MK; gi++) begin Amat[gi] = 0; Bmat[gi] = 0; end
        end
        1: begin  // all max
          for (gi = 0; gi < MK; gi++) begin Amat[gi] = 8'hFF; Bmat[gi] = 8'hFF; end
        end
        2: begin  // A identity, B random
          for (gi = 0; gi < MK; gi++) begin
            Amat[gi] = (gi[7:4] == gi[3:0]) ? 8'h01 : 8'h00;
            Bmat[gi] = next8();
          end
        end
        3: begin  // A random, B identity
          for (gi = 0; gi < MK; gi++) begin
            Amat[gi] = next8();
            Bmat[gi] = (gi[7:4] == gi[3:0]) ? 8'h01 : 8'h00;
          end
        end
        4: begin  // single-hot max in A, B max
          for (gi = 0; gi < MK; gi++) begin Amat[gi] = 0; Bmat[gi] = 8'hFF; end
          Amat[0] = 8'hFF;
        end
        5: begin  // rank-1 outer-product operands (row * column patterns)
          for (gi = 0; gi < MK; gi++) begin
            Amat[gi] = gi[3:0] * 8'h11;
            Bmat[gi] = gi[7:4] * 8'h0F;
          end
        end
        6: begin  // alternating AA/55
          for (gi = 0; gi < MK; gi++) begin
            Amat[gi] = gi[0] ? 8'hAA : 8'h55;
            Bmat[gi] = gi[0] ? 8'h55 : 8'hAA;
          end
        end
        7: begin  // A zero, B random
          for (gi = 0; gi < MK; gi++) begin Amat[gi] = 0; Bmat[gi] = next8(); end
        end
        8: begin  // A random, B zero
          for (gi = 0; gi < MK; gi++) begin Amat[gi] = next8(); Bmat[gi] = 0; end
        end
        9: begin  // row/col ramps
          for (gi = 0; gi < MK; gi++) begin
            Amat[gi] = {gi[7:4], gi[3:0]};
            Bmat[gi] = 8'd255 - gi[7:0];
          end
        end
        default: begin
          if (idx < NDIRECTED) begin
            // sparse random: mostly zero with a few max-value entries
            for (gi = 0; gi < MK; gi++) begin
              Amat[gi] = (next8() < 16) ? 8'hFF : 8'h00;
              Bmat[gi] = (next8() < 16) ? 8'hFF : 8'h00;
            end
          end else begin
            for (gi = 0; gi < MK; gi++) begin
              Amat[gi] = next8();
              Bmat[gi] = next8();
            end
          end
        end
      endcase
      // golden: C[i][j] = sum_l A[i][l] * B[l][j]  (exact, 20-bit max 1,040,400)
      for (gi = 0; gi < M; gi++)
        for (gj = 0; gj < N; gj++) begin
          Gold[gi*N + gj] = '0;
          for (gl = 0; gl < K; gl++)
            Gold[gi*N + gj] += 20'(Amat[gi*K + gl]) * 20'(Bmat[gl*N + gj]);
        end
    end
  endtask

  int c, j, wd;
  task automatic run_case(input int idx);
    begin
      gen_case(idx);
      rst = 1'b1;
      clear_case = 1'b1;
      repeat (3) @(posedge clk);
      #1;
      clear_case = 1'b0;
      rst = 1'b0;

      wd = 0;
      while (done !== 1'b1 && wd < WATCHDOG_CYCLES) begin
        @(posedge clk);
        wd = wd + 1;
      end
      if (done !== 1'b1) begin
        $display("TB_WATCHDOG case=%0d done never asserted after %0d cycles", idx, wd);
        $display("TB_CYCLES total=%0d", total_cycles + wd);
        $display("TB_SUMMARY total=%0d errors=%0d", total_checks + 1, total_errors + 1);
        $fatal(1, "FAIL");
      end
      total_cycles = total_cycles + wd;

      repeat (POST_DONE_WINDOW) @(posedge clk);

      total_checks++;
      if (oob_read) begin total_errors++; if (total_errors < 50) $display("TB_ERROR case=%0d oob_read", idx); end
      total_checks++;
      if (oob_write) begin total_errors++; if (total_errors < 50) $display("TB_ERROR case=%0d oob_write", idx); end
      total_checks++;
      if (post_done_write) begin
        total_errors++;
        if (total_errors < 50) $display("TB_ERROR case=%0d post_done_write", idx);
      end

      for (j = 0; j < MN; j = j + 1) begin
        total_checks++;
        if (!written[j]) begin
          total_errors++;
          if (total_errors < 50) $display("TB_ERROR case=%0d addr=%0d not written", idx, j);
        end else if (mem_out[j] !== Gold[j]) begin
          total_errors++;
          if (total_errors < 50)
            $display("TB_ERROR case=%0d addr=%0d expected=%0d actual=%0d",
                     idx, j, Gold[j], mem_out[j]);
        end
      end
    end
  endtask

  initial begin
    void'($value$plusargs("ncases=%d", ncases));
    total_checks = 0; total_errors = 0;
    total_cycles = 0; total_reads = 0; total_writes = 0;
    rst = 1'b1; clear_case = 1'b1;
    oob_read = 1'b0; oob_write = 1'b0; post_done_write = 1'b0;
    rd_data = 8'h00;

    for (c = 0; c < ncases; c = c + 1) run_case(c);

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_READS total=%0d", total_reads);
    $display("TB_WRITES total=%0d", total_writes);
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule
