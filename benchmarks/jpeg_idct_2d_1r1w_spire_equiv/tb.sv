// Active testbench harness for jpeg_idct_2d_1r1w — the flexible-schedule 8x8
// 2-D IDCT. The DUT reads 64 dequantized coefficients from a harness-owned
// input memory, computes the 2-D IDCT (the exact fixed-point function of the
// jpeg_idct_2d benchmarks: 32-bit wrap contexts, >>>11 / >>>15, (x*181)/256
// truncating division), writes the 64 row-major pixels to a harness-owned
// output memory, and asserts done. The DUT chooses its OWN cycle schedule and
// access order; this harness is the SOLE correctness oracle (no CEC).
//
// Port contract (all synchronous to clk; rst is synchronous active-high):
//   rd_en   out 1            issue a read this cycle
//   rd_addr out 6            coefficient index 0..63, row-major c[row][col]
//   rd_data in  16           valid EXACTLY 1 cycle after the cycle rd_en is high
//                            (SRAM-like registered read; back-to-back allowed)
//   wr_en   out 1            commit a result pixel this cycle (posted)
//   wr_addr out 6            pixel index 0..63, row-major
//   wr_data out 32           result value
//   done    out 1            sticky; rises after the last write is issued
//
// Vectors: one 32-bit-wide array; per case (stride 128): 64 input coefficients
// (low 16 bits) then 64 golden pixels (32-bit, row-major). Byte-identical to
// the jpeg_idct_2d benchmarks' vectors.dat.
//
// TB_CYCLES total = SUM of per-case cycle counts (reset-release -> done posedge).
module tb;
  // ---- shape / layout params (keep NCASES in sync with vectors.dat) ----
  localparam int NIN  = 64;             // input coefficients per case
  localparam int NOUT = 64;             // output pixels per case
  localparam int CASE_STRIDE = NIN + NOUT;   // 128 words per case
  localparam int NCASES = 323;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 20000;   // generous; a legal slow design fits
  localparam int POST_DONE_WINDOW = 200;    // cycles watched for illegal late writes

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
  logic [31:0] wr_data;
  logic        done;

  jpeg_idct_2d_1r1w dut (
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
  // data_all: per case (stride 128): 64 inputs (low 16b) then 64 golden pixels
  // (32b, row-major) — loaded directly with $readmemh.
  logic [31:0] data_all [0:NWORDS-1];
  logic [31:0] mem_out  [0:NOUT-1];     // DUT-written results for current case
  logic        written  [0:NOUT-1];     // write bitmask for current case
  int          case_base;               // word offset of current case in data_all
  logic        clear_case;              // 1-cycle pulse to reset per-case harness state
  logic        post_done_write;         // sticky: any write while done is high

  // ---- clock ----
  initial clk = 0;
  always #5 clk = ~clk;

  // ---- registered read port: rd_data valid one cycle after rd_en (SRAM-like) ----
  // (also counts read accesses — data-movement energy, summed across all cases)
  always @(posedge clk) begin
    if (rd_en) begin
      rd_data <= data_all[case_base + rd_addr][15:0];
      if (!rst && !done) total_reads <= total_reads + 1;   // count only the operating window
    end
  end

  // ---- posted write port + bitmask (single driver for mem_out/written) ----
  // (also counts write accesses)
  integer wk;
  always @(posedge clk) begin
    if (wr_en && !rst && !done) total_writes <= total_writes + 1;
    if (clear_case) begin
      post_done_write <= 1'b0;
      for (wk = 0; wk < NOUT; wk = wk + 1) written[wk] <= 1'b0;
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
  logic [46:0] exp_trace [0:98303];
  logic [31:0] exp_len [0:NCASES-1];
  logic [46:0] eq_e, eq_g;
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
          if ((eq_g[46] !== eq_e[46]) || (eq_g[7] !== eq_e[7]) || (eq_g[0] !== eq_e[0])
              || (eq_e[7] && (eq_g[45:14] !== eq_e[45:14] || eq_g[13:8] !== eq_e[13:8]))
              || (eq_e[0] && (eq_g[6:1] !== eq_e[6:1]))) begin
            total_errors++;
            if (eq_shown < 5) begin
              $display("TB_EQUIV_ERROR case=%0d cyc=%0d expected=%012x got=%012x (I/O trace differs from reference schedule)",
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

      // element checks: every pixel must have been written exactly to the golden value
      for (j = 0; j < NOUT; j = j + 1) begin
        total_checks++;
        if (!written[j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d not written (done asserted with incomplete block)", idx, j);
        end else if (mem_out[j] !== data_all[case_base + NIN + j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d expected=%08x actual=%08x",
                   idx, j, data_all[case_base + NIN + j], mem_out[j]);
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
