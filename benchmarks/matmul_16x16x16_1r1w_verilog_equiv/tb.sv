// Active testbench harness for matmul/16x16x16_1r1w.
//
// The DUT (matmul_core) computes C = A*B for M=K=N=16, 8-bit unsigned operands,
// 20-bit unsigned full-precision output. The DUT chooses its own cycle
// schedule; this harness models the memory it reads from and writes to, and is
// the SOLE correctness oracle (no CEC).
//
// Port contract (all synchronous to clk; rst is synchronous active-high):
//   rd_en   out 1            issue a read this cycle
//   rd_addr out 9            flat element index; A in [0,MK), B in [MK,MK+KN)
//   rd_data in  8            valid EXACTLY 1 cycle after the cycle rd_en is high
//                            (SRAM-like registered read; back-to-back allowed)
//   wr_en   out 1            commit a result element this cycle (posted)
//   wr_addr out 8            C element index in [0,MN), row-major
//   wr_data out 20           result value
//   done    out 1            sticky; rises after the last write is issued
//
// Vectors: see gen_vectors.py. One 20-bit-wide array, inputs in low 8 bits.
//   per case c (stride 768): A[0..255] B[0..255] (8-bit) then golden C[0..255] (20-bit)
//
// TB_CYCLES total = SUM of per-case cycle counts (reset-release -> done posedge).
`timescale 1ns/1ps
module tb;
  // ---- shape / layout params (keep NCASES in sync with gen_vectors.py) ----
  localparam int M = 16, K = 16, N = 16;
  localparam int MK = M*K;            // 256  A elements
  localparam int KN = K*N;            // 256  B elements
  localparam int MN = M*N;            // 256  C elements
  localparam int RD_SPAN = MK + KN;   // 512  valid read addresses [0,512)
  localparam int CASE_STRIDE = MK + KN + MN;  // 768 words per case in vectors.dat
  localparam int NCASES = 7;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 50000;   // generous; a legal slow design fits
  localparam int POST_DONE_WINDOW = 300;    // cycles watched for illegal late writes

  int total_checks;
  int total_errors;
  int total_cycles;
  int total_reads;    // read accesses (rd_en high), summed across cases -> data-movement energy
  int total_writes;   // write accesses (wr_en high), summed across cases

  // ---- DUT ports ----
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
    .clk(clk),
    .rst(rst),
    .rd_en(rd_en),
    .rd_addr(rd_addr),
    .rd_data(rd_data),
    .wr_en(wr_en),
    .wr_addr(wr_addr),
    .wr_data(wr_data),
    .done(done)
  );

  // ---- harness memory + state ----
  logic [19:0] data_all [0:NWORDS-1];   // inputs (low 8b) + golden C (20b)
  logic [19:0] mem_out  [0:MN-1];       // DUT-written results for current case
  logic        written  [0:MN-1];       // write bitmask for current case
  int          case_base;               // word offset of current case in data_all
  logic        clear_case;              // 1-cycle pulse to reset per-case harness state
  logic        oob_read;                // sticky: read while rd_en with addr out of range
  logic        oob_write;               // sticky: write with addr out of range
  logic        post_done_write;         // sticky: any write while done is high

  // ---- clock ----
  initial clk = 0;
  always #5 clk = ~clk;

  // ---- data-movement counters (summed across all cases) ----
  // Each cycle the DUT asserts rd_en / wr_en is one memory access. With a 1-wide
  // port that's one element; a future wide port would move several per access,
  // so this counts ACCESSES (rewards packing + reuse, not raw element count).
  always @(posedge clk) begin
    if (!rst && !done) begin          // count only the operating window (reset release -> done)
      if (rd_en) total_reads  <= total_reads  + 1;
      if (wr_en) total_writes <= total_writes + 1;
    end
  end

  // ---- registered read port: rd_data valid one cycle after rd_en (SRAM-like) ----
  always @(posedge clk) begin
    if (clear_case) begin
      oob_read <= 1'b0;
    end else if (rd_en) begin
      if (rd_addr >= RD_SPAN) oob_read <= 1'b1;          // structurally impossible at 9b; defensive
      else                    rd_data <= data_all[case_base + rd_addr][7:0];
    end
  end

  // ---- posted write port + bitmask (single driver for mem_out/written) ----
  integer wk;
  always @(posedge clk) begin
    if (clear_case) begin
      oob_write       <= 1'b0;
      post_done_write <= 1'b0;
      for (wk = 0; wk < MN; wk = wk + 1) written[wk] <= 1'b0;
    end else if (wr_en) begin
      if (done)               post_done_write <= 1'b1;   // write after done -> illegal
      else if (wr_addr >= MN) oob_write <= 1'b1;         // structurally impossible at 8b; defensive
      else begin
        mem_out[wr_addr] <= wr_data;
        written[wr_addr] <= 1'b1;
      end
    end
  end

  // ---- per-case driver ----
  int c, j, wd;
  // ---- I/O-trace equivalence oracle (see description.txt) ----
  logic [39:0] exp_trace [0:131071];
  logic [31:0] exp_len [0:NCASES-1];
  logic [39:0] eq_e, eq_g;
  int tidx, case_exp_end, eq_shown;
  task automatic run_case(input int idx);
    begin
      case_base = idx * CASE_STRIDE;
      case_exp_end = tidx + int'(exp_len[idx]);
      eq_shown = 0;
      // assert reset + clear harness state for a few cycles
      rst = 1'b1;
      clear_case = 1'b1;
      repeat (3) @(posedge clk);
      #1;
      clear_case = 1'b0;
      rst = 1'b0;   // released; DUT starts on the next posedge

      // run until done (sticky) or watchdog; wd counts operating cycles
      wd = 0;
      while (done !== 1'b1 && wd < WATCHDOG_CYCLES) begin
        @(posedge clk);
        wd = wd + 1;
        if (tidx < case_exp_end) begin
          eq_e = exp_trace[tidx];
          eq_g = {done, wr_data, wr_addr, wr_en, rd_addr, rd_en};
          total_checks++;
          if ((eq_g[39] !== eq_e[39]) || (eq_g[10] !== eq_e[10]) || (eq_g[0] !== eq_e[0])
              || (eq_e[10] && (eq_g[38:19] !== eq_e[38:19] || eq_g[18:11] !== eq_e[18:11]))
              || (eq_e[0] && (eq_g[9:1] !== eq_e[9:1]))) begin
            total_errors++;
            if (eq_shown < 5) begin
              $display("TB_EQUIV_ERROR case=%0d cyc=%0d expected=%010x got=%010x (I/O trace differs from reference schedule)",
                       idx, wd - 1, eq_e, eq_g);
              eq_shown++;
            end
          end
          tidx++;
        end
      end
      if (done !== 1'b1) begin
        $display("TB_WATCHDOG case=%0d done never asserted after %0d cycles", idx, wd);
        $display("TB_CYCLES total=%0d", total_cycles + wd);
        $display("TB_SUMMARY total=%0d errors=%0d", total_checks + 1, total_errors + 1);
        $fatal(1, "FAIL");
      end
      total_cycles = total_cycles + wd;
      total_checks++;
      if (tidx != case_exp_end) begin
        total_errors++;
        $display("TB_EQUIV_ERROR case=%0d done after %0d cycles; reference schedule has %0d", idx, wd, int'(exp_len[idx]));
        tidx = case_exp_end;
      end

      // watch a window for illegal post-done writes
      repeat (POST_DONE_WINDOW) @(posedge clk);

      // protocol checks (1 each; range checks are no-ops at these widths but kept)
      total_checks++;
      if (oob_read) begin
        total_errors++;
        $display("TB_ERROR case=%0d oob_read", idx);
      end
      total_checks++;
      if (oob_write) begin
        total_errors++;
        $display("TB_ERROR case=%0d oob_write", idx);
      end
      total_checks++;
      if (post_done_write) begin
        total_errors++;
        $display("TB_ERROR case=%0d post_done_write (DUT wrote after asserting done)", idx);
      end

      // element checks: every C[j] must have been written exactly to the golden value
      for (j = 0; j < MN; j = j + 1) begin
        total_checks++;
        if (!written[j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d not written (done asserted with incomplete C)", idx, j);
        end else if (mem_out[j] !== data_all[case_base + MK + KN + j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d expected=%0d actual=%0d",
                   idx, j, data_all[case_base + MK + KN + j], mem_out[j]);
        end
      end
    end
  endtask

  initial begin
    total_checks = 0;
    total_errors = 0;
    total_cycles = 0;
    total_reads = 0;
    total_writes = 0;
    rst = 1'b1;
    clear_case = 1'b1;
    oob_read = 1'b0;
    oob_write = 1'b0;
    post_done_write = 1'b0;
    rd_data = 8'h00;

    $readmemh("vectors.dat", data_all);
    $readmemh("trace.dat", exp_trace);
    $readmemh("trace_len.dat", exp_len);
    tidx = 0;

    for (c = 0; c < NCASES; c = c + 1) begin
      run_case(c);
    end

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_READS total=%0d", total_reads);
    $display("TB_WRITES total=%0d", total_writes);
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end

`ifdef VCD_FILE_PATH
// VCD dump of the DUT subtree for measured-power evaluation: tech_eval's
// netlist sim passes -DVCD_FILE_PATH=<path> + --trace-vcd; inert otherwise.
`define __PWRSTR(x) `"x`"
  initial begin
    $dumpfile(`__PWRSTR(`VCD_FILE_PATH));
    $dumpvars(0, dut);
  end
`endif
endmodule
