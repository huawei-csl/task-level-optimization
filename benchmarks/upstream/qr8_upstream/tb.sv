// Testbench for qr8_upstream: the VERBATIM logikbench upstream CORDIC QR
// at N=8/DW=16 (parameters set here; zero source edits), driven over its
// NATIVE interface -- per case one start pulse with the whole matrix on the
// 1024-bit a_in bus, result read from the 1024-bit r_out bus at the done
// pulse.
//
// Purpose: the upstream's own ARP under the study flow (upstream
// comparison). Same workload as the qr8_1r1w sibling: vectors.dat is
// byte-identical (stride 128 per case: words 0..63 = A row-major, 64..127
// = golden R row-major, 16-bit words; element k of a_in/r_out lives at
// bits [16k +: 16]).
//
// Metering mirrors the study TBs: reset between cases, per-case cycles
// counted from reset release to the done pulse inclusive, summed into
// TB_CYCLES. No memory port -> no TB_READS/TB_WRITES (as with the ALU).
module tb;
  localparam int N = 8;
  localparam int DW = 16;
  localparam int NN = N * N;                // 64 matrix elements
  localparam int CASE_STRIDE = 128;
  localparam int NCASES = 15;
  localparam int NWORDS = NCASES * CASE_STRIDE;
  localparam int WATCHDOG_CYCLES = 200;

  logic clk, reset_n, start, done;
  logic [N*N*DW-1:0] a_in, r_out;

  qr #(.N(N), .DW(DW)) dut
    (.clk(clk), .reset_n(reset_n), .start(start), .a_in(a_in),
     .done(done), .r_out(r_out));

  initial clk = 0;
  always #5 clk = ~clk;

  logic [15:0] data_all [0:NWORDS-1];

  int total_cycles = 0;
  int total_checks = 0;
  int total_errors = 0;

  task automatic run_case(input int idx);
    int base, cyc, k;
    logic done_case;
    begin
      base = idx * CASE_STRIDE;

      // reset between cases (active-low; not counted), like the study TBs
      start = 1'b0;
      a_in = '0;
      reset_n = 1'b0;
      @(negedge clk); @(negedge clk);
      reset_n = 1'b1;

      // native interface: whole matrix on the bus, one start pulse
      for (k = 0; k < NN; k = k + 1)
        a_in[16*k +: 16] = data_all[base + k];
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;

      cyc = 1;                              // the start-pulse cycle
      done_case = 1'b0;
      while (!done_case) begin
        @(posedge clk);
        if (done) begin
          for (k = 0; k < NN; k = k + 1) begin
            total_checks += 1;
            if (r_out[16*k +: 16] !== data_all[base + NN + k]) begin
              total_errors += 1;
              if (total_errors <= 10)
                $display("MISMATCH case %0d elem %0d: got %h exp %h",
                         idx, k, r_out[16*k +: 16], data_all[base + NN + k]);
            end
          end
          done_case = 1'b1;
        end
        cyc = cyc + 1;
        if (cyc > WATCHDOG_CYCLES) begin
          $display("TB_CYCLES total=%0d", total_cycles + cyc);
          $display("TB_SUMMARY total=%0d errors=%0d", total_checks + 1, total_errors + 1);
          $fatal(1, "WATCHDOG: case %0d no done after %0d cycles", idx, cyc);
        end
        @(negedge clk);
      end
      total_cycles += cyc;
    end
  endtask

  int c;
  initial begin
    start = 1'b0;
    a_in = '0;
    reset_n = 1'b0;

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
