"""Spire starting point for `matmul_core` — the DUMBEST correct design.

Computes C = A*B (M=K=N=16, 8-bit unsigned in, 20-bit unsigned out) with ONE
multiplier and ONE accumulator, walking (i, j, l) with a 5-state FSM and
honoring the harness's 1-cycle registered read latency. NO operand buffering,
NO parallelism, NO tiling — discovering those is exactly what the benchmark
measures. This is the correctness reference and the cost baseline. Modify it.

Schedule per inner step l (≈3 cycles/MAC → ~12.5k cycles per 16x16x16 matrix):
  S_ISSUE_A : drive rd_en, rd_addr = i*K + l            (A[i][l] read issued)
  S_ISSUE_B : capture rd_data -> a_q; drive rd_en,
              rd_addr = MK + l*N + j                    (A arrived; B[l][j] issued)
  S_MAC     : acc += a_q * rd_data                      (B arrived; accumulate)
  S_WRITE   : drive wr_en, wr_addr = i*N + j, wr_data = acc; advance (i,j)
  S_DONE    : sticky; done = 1

Sync active-high reset: the harness drives `rst`, so we use with_reset=False and
gate every register with `mux(rst, <init>, <next>)` (synchronous), per the
with_reset decision tree in benchmarks/turbo_rtl/README.md.
"""
from spire import Component, IORecord, Input, Output, UInt
from spire.expr import Const, Register, Wire, mux

M = K = N = 16
MK = M * K            # 256  A elements; flat read addrs [0, MK)
# C element max = K*255*255 = 1_040_400 < 2**20, so 20-bit acc never overflows.


class MatmulCore(Component):
    def __init__(self):
        # with_clock=True (at to_netlist) auto-creates the `clk` input; declare the
        # rest explicitly. `rst` is a plain data input (manual sync reset), but Spire
        # reserves the port name `rst` for its framework reset — declare it as `rst_i`
        # and rename the emitted port back to `rst` (the name the testbench binds to).
        self.io = IORecord(
            rst_i=Input(UInt(1)),
            rd_data=Input(UInt(8)),
            rd_en=Output(UInt(1)),
            rd_addr=Output(UInt(9)),
            wr_en=Output(UInt(1)),
            wr_addr=Output(UInt(8)),
            wr_data=Output(UInt(20)),
            done=Output(UInt(1)),
        )
        self.elaborate()

    def elaborate(self):
        rst = self.io.rst_i   # plain data input (manual sync reset); port renamed to `rst` at emit
        rd_data = self.io.rd_data

        # ---- state encoding ----
        ST_A   = Const(0, UInt(3))   # S_ISSUE_A
        ST_B   = Const(1, UInt(3))   # S_ISSUE_B
        ST_MAC = Const(2, UInt(3))   # S_MAC
        ST_W   = Const(3, UInt(3))   # S_WRITE
        ST_D   = Const(4, UInt(3))   # S_DONE
        LAST   = Const(15, UInt(4))  # K-1 / N-1 / M-1

        state = Register(UInt(3),  name="state")
        i     = Register(UInt(4),  name="i")
        j     = Register(UInt(4),  name="j")
        l     = Register(UInt(4),  name="l")
        a_q   = Register(UInt(8),  name="a_q")
        acc   = Register(UInt(20), name="acc")

        # ---- predicates ----
        in_a   = (state == ST_A)
        in_b   = (state == ST_B)
        in_mac = (state == ST_MAC)
        in_w   = (state == ST_W)
        l_last = (l == LAST)
        j_last = (j == LAST)
        i_last = (i == LAST)

        # ---- next-state (cascaded mux; default holds in S_DONE) ----
        next_state = Wire(UInt(3), name="next_state")
        next_state <<= mux(in_a,   ST_B,
                      mux(in_b,   ST_MAC,
                      mux(in_mac, mux(l_last, ST_W, ST_A),
                      mux(in_w,   mux(j_last & i_last, ST_D, ST_A),
                                  ST_D))))

        # ---- counter / datapath next-values ----
        next_l = mux(in_mac, mux(l_last, l, l + 1),
                 mux(in_w,   Const(0, UInt(4)), l))
        next_j = mux(in_w,   mux(j_last, Const(0, UInt(4)), j + 1), j)
        next_i = mux(in_w,   mux(j_last, mux(i_last, i, i + 1), i), i)
        next_a = mux(in_b,   rd_data, a_q)
        next_acc = mux(in_mac, acc + a_q * rd_data,
                   mux(in_w,   Const(0, UInt(20)), acc))

        # ---- registers with synchronous active-high reset ----
        state <<= mux(rst, ST_A,                next_state)
        i     <<= mux(rst, Const(0, UInt(4)),   next_i)
        j     <<= mux(rst, Const(0, UInt(4)),   next_j)
        l     <<= mux(rst, Const(0, UInt(4)),   next_l)
        a_q   <<= mux(rst, Const(0, UInt(8)),   next_a)
        acc   <<= mux(rst, Const(0, UInt(20)),  next_acc)

        # ---- combinational outputs ----
        self.io.rd_en   <<= in_a | in_b
        self.io.rd_addr <<= mux(in_a, i * K + l,
                            mux(in_b, MK + l * N + j, Const(0, UInt(9))))
        self.io.wr_en   <<= in_w
        self.io.wr_addr <<= i * N + j
        self.io.wr_data <<= acc
        self.io.done    <<= (state == ST_D)


_net = MatmulCore().to_netlist("matmul_core", with_clock=True, with_reset=False)
for _p in _net._ports:
    if _p.name == "rst_i":
        _p.name = "rst"
_net.to_verilog_file("design.v")
