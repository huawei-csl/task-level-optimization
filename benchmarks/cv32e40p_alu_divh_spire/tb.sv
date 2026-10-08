// Testbench for cv32e40p_alu — the CV32E40P (RI5CY) execution-stage ALU.
//
// Data-driven: reads vectors.dat, one test case per line, hex fields
//   op vm bma bmb ive cpx sbr csh a b c cchk exp_result exp_cmp
// Expected outputs were captured from the pristine upstream CV32E40P RTL
// (https://github.com/openhwgroup/cv32e40p) and cross-checked against
// independent Python models for all unambiguous op subsets.
//
// Handshake protocol (matches how the EX stage drives the ALU):
//   * rst_n held low for the first 5 cycles (sync or async reset both OK).
//   * Each vector: all inputs driven at a negedge with enable_i=1 and
//     ex_ready_i=1, then held stable; ready_o is polled at each following
//     negedge (cap 64 cycles -> error). When ready_o==1, result_o is checked
//     (and comparison_result_o iff cchk==1 — comparison ops only; for all
//     other ops comparison_result_o is a don't-care). enable_i is then
//     deasserted for one cycle before the next vector.
//   * The reference completes every op except div/rem combinationally
//     (ready_o already high at the first poll); div/rem take up to ~37 cycles.
module tb;
  logic clk = 0;
  always #5 clk = ~clk;

  logic        rst_n;
  logic        enable_i;
  logic [6:0]  op;
  logic [31:0] a, b, c;
  logic [1:0]  vm;
  logic [4:0]  bma, bmb;
  logic [1:0]  ive;
  logic        cpx, sbr;
  logic [1:0]  csh;
  logic [31:0] result_o;
  logic        comparison_result_o;
  logic        ready_o;
  logic        ex_ready_i;

  cv32e40p_alu dut (
      .clk                (clk),
      .rst_n              (rst_n),
      .enable_i           (enable_i),
      .operator_i         (op),
      .operand_a_i        (a),
      .operand_b_i        (b),
      .operand_c_i        (c),
      .vector_mode_i      (vm),
      .bmask_a_i          (bma),
      .bmask_b_i          (bmb),
      .imm_vec_ext_i      (ive),
      .is_clpx_i          (cpx),
      .is_subrot_i        (sbr),
      .clpx_shift_i       (csh),
      .result_o           (result_o),
      .comparison_result_o(comparison_result_o),
      .ready_o            (ready_o),
      .ex_ready_i         (ex_ready_i)
  );

  int fd, n, cycles;
  int total_cycles, div_cycles;
  int total_checks, total_errors;
  logic [3:0]  cchk;
  logic [31:0] exp_result;
  logic [3:0]  exp_cmp;
  string line;

  initial begin
    fd = $fopen("vectors.dat", "r");
    if (fd == 0) $fatal(1, "cannot open vectors.dat");

    total_checks = 0;
    total_errors = 0;
    total_cycles = 0;
    div_cycles  = 0;

    rst_n      = 1'b0;
    enable_i   = 1'b0;
    ex_ready_i = 1'b1;
    op = '0; a = '0; b = '0; c = '0; vm = '0;
    bma = '0; bmb = '0; ive = '0; cpx = '0; sbr = '0; csh = '0;
    repeat (5) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    while (!$feof(fd)) begin
      if ($fgets(line, fd) == 0) break;
      if (line.len() == 0 || line[0] == "#" || line[0] == "\n") continue;
      n = $sscanf(line, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h",
                  op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk,
                  exp_result, exp_cmp);
      if (n != 14) continue;

      @(negedge clk);
      enable_i = 1'b1;
      cycles = 0;
      do begin
        @(negedge clk);
        cycles++;
      end while (ready_o !== 1'b1 && cycles < 64);
      total_cycles += cycles;
      if (op == 7'h30 || op == 7'h31 || op == 7'h32 || op == 7'h33)
        div_cycles += cycles;

      total_checks++;
      if (ready_o !== 1'b1) begin
        $display("TB_ERROR timeout: op=%02h never asserted ready_o within 64 cycles (a=%08h b=%08h)",
                 op, a, b);
        total_errors++;
      end else if (result_o !== exp_result ||
                   (cchk[0] && comparison_result_o !== exp_cmp[0])) begin
        $display("TB_ERROR op=%02h vm=%01h bma=%02h bmb=%02h ive=%01h cpx=%01h sbr=%01h csh=%01h a=%08h b=%08h c=%08h : expected result=%08h cmp=%01h got result=%08h cmp=%01h",
                 op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c,
                 exp_result, (cchk[0] ? exp_cmp[0] : 1'bx),
                 result_o, comparison_result_o);
        total_errors++;
      end
      enable_i = 1'b0;
    end
    $fclose(fd);

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_DIV_CYCLES total=%0d", div_cycles);
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
