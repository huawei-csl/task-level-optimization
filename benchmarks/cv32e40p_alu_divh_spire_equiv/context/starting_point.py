"""Spire port of the CV32E40P (RI5CY) execution-stage ALU.

Assign-for-assign translation of the flattened upstream Verilog, with the two
large selection structures expressed through spire's selection-emission
features (see spire.selection_emission) instead of hand-nested mux cascades:
  1. find-first-one: the natural priority-chain loop under
     @selection_topology("tournament") -> balanced first-match tree;
  2. result_o: a switch_ under selection_topology("onehot") over the (disjoint)
     opcode labels -> one-hot AND-OR network (the parallel $pmux form).
Bit-exact vs the Verilog reference on all 20000 testbench vectors.
A plain-cascade sibling (no emission features) is kept at
an earlier mux-cascade variant.
"""

from spire import Component, IORecord, Input, Output, UInt, SInt, Register, Wire, selection_topology
from spire.control_structures import switch_, case_
from spire.expr import Const, mux, cat, fit_type

# ---- opcode encodings (cv32e40p_pkg) ----------------------------------------
OPS = {
    "ADD": 0b0011000, "SUB": 0b0011001, "ADDU": 0b0011010, "SUBU": 0b0011011,
    "ADDR": 0b0011100, "SUBR": 0b0011101, "ADDUR": 0b0011110, "SUBUR": 0b0011111,
    "XOR": 0b0101111, "OR": 0b0101110, "AND": 0b0010101,
    "SRA": 0b0100100, "SRL": 0b0100101, "ROR": 0b0100110, "SLL": 0b0100111,
    "BEXT": 0b0101000, "BEXTU": 0b0101001, "BINS": 0b0101010,
    "BCLR": 0b0101011, "BSET": 0b0101100, "BREV": 0b1001001,
    "FF1": 0b0110110, "FL1": 0b0110111, "CNT": 0b0110100, "CLB": 0b0110101,
    "EXTS": 0b0111110, "EXT": 0b0111111,
    "LTS": 0b0000000, "LTU": 0b0000001, "LES": 0b0000100, "LEU": 0b0000101,
    "GTS": 0b0001000, "GTU": 0b0001001, "GES": 0b0001010, "GEU": 0b0001011,
    "EQ": 0b0001100, "NE": 0b0001101,
    "SLTS": 0b0000010, "SLTU": 0b0000011, "SLETS": 0b0000110, "SLETU": 0b0000111,
    "ABS": 0b0010100, "CLIP": 0b0010110, "CLIPU": 0b0010111,
    "INS": 0b0101101,
    "MIN": 0b0010000, "MINU": 0b0010001, "MAX": 0b0010010, "MAXU": 0b0010011,
    "DIVU": 0b0110000, "DIV": 0b0110001, "REMU": 0b0110010, "REM": 0b0110011,
    "SHUF": 0b0111010, "SHUF2": 0b0111011, "PCKLO": 0b0111000, "PCKHI": 0b0111001,
}
VEC_MODE16 = 0b10
VEC_MODE8 = 0b11


def rep(bit, n):
    """Verilog {n{bit}} replication."""
    return cat(*([bit] * n))


def rev32(x):
    """Full 32-bit bit reversal."""
    return cat(*[x[31 - k] for k in range(32)])


class Cv32e40pAlu(Component):
    def __init__(self):
        self.io = IORecord(
            rst_n=Input(UInt(1)),
            enable_i=Input(UInt(1)),
            operator_i=Input(UInt(7)),
            operand_a_i=Input(UInt(32)),
            operand_b_i=Input(UInt(32)),
            operand_c_i=Input(UInt(32)),
            vector_mode_i=Input(UInt(2)),
            bmask_a_i=Input(UInt(5)),
            bmask_b_i=Input(UInt(5)),
            imm_vec_ext_i=Input(UInt(2)),
            is_clpx_i=Input(UInt(1)),
            is_subrot_i=Input(UInt(1)),
            clpx_shift_i=Input(UInt(2)),
            result_o=Output(UInt(32)),
            comparison_result_o=Output(UInt(1)),
            ready_o=Output(UInt(1)),
            ex_ready_i=Input(UInt(1)),
        )
        self.elaborate()

    def elaborate(self):
        io = self.io
        rst_n = io.rst_n
        op = io.operator_i
        a = io.operand_a_i
        b = io.operand_b_i
        c = io.operand_c_i
        vm = io.vector_mode_i
        bma = io.bmask_a_i
        bmb = io.bmask_b_i
        ive = io.imm_vec_ext_i
        clpx = io.is_clpx_i
        subrot = io.is_subrot_i
        csh = io.clpx_shift_i

        def is_op(*names):
            r = op == Const(OPS[names[0]], UInt(7))
            for n in names[1:]:
                r = r | (op == Const(OPS[n], UInt(7)))
            return r

        vm_is16 = vm == Const(VEC_MODE16, UInt(2))
        vm_is8 = vm == Const(VEC_MODE8, UInt(2))

        # ---------------------------------------------------------------------
        # operand preparation
        # ---------------------------------------------------------------------
        operand_a_neg = Wire(UInt(32))
        operand_a_neg <<= ~a
        operand_a_rev = Wire(UInt(32))
        operand_a_rev <<= rev32(a)
        operand_a_neg_rev = Wire(UInt(32))
        operand_a_neg_rev <<= rev32(operand_a_neg)
        operand_b_neg = Wire(UInt(32))
        operand_b_neg <<= ~b

        # forward declarations (assigned later)
        div_shift = Wire(UInt(6))
        div_valid = Wire(UInt(1))
        bmask = Wire(UInt(32))

        # ---------------------------------------------------------------------
        # partitioned adder
        # ---------------------------------------------------------------------
        adder_op_b_negate = Wire(UInt(1))
        adder_op_b_negate <<= is_op("SUB", "SUBR", "SUBU", "SUBUR") | subrot

        adder_op_a = Wire(UInt(32))
        adder_op_a <<= mux(is_op("ABS"), operand_a_neg,
                           mux(subrot, cat(a[16:32], b[0:16]), a))
        adder_op_b = Wire(UInt(32))
        adder_op_b <<= mux(adder_op_b_negate,
                           mux(subrot, ~cat(b[16:32], a[0:16]), operand_b_neg), b)

        # carry-in bits per byte boundary (verilog always_comb building 36-bit vectors)
        carry_mode = adder_op_b_negate | is_op("ABS", "CLIP")
        one = Const(1, UInt(1))
        zero = Const(0, UInt(1))
        # adder_in_a bits 0/9/18/27 default 1; cleared per vector mode for additions
        in_a0 = one
        in_a9 = mux(carry_mode, one, mux(vm_is8, zero, one))
        in_a18 = mux(carry_mode, one, mux(vm_is16 | vm_is8, zero, one))
        in_a27 = mux(carry_mode, one, mux(vm_is8, zero, one))
        # adder_in_b bits 0/9/18/27 default 0; set per vector mode for sub/abs/clip
        in_b0 = mux(carry_mode, one, zero)
        in_b9 = mux(carry_mode & vm_is8, one, zero)
        in_b18 = mux(carry_mode & (vm_is16 | vm_is8), one, zero)
        in_b27 = mux(carry_mode & vm_is8, one, zero)

        adder_in_a = Wire(UInt(36))
        adder_in_a <<= cat(in_a0, adder_op_a[0:8], in_a9, adder_op_a[8:16],
                           in_a18, adder_op_a[16:24], in_a27, adder_op_a[24:32])
        adder_in_b = Wire(UInt(36))
        adder_in_b <<= cat(in_b0, adder_op_b[0:8], in_b9, adder_op_b[8:16],
                           in_b18, adder_op_b[16:24], in_b27, adder_op_b[24:32])

        # $signed(in_a) + $signed(in_b) -> 37-bit
        adder_result_expanded = Wire(UInt(37))
        adder_result_expanded <<= (fit_type(adder_in_a, SInt(36)) +
                                   fit_type(adder_in_b, SInt(36)))[0:37]
        adder_result = Wire(UInt(32))
        adder_result <<= cat(adder_result_expanded[1:9], adder_result_expanded[10:18],
                             adder_result_expanded[19:27], adder_result_expanded[28:36])

        # rounding stage
        adder_round_value = Wire(UInt(32))
        adder_round_value <<= mux(is_op("ADDR", "SUBR", "ADDUR", "SUBUR"),
                                  cat(bmask[1:32], zero), Const(0, UInt(32)))
        adder_round_result = Wire(UInt(32))
        adder_round_result <<= (adder_result + adder_round_value)[0:32]

        # ---------------------------------------------------------------------
        # shifter
        # ---------------------------------------------------------------------
        shift_amt = Wire(UInt(32))
        shift_amt <<= mux(div_valid, cat(div_shift, Const(0, UInt(26))), b)

        shift_amt_left = Wire(UInt(32))
        shift_amt_left <<= mux(
            vm_is16, cat(shift_amt[16:32], shift_amt[0:16]),
            mux(vm_is8,
                cat(shift_amt[24:32], shift_amt[16:24], shift_amt[8:16], shift_amt[0:8]),
                shift_amt))

        shift_left = Wire(UInt(1))
        shift_left <<= is_op("SLL", "BINS", "FL1", "CLB", "DIV", "DIVU", "REM",
                             "REMU", "BREV")
        shift_use_round = Wire(UInt(1))
        shift_use_round <<= is_op("ADD", "SUB", "ADDR", "SUBR", "ADDU", "SUBU",
                                  "ADDUR", "SUBUR")
        shift_arithmetic = Wire(UInt(1))
        shift_arithmetic <<= is_op("SRA", "BEXT", "ADD", "SUB", "ADDR", "SUBR")

        clpx_shift_ex = cat(csh, Const(0, UInt(14)))  # 16-bit zero-extended
        shift_amt_norm = Wire(UInt(32))
        byte_norm = cat(bmb, Const(0, UInt(3)))       # {3'b000, bmask_b} per byte
        shift_amt_norm <<= mux(clpx, cat(clpx_shift_ex, clpx_shift_ex),
                               cat(byte_norm, byte_norm, byte_norm, byte_norm))

        shift_op_a = Wire(UInt(32))
        shift_op_a <<= mux(shift_left, operand_a_rev,
                           mux(shift_use_round, adder_round_result, a))
        shift_amt_int = Wire(UInt(32))
        shift_amt_int <<= mux(shift_use_round, shift_amt_norm,
                              mux(shift_left, shift_amt_left, shift_amt))

        # right shifts: emulate `$signed({fill, lane}) >>> amt` by appending fill
        # bits at the MSB end and slicing the lane width after a logical ">>".
        fill32 = shift_arithmetic & shift_op_a[31]

        # 32-bit lane (also handles ROR via {a, a})
        op_a_64 = mux(is_op("ROR"), cat(shift_op_a, shift_op_a),
                      cat(shift_op_a, rep(fill32, 32)))
        res32 = (op_a_64 >> shift_amt_int[0:5])[0:32]

        # 16-bit lanes
        def sr16(lane, amt):
            fill = shift_arithmetic & lane[15]
            return (cat(lane, rep(fill, 16)) >> amt)[0:16]

        res16 = cat(sr16(shift_op_a[0:16], shift_amt_int[0:4]),
                    sr16(shift_op_a[16:32], shift_amt_int[16:20]))

        # 8-bit lanes
        def sr8(lane, amt):
            fill = shift_arithmetic & lane[7]
            return (cat(lane, rep(fill, 8)) >> amt)[0:8]

        res8 = cat(sr8(shift_op_a[0:8], shift_amt_int[0:3]),
                   sr8(shift_op_a[8:16], shift_amt_int[8:11]),
                   sr8(shift_op_a[16:24], shift_amt_int[16:19]),
                   sr8(shift_op_a[24:32], shift_amt_int[24:27]))

        shift_right_result = Wire(UInt(32))
        shift_right_result <<= mux(vm_is16, res16, mux(vm_is8, res8, res32))

        shift_left_result = Wire(UInt(32))
        shift_left_result <<= rev32(shift_right_result)

        shift_result = Wire(UInt(32))
        shift_result <<= mux(shift_left, shift_left_result, shift_right_result)

        # ---------------------------------------------------------------------
        # comparison
        # ---------------------------------------------------------------------
        # second == comparator for CLIP
        operand_b_eq = mux(is_op("CLIPU"), Const(0, UInt(32)), operand_b_neg)
        is_equal_clip = a == operand_b_eq

        cmp_signed_ops = is_op("GTS", "GES", "LTS", "LES", "SLTS", "SLETS",
                               "MIN", "MAX", "ABS", "CLIP", "CLIPU")
        # cmp_signed[3:0]: vm8 -> 1111, vm16 -> 1010, vm32 -> 1000 (when signed op)
        cmp_signed = [Wire(UInt(1), name=f"cmp_signed_{i}") for i in range(4)]
        cmp_signed[3] <<= cmp_signed_ops
        cmp_signed[2] <<= cmp_signed_ops & vm_is8
        cmp_signed[1] <<= cmp_signed_ops & (vm_is8 | vm_is16)
        cmp_signed[0] <<= cmp_signed_ops & vm_is8

        is_equal_vec = []
        is_greater_vec = []
        for i in range(4):
            ab = a[8 * i:8 * i + 8]
            bb = b[8 * i:8 * i + 8]
            is_equal_vec.append(ab == bb)
            ax = fit_type(cat(ab, ab[7] & cmp_signed[i]), SInt(9))
            bx = fit_type(cat(bb, bb[7] & cmp_signed[i]), SInt(9))
            is_greater_vec.append(ax > bx)

        eq_all = is_equal_vec[3] & is_equal_vec[2] & is_equal_vec[1] & is_equal_vec[0]
        gt_all = is_greater_vec[3] | (is_equal_vec[3] & (is_greater_vec[2]
                 | (is_equal_vec[2] & (is_greater_vec[1]
                 | (is_equal_vec[1] & is_greater_vec[0])))))
        eq_h0 = is_equal_vec[0] & is_equal_vec[1]
        eq_h1 = is_equal_vec[2] & is_equal_vec[3]
        gt_h0 = is_greater_vec[1] | (is_equal_vec[1] & is_greater_vec[0])
        gt_h1 = is_greater_vec[3] | (is_equal_vec[3] & is_greater_vec[2])

        is_equal = [Wire(UInt(1), name=f"is_equal_{i}") for i in range(4)]
        is_greater = [Wire(UInt(1), name=f"is_greater_{i}") for i in range(4)]
        for i in range(4):
            eq16 = eq_h1 if i >= 2 else eq_h0
            gt16 = gt_h1 if i >= 2 else gt_h0
            is_equal[i] <<= mux(vm_is16, eq16, mux(vm_is8, is_equal_vec[i], eq_all))
            is_greater[i] <<= mux(vm_is16, gt16, mux(vm_is8, is_greater_vec[i], gt_all))

        cmp_result = [Wire(UInt(1), name=f"cmp_result_{i}") for i in range(4)]
        for i in range(4):
            eqi, gti = is_equal[i], is_greater[i]
            cmp_result[i] <<= mux(
                is_op("EQ"), eqi,
                mux(is_op("NE"), ~eqi,
                    mux(is_op("GTS", "GTU"), gti,
                        mux(is_op("GES", "GEU"), gti | eqi,
                            mux(is_op("LTS", "SLTS", "LTU", "SLTU"), ~(gti | eqi),
                                mux(is_op("SLETS", "SLETU", "LES", "LEU"), ~gti,
                                    eqi))))))

        io.comparison_result_o <<= cmp_result[3]

        # min/max/abs
        minmax_b = Wire(UInt(32))
        minmax_b <<= mux(is_op("ABS"), adder_result, b)
        do_min = is_op("MIN", "MINU", "CLIP", "CLIPU")
        sel_minmax = [is_greater[i] ^ do_min for i in range(4)]
        result_minmax = Wire(UInt(32))
        result_minmax <<= cat(*[mux(sel_minmax[i], a[8 * i:8 * i + 8],
                                    minmax_b[8 * i:8 * i + 8]) for i in range(4)])

        # clip
        clip_result = Wire(UInt(32))
        clipu_zero = a[31] | is_equal_clip
        clip_neg = adder_result_expanded[36] | is_equal_clip
        clip_result <<= mux(is_op("CLIPU"),
                            mux(clipu_zero, Const(0, UInt(32)), result_minmax),
                            mux(clip_neg, operand_b_neg, result_minmax))

        # ---------------------------------------------------------------------
        # shuffle / pack
        # ---------------------------------------------------------------------
        # register-select / through tables (first always_comb)
        d_reg_sel = Const(0b0000, UInt(4))
        d_reg1_sel = Const(0b01, UInt(2))
        d_reg0_sel = Const(0b10, UInt(2))
        d_through = Const(0b1111, UInt(4))

        ext_reg_sel = mux(vm_is8, Const(0b1110, UInt(4)), Const(0b1100, UInt(4)))

        shuf2_sel8 = cat(~b[2], ~b[10], ~b[18], ~b[26])
        shuf2_sel16 = cat(~b[1], ~b[1], ~b[17], ~b[17])
        shuf2_reg_sel = mux(vm_is8, shuf2_sel8, mux(vm_is16, shuf2_sel16, d_reg_sel))

        ins_sel8 = mux(ive == 0, Const(0b1110, UInt(4)),
                       mux(ive == 1, Const(0b1101, UInt(4)),
                           mux(ive == 2, Const(0b1011, UInt(4)),
                               Const(0b0111, UInt(4)))))
        ins_sel16 = cat(ive[0], ive[0], ~ive[0], ~ive[0])
        ins_reg_sel = mux(vm_is8, ins_sel8, mux(vm_is16, ins_sel16, d_reg_sel))

        shuffle_reg_sel = Wire(UInt(4))
        shuffle_reg_sel <<= mux(
            is_op("EXT", "EXTS"), ext_reg_sel,
            mux(is_op("PCKLO"), mux(vm_is8, Const(0b0001, UInt(4)), Const(0b0011, UInt(4))),
                mux(is_op("PCKHI"), mux(vm_is8, Const(0b0100, UInt(4)), Const(0b0011, UInt(4))),
                    mux(is_op("SHUF2"), shuf2_reg_sel,
                        mux(is_op("INS"), ins_reg_sel, d_reg_sel)))))

        shuffle_reg1_sel = Wire(UInt(2))
        shuffle_reg1_sel <<= mux(is_op("EXTS"), Const(0b11, UInt(2)),
                                 mux(is_op("EXT"), d_reg1_sel,
                                     mux(is_op("PCKLO", "PCKHI"), Const(0b00, UInt(2)),
                                         d_reg1_sel)))

        shuffle_reg0_sel = Wire(UInt(2))
        shuffle_reg0_sel <<= mux(is_op("INS"),
                                 mux(vm_is8, Const(0b00, UInt(2)),
                                     mux(vm_is16, Const(0b01, UInt(2)), d_reg0_sel)),
                                 d_reg0_sel)

        shuffle_through = Wire(UInt(4))
        shuffle_through <<= mux(is_op("PCKLO") & vm_is8, Const(0b0011, UInt(4)),
                                mux(is_op("PCKHI") & vm_is8, Const(0b1100, UInt(4)),
                                    d_through))

        # byte-selector table (second always_comb); each entry 2 bits
        d_bsel = [Const(0, UInt(2)) for _ in range(4)]

        exts_b8 = [ive for _ in range(4)]
        exts_b16 = [cat(zero, ive[0]), cat(one, ive[0]), cat(one, ive[0]), cat(one, ive[0])]
        pcklo_b16 = [Const(0b00, UInt(2)), Const(0b01, UInt(2)),
                     Const(0b00, UInt(2)), Const(0b01, UInt(2))]
        pckhi_b16 = [Const(0b10, UInt(2)), Const(0b11, UInt(2)),
                     Const(0b10, UInt(2)), Const(0b11, UInt(2))]
        shuf_b8 = [b[0:2], b[8:10], b[16:18], b[24:26]]
        shuf_b16 = [cat(zero, b[0]), cat(one, b[0]), cat(zero, b[16]), cat(one, b[16])]
        ins_b = [Const(0b00, UInt(2)), Const(0b01, UInt(2)),
                 Const(0b10, UInt(2)), Const(0b11, UInt(2))]

        shuffle_byte_sel = []
        for k in range(4):
            sel = mux(
                is_op("EXTS", "EXT"),
                mux(vm_is8, exts_b8[k], mux(vm_is16, exts_b16[k], d_bsel[k])),
                mux(is_op("PCKLO"),
                    mux(vm_is16, pcklo_b16[k], d_bsel[k]),
                    mux(is_op("PCKHI"),
                        mux(vm_is16, pckhi_b16[k], d_bsel[k]),
                        mux(is_op("SHUF2", "SHUF"),
                            mux(vm_is8, shuf_b8[k], mux(vm_is16, shuf_b16[k], d_bsel[k])),
                            mux(is_op("INS"), ins_b[k], d_bsel[k])))))
            w = Wire(UInt(2), name=f"shuffle_byte_sel_{k}")
            w <<= sel
            shuffle_byte_sel.append(w)

        shuffle_r0_in = Wire(UInt(32))
        shuffle_r0_in <<= mux(shuffle_reg0_sel[1], a,
                              mux(shuffle_reg0_sel[0],
                                  cat(a[0:16], a[0:16]),
                                  cat(a[0:8], a[0:8], a[0:8], a[0:8])))
        shuffle_r1_in = Wire(UInt(32))
        shuffle_r1_in <<= mux(shuffle_reg1_sel[1],
                              cat(rep(a[7], 8), rep(a[15], 8), rep(a[23], 8), rep(a[31], 8)),
                              mux(shuffle_reg1_sel[0], c, b))

        def byte_pick(src, sel):
            return mux(sel[1],
                       mux(sel[0], src[24:32], src[16:24]),
                       mux(sel[0], src[8:16], src[0:8]))

        shuffle_r0 = cat(*[byte_pick(shuffle_r0_in, shuffle_byte_sel[k]) for k in range(4)])
        shuffle_r1 = cat(*[byte_pick(shuffle_r1_in, shuffle_byte_sel[k]) for k in range(4)])

        shuffle_result = Wire(UInt(32))
        shuffle_result <<= cat(*[mux(shuffle_reg_sel[k],
                                     shuffle_r1[8 * k:8 * k + 8],
                                     shuffle_r0[8 * k:8 * k + 8]) for k in range(4)])
        pack_result = Wire(UInt(32))
        pack_result <<= cat(*[mux(shuffle_through[k],
                                  shuffle_result[8 * k:8 * k + 8],
                                  c[8 * k:8 * k + 8]) for k in range(4)])

        # ---------------------------------------------------------------------
        # bit count (popcount + find-first-one)
        # ---------------------------------------------------------------------
        # popcount tree (cv32e40p_popcnt)
        cnt_l1 = [(cat(a[2 * l], zero) + cat(a[2 * l + 1], zero))[0:2] for l in range(16)]
        cnt_l2 = [(cat(cnt_l1[2 * m], zero) + cat(cnt_l1[2 * m + 1], zero))[0:3] for m in range(8)]
        cnt_l3 = [(cat(cnt_l2[2 * n], zero) + cat(cnt_l2[2 * n + 1], zero))[0:4] for n in range(4)]
        cnt_l4 = [(cat(cnt_l3[2 * p], zero) + cat(cnt_l3[2 * p + 1], zero))[0:5] for p in range(2)]
        cnt_result = Wire(UInt(6))
        cnt_result <<= (cat(cnt_l4[0], zero) + cat(cnt_l4[1], zero))[0:6]

        # ff_one input mux
        ff_input = Wire(UInt(32))
        ff_input <<= mux(
            is_op("FF1"), a,
            mux(is_op("DIVU", "REMU", "FL1"), operand_a_rev,
                mux(is_op("DIV", "REM", "CLB"),
                    mux(a[31], operand_a_neg_rev, operand_a_rev),
                    Const(0, UInt(32)))))

        # find-first-one (priority from LSB), cv32e40p_ff_one
        # The natural priority-chain loop, restructured to a balanced
        # first-match tree (log2(32)=5 mux levels instead of 32) by the
        # emission library — same reduction upstream cv32e40p_ff_one.sv
        # hand-writes as ~100 lines of generate loops.
        @selection_topology("tournament")
        def _ff1_index(bits):
            chain = Const(0, UInt(5))
            for k in reversed(range(32)):
                chain = mux(bits[k], Const(k, UInt(5)), chain)
            return chain

        ff1_result = Wire(UInt(5))
        ff1_result <<= _ff1_index(ff_input)
        ff_no_one = Wire(UInt(1))
        ff_no_one <<= ff_input == Const(0, UInt(32))

        fl1_result = Wire(UInt(5))
        fl1_result <<= (Const(31, UInt(5)) - ff1_result)[0:5]
        clb_result = Wire(UInt(6))
        clb_result <<= (cat(ff1_result, zero) - Const(1, UInt(6)))[0:6]

        bitop_result = Wire(UInt(6))
        bitop_result <<= mux(
            is_op("FF1"), mux(ff_no_one, Const(32, UInt(6)), cat(ff1_result, zero)),
            mux(is_op("FL1"), mux(ff_no_one, Const(32, UInt(6)), cat(fl1_result, zero)),
                mux(is_op("CNT"), cnt_result,
                    mux(is_op("CLB"),
                        mux(ff_no_one,
                            mux(a[31], Const(31, UInt(6)), Const(0, UInt(6))),
                            clb_result),
                        Const(0, UInt(6))))))

        # ---------------------------------------------------------------------
        # bit manipulation
        # ---------------------------------------------------------------------
        bmask_first = Const(0xFFFFFFFE, UInt(32)) << bma
        bmask <<= ((~bmask_first) << bmb)[0:32]
        bmask_inv = ~bmask

        extract_is_signed = is_op("BEXT")
        extract_sign = extract_is_signed & (shift_result >> bma)[0]

        bextins_and = mux(is_op("BINS"), c, rep(extract_sign, 32))
        bextins_result = Wire(UInt(32))
        bextins_result <<= (bmask & shift_result) | (bextins_and & bmask_inv)

        bclr_result = Wire(UInt(32))
        bclr_result <<= a & bmask_inv
        bset_result = Wire(UInt(32))
        bset_result <<= a | bmask

        # bit reverse (radix 2/4/8 of shift_result)
        radix_2_rev = rev32(shift_result)
        radix_4_rev = cat(*[shift_result[30 - 2 * j:32 - 2 * j] for j in range(16)])
        radix_8_rev = cat(*([shift_result[29 - 3 * j:32 - 3 * j] for j in range(10)]
                            + [Const(0, UInt(2))]))
        reverse_result = Wire(UInt(32))
        radix_sel = bma[0:2]
        reverse_result <<= mux(radix_sel == Const(1, UInt(2)), radix_4_rev,
                               mux(radix_sel == Const(2, UInt(2)), radix_8_rev,
                                   radix_2_rev))

        # ---------------------------------------------------------------------
        # serial divider (cv32e40p_alu_div, C_WIDTH=32)
        # ---------------------------------------------------------------------
        div_signed = op[0]
        div_op_a_signed = a[31] & div_signed
        div_shift_int = mux(ff_no_one, Const(31, UInt(6)), clb_result)
        div_shift <<= (div_shift_int +
                       mux(div_op_a_signed, Const(0, UInt(6)), Const(1, UInt(6))))[0:6]
        div_valid <<= io.enable_i & is_op("DIV", "DIVU", "REM", "REMU")

        # divider I/O mapping (inputs A and B are swapped at the ALU level)
        d_opa = b                      # OpA_DI
        d_opb = shift_left_result      # OpB_DI
        d_opb_shift = div_shift        # OpBShift_DI
        d_opb_is_zero = cnt_result == Const(0, UInt(6))  # OpBIsZero_SI
        d_opb_sign = div_op_a_signed   # OpBSign_SI
        d_opcode = op[0:2]             # OpCode_SI
        d_in_vld = div_valid
        d_out_rdy = io.ex_ready_i

        # state encoding: IDLE=0, DIVIDE=1, FINISH=2
        state = Register(UInt(2), name="div_state")
        areg = Register(UInt(32), name="div_areg")
        breg = Register(UInt(32), name="div_breg")
        resreg = Register(UInt(32), name="div_resreg")
        cnt = Register(UInt(6), name="div_cnt")
        remsel = Register(UInt(1), name="div_remsel")
        compinv = Register(UInt(1), name="div_compinv")
        resinv = Register(UInt(1), name="div_resinv")

        st_idle = state == Const(0, UInt(2))
        st_divide = state == Const(1, UInt(2))
        st_finish = state == Const(2, UInt(2))

        # FSM outputs (combinational)
        load_en = st_idle & d_in_vld
        out_vld = (st_idle & ~d_in_vld) | st_finish
        areg_en_fsm = st_divide  # gated by ABComp below
        breg_en = load_en | st_divide
        resreg_en = st_divide

        cnt_zero = cnt == Const(0, UInt(6))
        state_next = mux(st_idle, mux(d_in_vld, Const(1, UInt(2)), Const(0, UInt(2))),
                         mux(st_divide, mux(cnt_zero, Const(2, UInt(2)), Const(1, UInt(2))),
                             mux(st_finish, mux(d_out_rdy, Const(0, UInt(2)), Const(2, UInt(2))),
                                 Const(0, UInt(2)))))

        # datapath
        pm_sel = load_en & ~(d_opcode[0] & (d_opa[31] ^ d_opb_sign))
        add_mux = mux(load_en, d_opa, breg)
        b_mux = mux(load_en, d_opb, cat(breg[1:32], compinv))
        resreg_rev = rev32(resreg)
        out_mux = mux(remsel, areg, resreg_rev)
        res_do = mux(resinv, (Const(0, UInt(32)) - out_mux)[0:32], out_mux)
        ab_comp = ((areg == breg) | ((areg > breg) ^ compinv)) & \
                  ((areg != Const(0, UInt(32))) | d_opb_is_zero)
        add_tmp = mux(load_en, Const(0, UInt(32)), areg)
        add_out = mux(pm_sel, (add_tmp + add_mux)[0:32], (add_tmp - add_mux)[0:32])

        cnt_next = mux(load_en, d_opb_shift,
                       mux(~cnt_zero, (cnt - Const(1, UInt(6)))[0:6], cnt))

        remsel_next = mux(load_en, d_opcode[1], remsel)
        compinv_next = mux(load_en, d_opb_sign, compinv)
        resinv_next = mux(load_en,
                          (~d_opb_is_zero | d_opcode[1]) & d_opcode[0] &
                          (d_opa[31] ^ d_opb_sign),
                          resinv)

        areg_next = mux(load_en, add_out, mux(areg_en_fsm & ab_comp, add_out, areg))
        breg_next = mux(breg_en, b_mux, breg)
        resreg_next = mux(load_en, Const(0, UInt(32)),
                          mux(resreg_en, cat(resreg[1:32], ab_comp), resreg))

        # registers with manual synchronous active-low reset
        state <<= mux(rst_n, state_next, Const(0, UInt(2)))
        areg <<= mux(rst_n, areg_next, Const(0, UInt(32)))
        breg <<= mux(rst_n, breg_next, Const(0, UInt(32)))
        resreg <<= mux(rst_n, resreg_next, Const(0, UInt(32)))
        cnt <<= mux(rst_n, cnt_next, Const(0, UInt(6)))
        remsel <<= mux(rst_n, remsel_next, Const(0, UInt(1)))
        compinv <<= mux(rst_n, compinv_next, Const(0, UInt(1)))
        resinv <<= mux(rst_n, resinv_next, Const(0, UInt(1)))

        result_div = res_do
        div_ready = out_vld

        # ---------------------------------------------------------------------
        # result mux
        # ---------------------------------------------------------------------
        cmp_bytes = cat(rep(cmp_result[0], 8), rep(cmp_result[1], 8),
                        rep(cmp_result[2], 8), rep(cmp_result[3], 8))
        sltx_result = cat(cmp_result[3], Const(0, UInt(31)))
        abs_result = mux(clpx, cat(a[0:16], adder_result[16:32]), result_minmax)
        bitop32 = cat(bitop_result, Const(0, UInt(26)))

        # Result selection as a switch_ over the opcode: the 57 labels are
        # distinct constants, so selection_topology("onehot") lowers this to the one-hot
        # AND-OR network a Verilog `case` becomes via $pmux (log depth)
        # instead of a 16-deep serial mux cascade. Unmatched opcodes fall
        # through to the pre-assigned 0, like the reference's case default.
        io.result_o <<= Const(0, UInt(32))
        with selection_topology("onehot"), switch_(op):
            with case_(OPS["AND"]):
                io.result_o <<= a & b
            with case_(OPS["OR"]):
                io.result_o <<= a | b
            with case_(OPS["XOR"]):
                io.result_o <<= a ^ b
            with case_(OPS["ADD"], OPS["ADDR"], OPS["ADDU"], OPS["ADDUR"],
                       OPS["SUB"], OPS["SUBR"], OPS["SUBU"], OPS["SUBUR"],
                       OPS["SLL"], OPS["SRL"], OPS["SRA"], OPS["ROR"]):
                io.result_o <<= shift_result
            with case_(OPS["BINS"], OPS["BEXT"], OPS["BEXTU"]):
                io.result_o <<= bextins_result
            with case_(OPS["BCLR"]):
                io.result_o <<= bclr_result
            with case_(OPS["BSET"]):
                io.result_o <<= bset_result
            with case_(OPS["BREV"]):
                io.result_o <<= reverse_result
            with case_(OPS["SHUF"], OPS["SHUF2"], OPS["PCKLO"], OPS["PCKHI"],
                       OPS["EXT"], OPS["EXTS"], OPS["INS"]):
                io.result_o <<= pack_result
            with case_(OPS["MIN"], OPS["MINU"], OPS["MAX"], OPS["MAXU"]):
                io.result_o <<= result_minmax
            with case_(OPS["ABS"]):
                io.result_o <<= abs_result
            with case_(OPS["CLIP"], OPS["CLIPU"]):
                io.result_o <<= clip_result
            with case_(OPS["EQ"], OPS["NE"], OPS["GTU"], OPS["GEU"],
                       OPS["LTU"], OPS["LEU"], OPS["GTS"], OPS["GES"],
                       OPS["LTS"], OPS["LES"]):
                io.result_o <<= cmp_bytes
            with case_(OPS["SLTS"], OPS["SLTU"], OPS["SLETS"], OPS["SLETU"]):
                io.result_o <<= sltx_result
            with case_(OPS["FF1"], OPS["FL1"], OPS["CLB"], OPS["CNT"]):
                io.result_o <<= bitop32
            with case_(OPS["DIV"], OPS["DIVU"], OPS["REM"], OPS["REMU"]):
                io.result_o <<= result_div

        io.ready_o <<= div_ready


Cv32e40pAlu().to_verilog_file("design.v", name="cv32e40p_alu",
                              with_clock=True, with_reset=False)
