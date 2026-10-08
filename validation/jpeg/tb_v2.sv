// Validation-v2 testbench for jpeg_idct_2d_1r1w: 100,000 blocks, dual mode.
// Default (check): reads vectors_v2.dat (128 words/case: 64 coeff inputs low
// 16b, then 64 golden pixels 32b) and checks like the campaign tb.sv.
// Capture (+capture): reads stim_v2.dat (64 coeff words/case), drives the
// BIT-EXACT STARTING DESIGN and writes vectors_v2.dat. The first 323 blocks
// are the campaign inputs; the validation runner crosschecks their captured
// goldens against the campaign's upstream-derived vectors.dat.
// Protocol (registered 1R read, posted writes, per-case sync reset, watchdog,
// post-done window) identical to the campaign tb.sv.
`timescale 1ns/1ps
module tb;
  localparam int NIN = 64, NOUT = 64;
  localparam int CASE_STRIDE = NIN + NOUT;
  localparam int NCASES = 100000;
  localparam int WATCHDOG_CYCLES = 20000;
  localparam int POST_DONE_WINDOW = 200;

  int total_checks, total_errors;
  longint total_cycles, total_reads, total_writes;

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
    .clk(clk), .rst(rst),
    .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(rd_data),
    .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data),
    .done(done)
  );

  bit capture_mode;
  logic [15:0] stim   [0:NCASES*NIN-1];        // capture mode input blocks
  logic [31:0] data_all [0:NCASES*CASE_STRIDE-1];  // check mode inputs+goldens
  logic [31:0] mem_out [0:NOUT-1];
  logic        written [0:NOUT-1];
  int          case_base;
  int          case_idx;
  logic        clear_case;
  logic        oob_write, post_done_write;
  int          fo;

  initial clk = 0;
  always #5 clk = ~clk;

  always @(posedge clk) begin
    if (!rst && !done) begin          // count only the operating window (reset release -> done)
      if (rd_en) total_reads  <= total_reads  + 1;
      if (wr_en) total_writes <= total_writes + 1;
    end
  end

  // registered read port: coefficient source depends on mode
  always @(posedge clk) begin
    if (rd_en) begin
      if (capture_mode) rd_data <= stim[case_idx * NIN + rd_addr];
      else              rd_data <= data_all[case_base + rd_addr][15:0];
    end
  end

  integer wk;
  always @(posedge clk) begin
    if (clear_case) begin
      oob_write       <= 1'b0;
      post_done_write <= 1'b0;
      for (wk = 0; wk < NOUT; wk = wk + 1) written[wk] <= 1'b0;
    end else if (wr_en) begin
      if (done)                post_done_write <= 1'b1;
      else if (wr_addr >= NOUT) oob_write <= 1'b1;
      else begin
        mem_out[wr_addr] <= wr_data;
        written[wr_addr] <= 1'b1;
      end
    end
  end

  int c, j, wd;
  task automatic run_case(input int idx);
    begin
      case_base = idx * CASE_STRIDE;
      case_idx  = idx;
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

      if (capture_mode) begin
        for (j = 0; j < NIN; j = j + 1)
          $fwrite(fo, "%08h\n", {16'h0, stim[idx * NIN + j]});
        for (j = 0; j < NOUT; j = j + 1)
          $fwrite(fo, "%08h\n", written[j] ? mem_out[j] : 32'hDEAD_DEAD);
        total_checks++;
        if (post_done_write || oob_write) begin
          total_errors++;
          $display("TB_ERROR case=%0d protocol violation during capture", idx);
        end
      end else begin
        total_checks++;
        if (oob_write) begin total_errors++; if (total_errors < 50) $display("TB_ERROR case=%0d oob_write", idx); end
        total_checks++;
        if (post_done_write) begin
          total_errors++;
          if (total_errors < 50) $display("TB_ERROR case=%0d post_done_write", idx);
        end
        for (j = 0; j < NOUT; j = j + 1) begin
          total_checks++;
          if (!written[j]) begin
            total_errors++;
            if (total_errors < 50) $display("TB_ERROR case=%0d addr=%0d not written", idx, j);
          end else if (mem_out[j] !== data_all[case_base + NIN + j]) begin
            total_errors++;
            if (total_errors < 50)
              $display("TB_ERROR case=%0d addr=%0d expected=%0d actual=%0d",
                       idx, j, data_all[case_base + NIN + j], mem_out[j]);
          end
        end
      end
    end
  endtask

  initial begin
    capture_mode = $test$plusargs("capture");
    total_checks = 0; total_errors = 0;
    total_cycles = 0; total_reads = 0; total_writes = 0;
    rst = 1'b1; clear_case = 1'b1;
    oob_write = 1'b0; post_done_write = 1'b0;
    rd_data = '0;

    if (capture_mode) begin
      $readmemh("stim_v2.dat", stim);
      fo = $fopen("vectors_v2.dat", "w");
      if (fo == 0) $fatal(1, "cannot open vectors_v2.dat for write");
    end else begin
      $readmemh("vectors_v2.dat", data_all);
    end

    for (c = 0; c < NCASES; c = c + 1) run_case(c);
    if (capture_mode) $fclose(fo);

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_READS total=%0d", total_reads);
    $display("TB_WRITES total=%0d", total_writes);
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule
