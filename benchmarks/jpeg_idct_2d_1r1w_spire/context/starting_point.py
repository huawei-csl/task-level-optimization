"""Spire starting point for jpeg_idct_2d_1r1w — the flexible-schedule 8x8 2-D IDCT
with harness-owned memories. The compute core is the bit-exact spire port of the
core_jpeg composition jpeg_idct_x -> jpeg_idct_transpose -> jpeg_idct_y (see the
jpeg_idct_2d benchmarks); around it, ``JpegIdct2D1r1w`` implements the SIMPLEST
correct schedule: load all 64 coefficients into a register buffer (one read per
cycle, 1-cycle registered read), play the fixed 64-beat schedule into the
pipeline, and write each pixel as it streams out (row-major address from the
pipeline's output index). NO load/compute overlap, NO read reuse — discovering
better schedules is exactly what the benchmark measures. Modify it.

Structure mirrors the RTL 1:1:
  - ``JpegIdct1D``: the AAN-style 1-D pass, parameterized exactly like the RTL
    (x: OUT_SHIFT=11, 16-bit sign-extended inputs; y: OUT_SHIFT=15, 32-bit inputs,
    plus the un-transpose output-index permutation {i[2:0], i[5:3]}).
  - ``JpegIdctTranspose``: the double-buffered corner-turn — 4 RAMs (32x32,
    1 write + 1 registered read port, readFirst) split by row pair, IDLE/SETUP/
    ACTIVE readout FSM with the +/-8 stride that serves columns as alternating
    even/odd row sets.
  - ``JpegIdct2D``: the composition with downstream ready tied high (the x-pass
    accepts no backpressure), exactly as in the original jpeg_idct.v.

Verilog width semantics reproduced exactly: every arithmetic expression wraps in
32-bit signed context (`_s32`), `>>> k` is an arithmetic shift (`_sra`), and
`(x * 181) / 256` is a wrapped 32-bit multiply followed by SIGNED division
truncating toward zero (`_div256_trunc`).

Sync active-high reset: the harness drives `rst_i`, so with_reset=False and every
register is gated with `mux(rst, init, next)`. The emitted clock is renamed to
`clk_i` (the port name the testbench binds to).
"""
from spire import Component, IORecord, Input, Output, UInt
from spire.expr import Const, Register, SInt, cat, mux, reinterpret, s_ext

C1_16 = 4017   # cos( pi/16) x4096
C2_16 = 3784   # cos(2pi/16) x4096
C3_16 = 3406   # cos(3pi/16) x4096
C4_16 = 2896   # cos(4pi/16) x4096
C5_16 = 2276   # cos(5pi/16) x4096
C6_16 = 1567   # cos(6pi/16) x4096
C7_16 = 799    # cos(7pi/16) x4096


def _s32(x):
    """Truncate a signed expression to SInt(32) — Verilog's 32-bit context wrap."""
    w = x.typ.width
    if w == 32:
        return x if x.typ.signed else reinterpret(x, SInt(32))
    if w < 32:
        return s_ext(x, 32)
    return reinterpret(x[0:32], SInt(32))


def _sra(x32, k):
    """Arithmetic shift right of an SInt(32) by constant k, 32-bit result."""
    return reinterpret(s_ext(x32, 32 + k)[k:32 + k], SInt(32))


def _div256_trunc(x32):
    """Verilog `x / 256` on signed 32-bit: truncate toward zero."""
    neg = x32 < Const(0, SInt(32))
    adj = mux(neg, Const(255, SInt(10)), Const(0, SInt(10)))
    s = s_ext(x32, 33) + adj                       # exact, no wrap (34 bits)
    return reinterpret(s_ext(s, 40)[8:40], SInt(32))


class JpegIdct1D(Component):
    """One 1-D 8-point IDCT pass (jpeg_idct_x / jpeg_idct_y, selected by params)."""

    def __init__(self, out_shift: int, input_width: int, unconv_idx: bool, prefix: str):
        self.out_shift = out_shift
        self.input_width = input_width
        self.unconv_idx = unconv_idx
        self.prefix = prefix               # unique register-name prefix per instance
        self.io = IORecord(
            rst=Input(UInt(1)),
            img_start=Input(UInt(1)),
            in_valid=Input(UInt(1)),
            in_data0=Input(UInt(input_width)),
            in_data1=Input(UInt(input_width)),
            in_data2=Input(UInt(input_width)),
            in_data3=Input(UInt(input_width)),
            in_idx=Input(UInt(3)),
            out_valid=Output(UInt(1)),
            out_data=Output(UInt(32)),
            out_idx=Output(UInt(6)),
        )
        self.elaborate()

    def elaborate(self):
        io = self.io
        rst = io.rst
        start = io.img_start
        pf = self.prefix

        def to_s32(d):
            if self.input_width == 16:
                return s_ext(reinterpret(d, SInt(16)), 32)
            return reinterpret(d, SInt(32))

        b0, b1, b2, b3 = (to_s32(io.in_data0), to_s32(io.in_data1),
                          to_s32(io.in_data2), to_s32(io.in_data3))
        idx = io.in_idx

        def at(k):
            return idx == Const(k, UInt(3))

        def sc(v):
            return Const(v, SInt(32))

        def sreg(name):
            return Register(SInt(32), name=f"{pf}_{name}")

        def clock(reg, next_val, init=0):
            reg <<= mux(rst, sc(init), next_val)

        # ---- input stage: operand/coefficient loading (idx-scheduled) --------
        i0 = sreg("i0")
        mul_a = [sreg(f"mul{i}_a") for i in range(5)]
        mul_b = [sreg(f"mul{i}_b") for i in range(5)]

        clock(i0, mux(at(0), _s32(b0 + b2), mux(at(2), _s32(b0 - b2), i0)))
        clock(mul_a[0], mux(at(0), b1, mux(at(1) | at(3) | at(4), b0, mux(at(5), b1, mul_a[0]))))
        clock(mul_b[0], mux(at(0), sc(C2_16),
                        mux(at(1), sc(C1_16),
                        mux(at(3) | at(4), sc(C7_16),
                        mux(at(5), sc(C6_16), mul_b[0])))))
        clock(mul_a[1], mux(at(0) | at(1) | at(3) | at(4) | at(5), b3, mul_a[1]))
        clock(mul_b[1], mux(at(0), sc(C6_16),
                        mux(at(1), sc(C7_16),
                        mux(at(3) | at(4), sc(C1_16),
                        mux(at(5), sc(C2_16), mul_b[1])))))
        clock(mul_a[2], mux(at(1) | at(3) | at(4), b2, mul_a[2]))
        clock(mul_b[2], mux(at(1), sc(C5_16), mux(at(3) | at(4), sc(C3_16), mul_b[2])))
        clock(mul_a[3], mux(at(1) | at(3) | at(4), b1, mul_a[3]))
        clock(mul_b[3], mux(at(1), sc(C3_16), mux(at(3) | at(4), sc(C5_16), mul_b[3])))
        clock(mul_a[4], mux(at(1) | at(5), i0, mul_a[4]))
        clock(mul_b[4], mux(at(1) | at(5), sc(C4_16), mul_b[4]))

        # ---- multiply stage + one extra pipeline register --------------------
        mul_q = [sreg(f"mul{i}_q") for i in range(5)]
        mul = [sreg(f"mul{i}") for i in range(5)]
        for i in range(5):
            clock(mul_q[i], _s32(mul_a[i] * mul_b[i]))
            clock(mul[i], mul_q[i])

        # ---- valid/idx pipeline (stages 0..3) --------------------------------
        st_v = [Register(UInt(1), name=f"{pf}_stg{i}_valid_q") for i in range(4)]
        st_i = [Register(UInt(3), name=f"{pf}_stg{i}_idx_q") for i in range(4)]
        st_v[0] <<= mux(rst, Const(0, UInt(1)), io.in_valid)
        st_i[0] <<= mux(rst, Const(0, UInt(3)), idx)
        for i in range(1, 4):
            st_v[i] <<= mux(rst, Const(0, UInt(1)), st_v[i - 1])
            st_i[i] <<= mux(rst, Const(0, UInt(3)), st_i[i - 1])

        # ---- butterfly register file (keyed on stage-2 idx) -------------------
        def at2(k):
            return st_i[2] == Const(k, UInt(3))

        o_s5, o_s6, o_s7 = sreg("o_s5"), sreg("o_s6"), sreg("o_s7")
        o_t = [sreg(f"o_t{i}") for i in range(8)]
        o_t6_5, o_t5_6 = sreg("o_t6_5"), sreg("o_t5_6")
        dflt = st_i[2] == Const(7, UInt(3))

        clock(o_s5, mux(at2(4), _s32(mul[2] - mul[3]),
                    mux(dflt, _div256_trunc(_s32(o_t6_5 * sc(181))), o_s5)))
        clock(o_s6, mux(at2(1), _s32(mul[2] + mul[3]),
                    mux(dflt, _div256_trunc(_s32(o_t5_6 * sc(181))), o_s6)))
        clock(o_s7, mux(at2(1), _s32(mul[0] + mul[1]), o_s7))
        clock(o_t[0], mux(at2(1), mul[4],
                      mux(at2(2), _s32(o_t[0] + o_t[3]),
                      mux(at2(4), _s32(mul[0] - mul[1]), o_t[0]))))
        clock(o_t[1], mux(at2(6), _s32(o_t[4] + o_t[3]), o_t[1]))
        clock(o_t[2], mux(at2(6), _s32(o_t[4] - o_t[3]), o_t[2]))
        clock(o_t[3], mux(at2(0), _s32(mul[0] + mul[1]),
                      mux(at2(2), _s32(o_t[0] - o_t[3]),
                      mux(at2(5), _s32(mul[0] - mul[1]), o_t[3]))))
        clock(o_t[4], mux(at2(3), _s32(_s32(mul[0] - mul[1]) + _s32(mul[2] - mul[3])),
                      mux(at2(5), mul[4], o_t[4])))
        clock(o_t[5], mux(at2(5), _s32(o_t[0] - o_s5), o_t[5]))
        clock(o_t[6], mux(at2(5), _s32(o_s7 - o_s6), o_t[6]))
        clock(o_t[7], mux(at2(2), _s32(o_s6 + o_s7), o_t[7]))
        clock(o_t6_5, mux(at2(6), _s32(o_t[6] - o_t[5]), o_t6_5))
        clock(o_t5_6, mux(at2(6), _s32(o_t[5] + o_t[6]), o_t5_6))

        # ---- output block registers (stage-3, valid-gated) --------------------
        def at3(k):
            return (st_v[3] == Const(1, UInt(1))) & (st_i[3] == Const(k, UInt(3)))

        bo = [sreg(f"block_out{i}") for i in range(8)]
        bo_tmp = sreg("block_out_tmp")
        sh = self.out_shift
        clock(bo[0], mux(at3(3), _sra(_s32(o_t[0] + o_t[7]), sh), bo[0]))
        clock(bo_tmp, mux(at3(3), _sra(_s32(o_t[0] - o_t[7]), sh), bo_tmp))
        clock(bo[3], mux(at3(3), _sra(_s32(o_t[3] + o_t[4]), sh), bo[3]))
        clock(bo[4], mux(at3(3), _sra(_s32(o_t[3] - o_t[4]), sh), bo[4]))
        clock(bo[7], mux(at3(6), bo_tmp, bo[7]))
        clock(bo[2], mux(at3(7), _sra(_s32(o_t[2] + o_s5), sh), bo[2]))
        clock(bo[5], mux(at3(7), _sra(_s32(o_t[2] - o_s5), sh), bo[5]))
        clock(bo[1], mux(at3(7), _sra(_s32(o_t[1] + o_s6), sh), bo[1]))
        clock(bo[6], mux(at3(7), _sra(_s32(o_t[1] - o_s6), sh), bo[6]))

        # ---- output streamer ---------------------------------------------------
        valid_q = Register(UInt(8), name=f"{pf}_valid_q")
        valid_q <<= mux(rst, Const(0, UInt(8)),
                    mux(start, Const(0, UInt(8)), cat(st_v[3], valid_q[0:7])))
        out_valid = valid_q[6]

        ptr_q = Register(UInt(6), name=f"{pf}_ptr_q")
        ptr_q <<= mux(rst, Const(0, UInt(6)),
                  mux(start, Const(0, UInt(6)),
                  mux(out_valid, ptr_q + Const(1, UInt(6)), ptr_q)))

        sel = ptr_q[0:3]
        data = bo[7]
        for i in range(6, -1, -1):
            data = mux(sel == Const(i, UInt(3)), bo[i], data)

        io.out_valid <<= out_valid
        io.out_data <<= reinterpret(data, UInt(32))
        # y-pass: un-transpose permutation {ptr[2:0], ptr[5:3]}; x-pass: raw ptr
        io.out_idx <<= cat(ptr_q[3:6], ptr_q[0:3]) if self.unconv_idx else ptr_q


class JpegIdctTranspose(Component):
    """Double-buffered 8x8 corner-turn (jpeg_idct_transpose + its 4 RAMs)."""

    def __init__(self):
        self.io = IORecord(
            rst=Input(UInt(1)),
            img_start=Input(UInt(1)),
            in_valid=Input(UInt(1)),
            in_data=Input(UInt(32)),
            in_idx=Input(UInt(6)),
            out_ready=Input(UInt(1)),
            in_accept=Output(UInt(1)),
            out_valid=Output(UInt(1)),
            out_data0=Output(UInt(32)),
            out_data1=Output(UInt(32)),
            out_data2=Output(UInt(32)),
            out_data3=Output(UInt(32)),
            out_idx=Output(UInt(3)),
        )
        self.elaborate()

    def elaborate(self):
        io = self.io
        rst = io.rst
        start = io.img_start

        ST_IDLE, ST_SETUP, ST_ACTIVE = 0, 1, 2
        block_wr = Register(UInt(1), name="t_block_wr_q")
        block_rd = Register(UInt(1), name="t_block_rd_q")
        block_ready = [Register(UInt(1), name=f"t_block_ready{i}_q") for i in range(2)]
        state = Register(UInt(2), name="t_state_q")
        rd_idx = Register(UInt(6), name="t_rd_idx_q")
        rd_addr = Register(UInt(4), name="t_rd_addr_q")

        ready_now = mux(block_rd == Const(1, UInt(1)), block_ready[1], block_ready[0])
        in_accept = ~mux(block_wr == Const(1, UInt(1)), block_ready[1], block_ready[0])
        out_valid = state == Const(ST_ACTIVE, UInt(2))

        # ---- the 4 corner-turn RAMs (row pairs 2n/2n+1) -----------------------
        # Modeled as explicit 32x32 register files with a registered read-first
        # read port — the exact semantics of the original dual-port RAM (which
        # this flow memory-maps to flops anyway; no reset, like the original).
        wr_ptr = cat(io.in_idx[0:4], block_wr)              # {block_wr, idx[3:0]}
        rd_ptr = cat(rd_addr, block_rd)                     # {block_rd, rd_addr}
        wr_beat = (io.in_valid == Const(1, UInt(1))) & (in_accept == Const(1, UInt(1)))

        def corner_ram(n):
            wr_en = wr_beat & (io.in_idx[4:6] == Const(n, UInt(2)))
            cells = [Register(UInt(32), name=f"t_ram{n}_c{i}") for i in range(32)]
            for i in range(32):
                hit = wr_en & (wr_ptr == Const(i, UInt(5)))
                cells[i] <<= mux(hit, io.in_data, cells[i])
            rd = cells[31]
            for i in range(30, -1, -1):
                rd = mux(rd_ptr == Const(i, UInt(5)), cells[i], rd)
            rd_q = Register(UInt(32), name=f"t_ram{n}_rd_q")
            rd_q <<= rd                                     # registered read, read-first
            return rd_q

        ram_rd = [corner_ram(n) for n in range(4)]

        # ---- input/output block bookkeeping ------------------------------------
        set_beat = wr_beat & (io.in_idx == Const(63, UInt(6)))       # block fully written
        clr_beat = (out_valid == Const(1, UInt(1))) & (rd_idx == Const(63, UInt(6)))
        for i in range(2):
            bit = Const(i, UInt(1))
            nxt = mux(clr_beat & (block_rd == bit), Const(0, UInt(1)),
                  mux(set_beat & (block_wr == bit), Const(1, UInt(1)), block_ready[i]))
            block_ready[i] <<= mux(rst | start, Const(0, UInt(1)), nxt)
        block_wr <<= mux(rst | start, Const(0, UInt(1)),
                     mux(set_beat, ~block_wr, block_wr))
        block_rd <<= mux(rst | start, Const(0, UInt(1)),
                     mux(clr_beat, ~block_rd, block_rd))

        # ---- readout FSM ---------------------------------------------------------
        is_idle = state == Const(ST_IDLE, UInt(2))
        is_setup = state == Const(ST_SETUP, UInt(2))
        is_active = out_valid
        nxt_state = mux(is_idle, mux(ready_now & io.out_ready, Const(ST_SETUP, UInt(2)),
                                     Const(ST_IDLE, UInt(2))),
                    mux(is_setup, Const(ST_ACTIVE, UInt(2)),
                    mux(is_active, mux(rd_idx == Const(63, UInt(6)),
                                       Const(ST_IDLE, UInt(2)), Const(ST_ACTIVE, UInt(2))),
                        state)))
        state <<= mux(rst, Const(ST_IDLE, UInt(2)),
                  mux(start, Const(ST_IDLE, UInt(2)), nxt_state))

        rd_idx <<= mux(rst | start, Const(0, UInt(6)),
                   mux(is_active, rd_idx + Const(1, UInt(6)), rd_idx))

        k = rd_idx[0:3]

        def kat(v):
            return k == Const(v, UInt(3))

        step = mux(kat(0), rd_addr - Const(8, UInt(4)),
               mux(kat(1), rd_addr + Const(8, UInt(4)),
               mux(kat(2), rd_addr,
               mux(kat(3), rd_addr - Const(8, UInt(4)),
               mux(kat(4), rd_addr + Const(8, UInt(4)),
               mux(kat(5), rd_addr - Const(8, UInt(4)),
               mux(kat(6), rd_addr + Const(1, UInt(4)),
                           rd_addr + Const(8, UInt(4)))))))))
        rd_addr <<= mux(rst, Const(0, UInt(4)),
                    mux(is_idle, Const(0, UInt(4)),
                    mux(is_setup, Const(8, UInt(4)),
                    mux(is_active, step, rd_addr))))

        io.in_accept <<= in_accept
        io.out_valid <<= out_valid
        io.out_idx <<= k
        io.out_data0 <<= ram_rd[0]
        io.out_data1 <<= ram_rd[1]
        io.out_data2 <<= ram_rd[2]
        io.out_data3 <<= ram_rd[3]


class JpegIdct2D(Component):
    """x-pass -> transpose -> y-pass, downstream ready tied high (as in jpeg_idct.v)."""

    def __init__(self):
        self.io = IORecord(
            rst_i=Input(UInt(1)),
            img_start_i=Input(UInt(1)),
            img_end_i=Input(UInt(1)),        # unused (as in the original RTL)
            inport_valid_i=Input(UInt(1)),
            inport_data0_i=Input(UInt(16)),
            inport_data1_i=Input(UInt(16)),
            inport_data2_i=Input(UInt(16)),
            inport_data3_i=Input(UInt(16)),
            inport_idx_i=Input(UInt(3)),
            outport_valid_o=Output(UInt(1)),
            outport_data_o=Output(UInt(32)),
            outport_idx_o=Output(UInt(6)),
        )
        self.elaborate()

    def elaborate(self):
        io = self.io
        x = JpegIdct1D(out_shift=11, input_width=16, unconv_idx=False, prefix="x")
        t = JpegIdctTranspose()
        y = JpegIdct1D(out_shift=15, input_width=32, unconv_idx=True, prefix="y")

        for sub in (x, t, y):
            sub.io.rst <<= io.rst_i
            sub.io.img_start <<= io.img_start_i

        x.io.in_valid <<= io.inport_valid_i
        x.io.in_data0 <<= io.inport_data0_i
        x.io.in_data1 <<= io.inport_data1_i
        x.io.in_data2 <<= io.inport_data2_i
        x.io.in_data3 <<= io.inport_data3_i
        x.io.in_idx <<= io.inport_idx_i

        t.io.in_valid <<= x.io.out_valid
        t.io.in_data <<= x.io.out_data
        t.io.in_idx <<= x.io.out_idx
        t.io.out_ready <<= Const(1, UInt(1))

        y.io.in_valid <<= t.io.out_valid
        y.io.in_data0 <<= t.io.out_data0
        y.io.in_data1 <<= t.io.out_data1
        y.io.in_data2 <<= t.io.out_data2
        y.io.in_data3 <<= t.io.out_data3
        y.io.in_idx <<= t.io.out_idx

        io.outport_valid_o <<= y.io.out_valid
        io.outport_data_o <<= y.io.out_data
        io.outport_idx_o <<= y.io.out_idx




class JpegIdct2D1r1w(Component):
    """Simplest correct schedule: LOAD (64 reads) -> PLAY (64 beats) -> stream writes.

    Fairness of the recast: the untouched upstream core consumes four 16-bit
    lanes per beat and re-samples each word on ~4 beats (shared multipliers),
    so a serial 1R/1W port cannot be wired through -- any adapter must buffer
    (at least a row) or re-read each word ~4x. This wrapper is only that
    mandatory contract I/O in its simplest correct form; leaner overlapped
    schedules are deliberate optimization headroom, equal for both tracks.
    """

    def __init__(self):
        self.io = IORecord(
            rst_i=Input(UInt(1)),        # plain data input (manual sync reset); renamed to `rst` at emit
            rd_data=Input(UInt(16)),
            rd_en=Output(UInt(1)),
            rd_addr=Output(UInt(6)),
            wr_en=Output(UInt(1)),
            wr_addr=Output(UInt(6)),
            wr_data=Output(UInt(32)),
            done=Output(UInt(1)),
        )
        self.elaborate()

    def elaborate(self):
        io = self.io
        rst = io.rst_i
        ST_LOAD, ST_PLAY, ST_WAIT = 0, 1, 2

        state = Register(UInt(2), name="c_state_q")
        ld = Register(UInt(7), name="c_ld_q")
        play = Register(UInt(6), name="c_play_q")
        wcnt = Register(UInt(7), name="c_wcnt_q")
        done_q = Register(UInt(1), name="c_done_q")
        cap_val = Register(UInt(1), name="c_cap_val_q")
        cap_idx = Register(UInt(6), name="c_cap_idx_q")

        in_load = state == Const(ST_LOAD, UInt(2))
        in_play = state == Const(ST_PLAY, UInt(2))
        ld_issue = in_load & (ld < Const(64, UInt(7)))

        io.rd_en <<= ld_issue
        io.rd_addr <<= ld[0:6]

        cap_val <<= mux(rst, Const(0, UInt(1)), ld_issue)
        cap_idx <<= mux(rst, Const(0, UInt(6)), ld[0:6])

        # coefficient buffer (not reset — fully written before use each case)
        buf = [Register(UInt(16), name=f"c_buf{i}") for i in range(64)]
        for i in range(64):
            hit = (cap_val == Const(1, UInt(1))) & (cap_idx == Const(i, UInt(6)))
            buf[i] <<= mux(hit, io.rd_data, buf[i])

        # FSM
        load_done = (ld == Const(64, UInt(7))) & (cap_val == Const(0, UInt(1)))
        nxt_state = mux(in_load, mux(load_done, Const(ST_PLAY, UInt(2)), state),
                    mux(in_play, mux(play == Const(63, UInt(6)),
                                     Const(ST_WAIT, UInt(2)), state), state))
        state <<= mux(rst, Const(ST_LOAD, UInt(2)), nxt_state)
        ld <<= mux(rst, Const(0, UInt(7)), mux(ld_issue, ld + Const(1, UInt(7)), ld))
        play <<= mux(rst, Const(0, UInt(6)), mux(in_play, play + Const(1, UInt(6)), play))

        # play phase: the fixed serializer schedule (even/odd lane sets)
        pk = play[0:3]
        pr = play[3:6]
        even = ((pk == Const(0, UInt(3))) | (pk == Const(2, UInt(3))) |
                (pk == Const(5, UInt(3))) | (pk == Const(7, UInt(3))))

        def row_sel(col):
            v = buf[7 * 8 + col]
            for r in range(6, -1, -1):
                v = mux(pr == Const(r, UInt(3)), buf[r * 8 + col], v)
            return v

        def lane(n):
            d = mux(even, row_sel(2 * n), row_sel(2 * n + 1))
            return mux(in_play, d, Const(0, UInt(16)))

        core = JpegIdct2D()
        core.io.rst_i <<= rst
        core.io.img_start_i <<= Const(0, UInt(1))
        core.io.img_end_i <<= Const(0, UInt(1))
        core.io.inport_valid_i <<= in_play
        core.io.inport_data0_i <<= lane(0)
        core.io.inport_data1_i <<= lane(1)
        core.io.inport_data2_i <<= lane(2)
        core.io.inport_data3_i <<= lane(3)
        core.io.inport_idx_i <<= mux(in_play, pk, Const(0, UInt(3)))

        # write-through of streamed pixels + done
        p_valid = core.io.outport_valid_o
        io.wr_en <<= p_valid
        io.wr_addr <<= core.io.outport_idx_o
        io.wr_data <<= core.io.outport_data_o

        wcnt <<= mux(rst, Const(0, UInt(7)),
                 mux(p_valid == Const(1, UInt(1)), wcnt + Const(1, UInt(7)), wcnt))
        done_q <<= mux(rst, Const(0, UInt(1)),
                   mux((p_valid == Const(1, UInt(1))) & (wcnt == Const(63, UInt(7))),
                       Const(1, UInt(1)), done_q))
        io.done <<= done_q


# `rst` is a plain data input (manual synchronous reset), but the Spire API reserves
# the port name `rst` — declare it as `rst_i` and rename the emitted port back to
# `rst` (the name the testbench binds to). The clock keeps its default name `clk`.
_net = JpegIdct2D1r1w().to_netlist("jpeg_idct_2d_1r1w", with_clock=True, with_reset=False)
for _p in _net._ports:
    if _p.name == "rst_i":
        _p.name = "rst"
_net.to_verilog_file("design.v")
