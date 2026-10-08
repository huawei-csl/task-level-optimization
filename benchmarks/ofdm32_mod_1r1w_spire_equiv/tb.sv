// Active testbench harness for ofdm32_mod_1r1w — the TRANSMIT half of the
// OFDM modem (N=32, CP=8) behind a 1R/1W memory port.
//
// The DUT (ofdm32_mod_core) reads one job from a harness-owned input memory
// (word 0 = mod, words 1..12 = the 192 payload bits, word k holding
// in_bits[16(k-1) +: 16]), runs the bit-exact TX chain (QAM map -> IFFT ->
// CP), writes the 80 sample words of the modulated symbol (sample s:
// word 2s = I, word 2s+1 = Q, two's-complement 16-bit) to a harness-owned
// output memory, and asserts done. The DUT chooses its OWN cycle schedule
// and access order; this harness is the SOLE correctness oracle (no CEC).
// Golden samples were captured from the upstream RTL's TX at the loopback
// tap (see validation/ofdm/gen_split_vectors.py).
//
// Port contract (all synchronous to clk; rst is async active-high):
//   rd_en   out 1            issue a read this cycle
//   rd_addr out 4            input word index 0..12
//   rd_data in  16           valid EXACTLY 1 cycle after the cycle rd_en is high
//                            (SRAM-like registered read; back-to-back allowed)
//   wr_en   out 1            commit a result word this cycle (posted)
//   wr_addr out 7            output word index 0..79
//   wr_data out 16           sample word (I/Q interleaved)
//   done    out 1            sticky; rises after the last write is issued
//
// Vectors: one 16-bit-wide array; per case (stride 93): 13 input words then
// 80 golden sample words. See validation/ofdm/gen_split_vectors.py.
//
// TB_CYCLES total = SUM of per-case cycle counts (reset-release -> done posedge).
`timescale 1ns/1ps
module tb;
  // ---- shape / layout params (keep NCASES in sync with gen_vectors.py) ----
  localparam int NIN = 13;            // input words per case
  localparam int NOUT = 80;           // output words per case
  localparam int RD_SPAN = 13;        // valid read addresses [0,13)
  localparam int CASE_STRIDE = 93;    // words per case in vectors.dat
  localparam int NCASES = 21;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 4000;    // generous; a legal slow design fits
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
  logic [3:0]  rd_addr;
  logic [15:0] rd_data;
  logic        wr_en;
  logic [6:0]  wr_addr;
  logic [15:0] wr_data;
  logic        done;

  ofdm32_mod_core dut (
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
  logic [15:0] data_all [0:NWORDS-1];   // inputs + golden outputs per case
  logic [15:0] mem_out  [0:NOUT-1];     // DUT-written results for current case
  logic        written  [0:NOUT-1];     // write bitmask for current case
  int          case_base;               // word offset of current case in data_all
  logic        clear_case;              // 1-cycle pulse to reset per-case harness state
  logic        oob_read;                // sticky: read with addr out of range
  logic        oob_write;               // sticky: write with addr out of range
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
    if (clear_case) begin
      oob_read <= 1'b0;
    end else if (rd_en) begin
      if (rd_addr >= RD_SPAN) oob_read <= 1'b1;
      else                    rd_data <= data_all[case_base + rd_addr];
    end
  end

  // ---- posted write port + bitmask (single driver for mem_out/written) ----
  integer wk;
  always @(posedge clk) begin
    if (clear_case) begin
      oob_write       <= 1'b0;
      post_done_write <= 1'b0;
      for (wk = 0; wk < NOUT; wk = wk + 1) written[wk] <= 1'b0;
    end else if (wr_en) begin
      if (done)                post_done_write <= 1'b1;   // write after done -> illegal
      else if (wr_addr >= NOUT) oob_write <= 1'b1;
      else begin
        mem_out[wr_addr] <= wr_data;
        written[wr_addr] <= 1'b1;
      end
    end
  end

  // ---- per-case driver ----
  int c, j, wd;
  // ---- I/O-trace equivalence oracle (see description.txt) ----
  logic [29:0] exp_trace [0:8191];
  logic [31:0] exp_len [0:NCASES-1];
  logic [29:0] eq_e, eq_g;
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
          if ((eq_g[29] !== eq_e[29]) || (eq_g[5] !== eq_e[5]) || (eq_g[0] !== eq_e[0])
              || (eq_e[5] && (eq_g[28:13] !== eq_e[28:13] || eq_g[12:6] !== eq_e[12:6]))
              || (eq_e[0] && (eq_g[4:1] !== eq_e[4:1]))) begin
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

      // word checks: every output word written exactly to the golden value
      for (j = 0; j < NOUT; j = j + 1) begin
        total_checks++;
        if (!written[j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d not written (done asserted with incomplete output)", idx, j);
        end else if (mem_out[j] !== data_all[case_base + NIN + j]) begin
          total_errors++;
          $display("TB_ERROR case=%0d addr=%0d expected=%04x actual=%04x",
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
    oob_read = 1'b0;
    oob_write = 1'b0;
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
