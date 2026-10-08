// Testbench for ofdm32_mod_upstream: the VERBATIM (parameter-corrected)
// logikbench upstream TX at N=32/CP=8, driven over its NATIVE interface --
// one in_valid pulse per symbol (192-bit payload + mod held stable), the
// modulated symbol streamed back as out_valid/out_last/out_r/out_i beats.
//
// Purpose: the upstream's own ARP under the study flow (upstream
// comparison; replaces the equal-effort estimate). Same workload as the
// ofdm32_mod_1r1w sibling: vectors.dat is byte-identical (stride 93 per
// case: word 0 = mod, 1..12 = in_bits, 13..92 = golden samples, word
// 13+2s = I and 14+2s = Q of sample s).
//
// Metering mirrors the study TBs: reset between cases, per-case cycles
// counted from reset release to the out_last beat inclusive, summed into
// TB_CYCLES. No memory port -> no TB_READS/TB_WRITES (as with the ALU).
module tb;
  localparam int DW = 16;
  localparam int N = 32;
  localparam int CP = 8;
  localparam int NSAMP = N + CP;            // 40 streamed samples per case
  localparam int CASE_STRIDE = 93;
  localparam int NCASES = 21;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 4000;

  logic clk, rst, in_valid;
  logic [1:0] mod;
  logic [6*N-1:0] in_bits;
  logic out_valid, out_last;
  logic signed [DW-1:0] out_r, out_i;

  ofdm_tx #(.DW(DW), .N(N), .CP(CP)) dut
    (.clk(clk), .rst(rst), .in_valid(in_valid), .mod(mod), .in_bits(in_bits),
     .out_valid(out_valid), .out_last(out_last), .out_r(out_r), .out_i(out_i));

  initial clk = 0;
  always #5 clk = ~clk;

  logic [15:0] data_all [0:NWORDS-1];

  int total_cycles = 0;
  int total_checks = 0;
  int total_errors = 0;

  task automatic run_case(input int idx);
    int base, cyc, samp, k;
    logic done_case;
    begin
      base = idx * CASE_STRIDE;

      // reset between cases (not counted), like the study TBs
      in_valid = 1'b0;
      mod = 2'b00;
      in_bits = '0;
      rst = 1'b1;
      @(negedge clk); @(negedge clk);
      rst = 1'b0;

      // native interface: one pulse, payload + mod applied together
      mod = data_all[base][1:0];
      for (k = 1; k <= 12; k = k + 1)
        in_bits[16*(k-1) +: 16] = data_all[base + k];
      in_valid = 1'b1;
      @(negedge clk);
      in_valid = 1'b0;

      cyc = 1;                              // the pulse cycle
      samp = 0;
      done_case = 1'b0;
      while (!done_case) begin
        @(posedge clk);
        if (out_valid) begin
          if (samp < NSAMP) begin
            total_checks += 2;
            if (out_r !== $signed(data_all[base + 13 + 2*samp])) begin
              total_errors += 1;
              if (total_errors <= 10)
                $display("MISMATCH case %0d sample %0d I: got %h exp %h",
                         idx, samp, out_r, data_all[base + 13 + 2*samp]);
            end
            if (out_i !== $signed(data_all[base + 14 + 2*samp])) begin
              total_errors += 1;
              if (total_errors <= 10)
                $display("MISMATCH case %0d sample %0d Q: got %h exp %h",
                         idx, samp, out_i, data_all[base + 14 + 2*samp]);
            end
          end
          samp = samp + 1;
        end
        if (out_last) done_case = 1'b1;
        cyc = cyc + 1;
        if (cyc > WATCHDOG_CYCLES) begin
          $display("TB_CYCLES total=%0d", total_cycles + cyc);
          $display("TB_SUMMARY total=%0d errors=%0d", total_checks + 1, total_errors + 1);
          $fatal(1, "WATCHDOG: case %0d no out_last after %0d cycles", idx, cyc);
        end
        @(negedge clk);
      end

      // exactly N+CP streamed samples expected
      total_checks += 1;
      if (samp != NSAMP) begin
        total_errors += 1;
        $display("MISMATCH case %0d: %0d samples streamed, expected %0d",
                 idx, samp, NSAMP);
      end
      total_cycles += cyc;
    end
  endtask

  int c;
  initial begin
    in_valid = 1'b0;
    mod = 2'b00;
    in_bits = '0;
    rst = 1'b1;

    $readmemh("vectors.dat", data_all);

    for (c = 0; c < NCASES; c = c + 1) begin
      run_case(c);
    end

    $display("TB_CYCLES total=%0d", total_cycles);
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
