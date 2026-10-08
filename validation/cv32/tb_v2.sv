// Validation-v2 testbench for cv32e40p_alu: 2M-vector set, dual mode.
//
// Default (check) mode: reads vectors_v2.dat (14 hex fields, as the campaign
// tb.sv) and checks result_o / comparison_result_o; prints TB_SUMMARY.
// Capture mode (+capture): reads vectors_v2_stim.dat (12 fields, no expected
// outputs), drives the BIT-EXACT STARTING DESIGN, and writes vectors_v2.dat
// with the captured outputs appended. The first 20k stimuli are the original
// campaign vectors; capture correctness is crosschecked against their known
// goldens by the validation runner.
// File paths overridable with +stim=<path> and +vec=<path>.
//
// Protocol identical to the campaign tb.sv (negedge-driven handshake,
// 64-cycle ready cap).
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

  bit capture_mode;
  string stim_path = "vectors_v2_stim.dat";
  string vec_path  = "vectors_v2.dat";
  int fd, fo, n, cycles;
  longint total_cycles;
  int total_checks, total_errors;
  logic [3:0]  cchk;
  logic [31:0] exp_result;
  logic [3:0]  exp_cmp;
  string line;

  initial begin
    capture_mode = $test$plusargs("capture");
    void'($value$plusargs("stim=%s", stim_path));
    void'($value$plusargs("vec=%s", vec_path));

    if (capture_mode) begin
      fd = $fopen(stim_path, "r");
      if (fd == 0) $fatal(1, "cannot open %s", stim_path);
      fo = $fopen(vec_path, "w");
      if (fo == 0) $fatal(1, "cannot open %s for write", vec_path);
      $fwrite(fo, "# op vm bma bmb ive cpx sbr csh a b c cchk exp_result exp_cmp (captured from starting design)\n");
    end else begin
      fd = $fopen(vec_path, "r");
      if (fd == 0) $fatal(1, "cannot open %s", vec_path);
    end

    total_checks = 0;
    total_errors = 0;
    total_cycles = 0;

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
      if (capture_mode) begin
        n = $sscanf(line, "%h %h %h %h %h %h %h %h %h %h %h %h",
                    op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk);
        if (n != 12) continue;
      end else begin
        n = $sscanf(line, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h",
                    op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk,
                    exp_result, exp_cmp);
        if (n != 14) continue;
      end

      @(negedge clk);
      enable_i = 1'b1;
      cycles = 0;
      do begin
        @(negedge clk);
        cycles++;
      end while (ready_o !== 1'b1 && cycles < 64);
      total_cycles += cycles;

      total_checks++;
      if (ready_o !== 1'b1) begin
        $display("TB_ERROR timeout: op=%02h never asserted ready_o within 64 cycles (a=%08h b=%08h)",
                 op, a, b);
        total_errors++;
      end else if (capture_mode) begin
        $fwrite(fo, "%02h %01h %02h %02h %01h %01h %01h %01h %08h %08h %08h %01h %08h %01h\n",
                op, vm, bma, bmb, ive, cpx, sbr, csh, a, b, c, cchk,
                result_o, comparison_result_o);
      end else if (result_o !== exp_result ||
                   (cchk[0] && comparison_result_o !== exp_cmp[0])) begin
        if (total_errors < 50)
          $display("TB_ERROR op=%02h vm=%01h a=%08h b=%08h c=%08h : expected result=%08h cmp=%01h got result=%08h cmp=%01h",
                   op, vm, a, b, c, exp_result, (cchk[0] ? exp_cmp[0] : 1'bx),
                   result_o, comparison_result_o);
        total_errors++;
      end
      enable_i = 1'b0;
    end
    $fclose(fd);
    if (capture_mode) $fclose(fo);

    $display("TB_CYCLES total=%0d", total_cycles);
    $display("TB_SUMMARY total=%0d errors=%0d", total_checks, total_errors);
    if (total_errors != 0) $fatal(1, "FAIL");
    $display("PASS");
    $finish;
  end
endmodule
