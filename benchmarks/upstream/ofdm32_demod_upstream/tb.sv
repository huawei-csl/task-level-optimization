// Testbench for ofdm32_demod_upstream: the VERBATIM (parameter-corrected)
// logikbench upstream RX at N=32/CP=8, driven over its NATIVE interface --
// the noisy time-domain symbol streamed in as in_valid/in_last/in_r/in_i
// beats (one complex sample per cycle, mod held stable for the case), the
// demapped payload returned on one out_valid beat as 192-bit out_bits.
//
// Purpose: the upstream's own ARP under the study flow (upstream
// comparison). Same workload as the ofdm32_demod_1r1w sibling: vectors.dat is
// byte-identical (stride 93 per case: word 0 = mod, 1..80 = noisy input
// samples with word 1+2s = I and 2+2s = Q of sample s, 81..92 = golden
// demapped bit words out_bits[16k +: 16]).
//
// Metering mirrors the study TBs: reset between cases, per-case cycles
// counted from reset release to the out_valid beat inclusive, summed into
// TB_CYCLES. No memory port -> no TB_READS/TB_WRITES (as with the ALU).
module tb;
  localparam int DW = 16;
  localparam int N = 32;
  localparam int CP = 8;
  localparam int NSAMP = N + CP;            // 40 streamed input samples
  localparam int NOUTW = 12;                // golden out_bits words
  localparam int CASE_STRIDE = 93;
  localparam int NCASES = 21;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 4000;

  logic clk, rst, in_valid, in_last;
  logic signed [DW-1:0] in_r, in_i;
  logic [1:0] mod;
  logic out_valid;
  logic [6*N-1:0] out_bits;

  ofdm_rx #(.DW(DW), .N(N), .CP(CP)) dut
    (.clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
     .in_r(in_r), .in_i(in_i), .mod(mod),
     .out_valid(out_valid), .out_bits(out_bits));

  initial clk = 0;
  always #5 clk = ~clk;

  logic [15:0] data_all [0:NWORDS-1];

  int total_cycles = 0;
  int total_checks = 0;
  int total_errors = 0;

  task automatic run_case(input int idx);
    int base, cyc, s, k, nout;
    logic done_case;
    begin
      base = idx * CASE_STRIDE;

      // reset between cases (not counted), like the study TBs
      in_valid = 1'b0;
      in_last = 1'b0;
      in_r = '0;
      in_i = '0;
      mod = data_all[base][1:0];            // held stable for the whole case
      rst = 1'b1;
      @(negedge clk); @(negedge clk);
      rst = 1'b0;

      cyc = 0;
      nout = 0;
      done_case = 1'b0;

      // native interface: stream the 40 noisy samples back-to-back
      for (s = 0; s < NSAMP; s = s + 1) begin
        in_valid = 1'b1;
        in_last = (s == NSAMP - 1);
        in_r = $signed(data_all[base + 1 + 2*s]);
        in_i = $signed(data_all[base + 2 + 2*s]);
        @(posedge clk);
        cyc = cyc + 1;
        if (out_valid) nout = nout + 1;     // must not fire this early
        @(negedge clk);
      end
      in_valid = 1'b0;
      in_last = 1'b0;
      in_r = '0;
      in_i = '0;

      // wait for the demapped payload
      while (!done_case) begin
        @(posedge clk);
        cyc = cyc + 1;
        if (out_valid) begin
          nout = nout + 1;
          for (k = 0; k < NOUTW; k = k + 1) begin
            total_checks += 1;
            if (out_bits[16*k +: 16] !== data_all[base + 81 + k]) begin
              total_errors += 1;
              if (total_errors <= 10)
                $display("MISMATCH case %0d word %0d: got %h exp %h",
                         idx, k, out_bits[16*k +: 16], data_all[base + 81 + k]);
            end
          end
          done_case = 1'b1;
        end
        if (cyc > WATCHDOG_CYCLES) begin
          $display("TB_CYCLES total=%0d", total_cycles + cyc);
          $display("TB_SUMMARY total=%0d errors=%0d", total_checks + 1, total_errors + 1);
          $fatal(1, "WATCHDOG: case %0d no out_valid after %0d cycles", idx, cyc);
        end
        @(negedge clk);
      end

      // exactly one out_valid beat expected
      total_checks += 1;
      if (nout != 1) begin
        total_errors += 1;
        $display("MISMATCH case %0d: %0d out_valid beats, expected 1", idx, nout);
      end
      total_cycles += cyc;
    end
  endtask

  int c;
  initial begin
    in_valid = 1'b0;
    in_last = 1'b0;
    in_r = '0;
    in_i = '0;
    mod = 2'b00;
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
