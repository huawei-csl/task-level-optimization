// Active testbench harness for qr8_1r1w — the memory-port recast of the
// logikbench CORDIC QR at N=8, DW=16.
//
// The DUT (qr8_core) reads the 8x8 signed input matrix A from a harness-owned
// memory, computes the fixed-point QR triangularization R with the EXACT
// upstream arithmetic (M=16 CORDIC micro-rotations, F=8 fractional bits,
// IW=32 wrap, KINV Q15 gain compensation), writes all 64 R elements to a
// harness-owned output memory, and asserts done. The DUT chooses its OWN
// cycle schedule and access order; this harness is the SOLE correctness
// oracle (no CEC). Golden R values were captured from the UNMODIFIED
// upstream RTL at N=8.
//
// Port contract (all synchronous to clk; rst is synchronous active-high):
//   rd_en   out 1            issue a read this cycle
//   rd_addr out 6            A element index 0..63, row-major
//   rd_data in  16           valid EXACTLY 1 cycle after the cycle rd_en is high
//                            (SRAM-like registered read; back-to-back allowed)
//   wr_en   out 1            commit a result element this cycle (posted)
//   wr_addr out 6            R element index 0..63, row-major
//   wr_data out 16           result value ((W >> F) low 16 bits)
//   done    out 1            sticky; rises after the last write is issued
//
// Vectors: one 16-bit-wide array; per case (stride 128): 64 input elements
// then 64 golden R elements (row-major).
//
// TB_CYCLES total = SUM of per-case cycle counts (reset-release -> done posedge).
`timescale 1ns/1ps
module tb;
  // ---- shape / layout params (keep NCASES in sync with gen_vectors.py) ----
  localparam int N = 8;
  localparam int NN = 64;             // matrix elements (in and out)
  localparam int RD_SPAN = 64;        // valid read addresses [0,64)
  localparam int CASE_STRIDE = 128;   // words per case in vectors.dat
  localparam int NCASES = 15;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 8000;    // generous; a legal slow design fits
  localparam int POST_DONE_WINDOW = 100;    // cycles watched for illegal late writes

  int total_checks;
  int total_errors;
  int total_cycles;
  int total_reads;    // read accesses (rd_en high), summed across cases -> data-movement energy
  int total_writes;   // write accesses (wr_en high), summed across cases

  // ---- DUT ports ----
  logic        clk;
  logic        rst;
  logic        rd_en;
  logic [5:0]  rd_addr;
  logic [15:0] rd_data;
  logic        wr_en;
  logic [5:0]  wr_addr;
  logic [15:0] wr_data;
  logic        done;

  qr8_core dut (
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
  logic [15:0] data_all [0:NWORDS-1];   // inputs + golden R per case
  logic [15:0] mem_out  [0:NN-1];       // DUT-written results for current case
  logic        written  [0:NN-1];       // write bitmask for current case
  int          case_base;               // word offset of current case in data_all
  logic        clear_case;              // 1-cycle pulse to reset per-case harness state
  logic        post_done_write;         // sticky: any write while done is high

  // ---- clock ----
  initial clk = 0;
  always #5 clk = ~clk;

  // ---- data-movement counters (summed across all cases) ----
  always @(posedge clk) begin
    if (!rst && !done) begin          // count only the operating window (reset release -> done)
      if (rd_en) total_reads  <= total_reads  + 1;
      if (wr_en) total_writes <= total_writes + 1;
    end
  end

  // ---- registered read port: rd_data valid one cycle after rd_en (SRAM-like) ----
  always @(posedge clk) begin
    if (rd_en) rd_data <= data_all[case_base + rd_addr];
  end

  // ---- posted write port + bitmask (single driver for mem_out/written) ----
  integer wk;
  always @(posedge clk) begin
    if (clear_case) begin
      post_done_write <= 1'b0;
      for (wk = 0; wk < NN; wk = wk + 1) written[wk] <= 1'b0;
    end else if (wr_en) begin
      if (done) post_done_write <= 1'b1;   // write after done -> illegal
      else begin
        mem_out[wr_addr] <= wr_data;
        written[wr_addr] <= 1'b1;
      end
    end
  end

  // ---- per-case driver ----
  int c, j, wd;
  // ---- I/O-trace equivalence oracle (see description.txt) ----
  logic [30:0] exp_trace [0:65535];
  logic [31:0] exp_len [0:NCASES-1];
  logic [30:0] eq_e, eq_g;
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
          if ((eq_g[30] !== eq_e[30]) || (eq_g[7] !== eq_e[7]) || (eq_g[0] !== eq_e[0])
              || (eq_e[7] && (eq_g[29:14] !== eq_e[29:14] || eq_g[13:8] !== eq_e[13:8]))
              || (eq_e[0] && (eq_g[6:1] !== eq_e[6:1]))) begin
            total_errors++;
            if (eq_shown < 5) begin
              $display("TB_EQUIV_ERROR case=%0d cyc=%0d expected=%08x got=%08x (I/O trace differs from reference schedule)",
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

      // one-case trace; also keeps the access counters live across
      // processes (works around a Verilator const-folding issue seen at -O2,
      // where NBA-incremented counters read only at end-of-sim fold to 0)
      if (idx == 0)
        $display("TB_CASE case=0 cycles=%0d reads_so_far=%0d writes_so_far=%0d",
                 wd, total_reads, total_writes);

      total_checks++;
      if (post_done_write) begin
        total_errors++;
        $display("TB_ERROR case=%0d post_done_write (DUT wrote after asserting done)", idx);
      end

      // element checks: every R[j] must have been written exactly to the golden value
      for (j = 0; j < NN; j = j + 1) begin
        total_checks++;
        if (!written[j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d not written (done asserted with incomplete R)", idx, j);
        end else if (mem_out[j] !== data_all[case_base + NN + j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d expected=%04x actual=%04x",
                   idx, j, data_all[case_base + NN + j], mem_out[j]);
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
    post_done_write = 1'b0;
    rd_data = 16'h0000;

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
