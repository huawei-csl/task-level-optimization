"""Spire starting point for `qr8_core` — the fully SERIAL 1R/1W recast of the
logikbench CORDIC QR at N=8, DW=16: the DUMBEST correct design.

Computes the fixed-point QR triangularization with the EXACT arithmetic of
the upstream logikbench `qr` (M=16 CORDIC micro-rotations, F=8 fractional
bits, IW=32 wrap-around width, KINV=19898 Q15 gain compensation, the same
(jr, ir) annihilation order, first-quadrant pivots). The stimuli are
study-defined (the benchmark's vector generator: a deterministic mix of structured and
random matrices; upstream ships no vectors); the golden R values in
vectors.dat were captured by simulating the UNMODIFIED upstream RTL at N=8
on exactly these stimuli.

Schedule — ONE CORDIC micro-rotation (a shift-add pair) per cycle, one
column pair at a time:
  LOAD  : 64 reads, one element per cycle (registered read)
  per rotation (jr, ir):
    VEC  : 16 cycles — vectoring CORDIC on the pivot pair, recording the
           direction bit sigma[i] each cycle (results discarded)
    ROT  : for each column k = jr..N-1: 16 replay cycles + 1 gain-comp /
           write-back cycle (acc " * KINV >> 15" and W update)
  STORE : 64 writes of W[g] >> F, one per cycle
  DONE  : sticky
~3.4k cycles/job. NO parallel columns (the upstream rotates all 8 columns in
one cycle), NO chained micro-rotations, NO load/compute/store overlap —
recovering that parallelism, in any mix, is exactly what this benchmark
measures.

The recast serializes the upstream OVER TIME: the upstream computes one
full rotation per cycle (64-entry gather, 16-stage vectoring CORDIC, 8
parallel 16-stage rotation CORDICs, gain multiply — one giant combinational
cone between the W registers), while this design unrolls that cone into its
single shift-add micro-rotation steps, one per cycle, time-multiplexing ONE
stage. Why not wrap the untouched core behind the 1R/1W port instead: the
one-cycle cone maps to a ~11.9 ns period (measured through the study flow
at the upstream's native interface, benchmarks/qr8_upstream), and no
across-cycle tool can shorten it — retiming cannot split the cone (the
W -> cone -> W loop holds one register stage, a retiming invariant) and
pipelining conserves runtime (rotation r+1 reads the rows rotation r wrote,
and the annihilation order is pinned by the bit-exact oracle, so the
initiation interval equals the pipeline depth). That period would also
multiply every wrapper I/O cycle: a wrapped untouched core measures ~3.8x
worse adp than this start. Fairness: the arithmetic and goldens are
exactly the upstream's, both study tracks start from the same design, and
the serialization is not deck-stacking in the recast's favor — measured at
its native interface the upstream (~1.15e11) scores BETTER than this
serial start (1.60e11), i.e. the recast is the WEAKER baseline and x-start
gains partly re-earn the serialized parallelism.

Sync active-high reset: the harness drives `rst`; spire reserves that port
name, so it is declared `rst_i` and renamed to `rst` at emit (matmul pattern).
"""
from __future__ import annotations

from typing import List

from spire import Component, IORecord, Input, Output, selection_topology
from spire.expr import Const, Expr, Register, SInt, UInt, cat, fit_type, mux, reinterpret

N = 8           # matrix dimension
DW = 16         # signed I/O word width
IW = DW + 16    # internal width (guard + fractional)
M = 16          # CORDIC micro-rotations
F = 8           # fractional bits
KINV = 19898    # 1/K in Q15 (K = prod sqrt(1 + 2^-2i), M = 16)
NN = N * N      # 64 matrix elements

S_LOAD, S_VEC, S_ROT, S_GAIN, S_STORE, S_DONE = 0, 1, 2, 3, 4, 5


def _u(v: int, w: int) -> Expr:
    return Const(v, UInt(w))


def _asr_const(x: Expr, k: int) -> Expr:
    """Arithmetic shift right by a CONSTANT k."""
    w = x.typ.width
    if k == 0:
        return x
    return fit_type(reinterpret(x[k:w], SInt(w - k)), SInt(w))


def _sel(items: List[Expr], idx: Expr, nbits: int) -> Expr:
    """Balanced select tree items[idx] (pads with items[0])."""
    items = list(items) + [items[0]] * ((1 << nbits) - len(items))

    def build(off: int, bit: int) -> Expr:
        if bit < 0:
            return items[off]
        return mux(idx[bit:bit + 1], build(off + (1 << bit), bit - 1),
                   build(off, bit - 1))

    return build(0, nbits - 1)


class Qr8Core(Component):
    """N=8 CORDIC Givens QR, one micro-rotation per cycle, 1R/1W port."""

    def __init__(self) -> None:
        self.io = IORecord(
            rst_i=Input(UInt(1)),
            rd_data=Input(UInt(DW)),
            rd_en=Output(UInt(1)),
            rd_addr=Output(UInt(6)),
            wr_en=Output(UInt(1)),
            wr_addr=Output(UInt(6)),
            wr_data=Output(UInt(DW)),
            done=Output(UInt(1)),
        )
        self.elaborate()

    def elaborate(self) -> None:
        io = self.io
        rst = io.rst_i

        # ---- state ------------------------------------------------------------
        W = [Register(SInt(IW), name=f"w_{g}") for g in range(NN)]
        state = Register(UInt(3), name="state")
        lc = Register(UInt(7), name="lc")        # load counter 0..64
        sc = Register(UInt(7), name="sc")        # store counter 0..63
        jr = Register(UInt(3), name="jr")        # pivot row / column
        ir = Register(UInt(3), name="ir")        # row being annihilated
        kc = Register(UInt(3), name="kc")        # column being rotated
        mi = Register(UInt(4), name="mi")        # micro-rotation step 0..15
        sig = Register(UInt(M), name="sigma")    # recorded direction bits
        cx = Register(SInt(IW), name="cx")       # CORDIC x accumulator
        cy = Register(SInt(IW), name="cy")       # CORDIC y accumulator

        in_load = state == _u(S_LOAD, 3)
        in_vec = state == _u(S_VEC, 3)
        in_rot = state == _u(S_ROT, 3)
        in_gain = state == _u(S_GAIN, 3)
        in_store = state == _u(S_STORE, 3)
        in_done = state == _u(S_DONE, 3)

        # ---- LOAD: issue read k while lc<64; capture element lc-1 -------------
        issue = in_load & (lc < _u(NN, 7))
        capture = in_load & (lc >= _u(1, 7))
        cap_idx = fit_type(lc - _u(1, 7), UInt(7))[0:6]
        load_last = in_load & (lc == _u(NN, 7))
        load_v = fit_type(reinterpret(cat(_u(0, F), io.rd_data), SInt(DW + F)), SInt(IW))

        # ---- W element reads (row, column) ------------------------------------
        def w_at(row: Expr, col: Expr) -> Expr:
            rows = [_sel([W[j * N + k] for k in range(N)], col, 3) for j in range(N)]
            return _sel(rows, row, 3)

# (element reads for CORDIC seeding happen below, with the NEXT phase's
        # indices — the seed lands in cx/cy on the same edge that advances the FSM)

        # ---- one micro-rotation step (shared datapath) ------------------------
        # shift amount = mi (variable): 16:1 select over the constant shifts
        sx = _sel([_asr_const(cx, i) for i in range(M)], mi, 4)
        sy = _sel([_asr_const(cy, i) for i in range(M)], mi, 4)
        # direction: vectoring takes sign(y); rotation replays sigma[mi]
        dpos_vec = cy[IW - 1:IW]
        dpos_rot = _sel([sig[i:i + 1] for i in range(M)], mi, 4)
        dpos = mux(in_vec, dpos_vec, dpos_rot)
        nx = fit_type(mux(dpos, cx - sy, cx + sy), SInt(IW))
        ny = fit_type(mux(dpos, cy + sx, cy - sx), SInt(IW))
        step_last = mi == _u(M - 1, 4)

        # record sigma during vectoring (bit mi <- dpos)
        sig_next = cat(*[mux(mi == _u(i, 4), dpos_vec, sig[i:i + 1]) for i in range(M)])

        # ---- gain compensation (one cycle, two multiplies) --------------------
        gx = reinterpret((cx * Const(KINV, SInt(17)))[15:15 + IW], SInt(IW))
        gy = reinterpret((cy * Const(KINV, SInt(17)))[15:15 + IW], SInt(IW))

        # ---- FSM edges ---------------------------------------------------------
        vec_done = in_vec & step_last
        rot_done = in_rot & step_last
        col_last = kc == _u(N - 1, 3)
        ir_last = ir == _u(N - 1, 3)
        last_pair = ir_last & (jr == _u(N - 2, 3))
        gain_next_state = mux(~col_last, _u(S_ROT, 3),
                          mux(~last_pair, _u(S_VEC, 3), _u(S_STORE, 3)))

        # ---- CORDIC seeding: read W with the NEXT phase's indices -------------
        next_jr = mux(ir_last, jr + _u(1, 3), jr)          # pair advance
        next_ir = mux(ir_last, jr + _u(2, 3), ir + _u(1, 3))
        seed_col = mux(in_gain & ~col_last, kc + _u(1, 3),  # next column, same pair
                   mux(vec_done, jr,                        # ROT starts at the pivot col
                       next_jr))                            # next pair's pivot col / load_last (jr=0)
        seed_rj = mux(in_gain & col_last, next_jr, jr)
        seed_ri = mux(in_gain & col_last, next_ir, ir)
        seed_x = w_at(seed_rj, seed_col)
        seed_y = w_at(seed_ri, seed_col)
        seed_now = load_last | vec_done | in_gain

        state <<= mux(rst, _u(S_LOAD, 3),
                  mux(load_last, _u(S_VEC, 3),
                  mux(vec_done, _u(S_ROT, 3),
                  mux(rot_done, _u(S_GAIN, 3),
                  mux(in_gain, gain_next_state,
                  mux(in_store & (sc == _u(NN - 1, 7)), _u(S_DONE, 3),
                      state))))))
        lc <<= mux(rst, _u(0, 7), mux(issue, lc + _u(1, 7), lc))
        sc <<= mux(rst, _u(0, 7), mux(in_store, sc + _u(1, 7), sc))
        mi <<= mux(rst | vec_done | rot_done | in_gain, _u(0, 4),
               mux(in_vec | in_rot, mi + _u(1, 4), mi))
        # rotation bookkeeping: kc starts at jr (pivot column rotates too);
        # after the last column of a pair, advance (jr, ir) exactly as upstream
        adv_pair = in_gain & col_last & ~last_pair
        kc <<= mux(rst, _u(0, 3),
               mux(vec_done, jr,
               mux(in_gain & ~col_last, kc + _u(1, 3), kc)))
        jr <<= mux(rst | load_last, _u(0, 3),
               mux(adv_pair & ir_last, jr + _u(1, 3), jr))
        ir <<= mux(rst | load_last, _u(1, 3),
               mux(adv_pair, mux(ir_last, jr + _u(2, 3), ir + _u(1, 3)), ir))
        sig <<= mux(in_vec, sig_next, sig)
        # CORDIC accumulators: (re)seed on every phase boundary, step in VEC/ROT
        cx <<= mux(seed_now, seed_x, mux(in_vec | in_rot, nx, cx))
        cy <<= mux(seed_now, seed_y, mux(in_vec | in_rot, ny, cy))

        # ---- working-matrix writes --------------------------------------------
        # load capture, or the gain-compensated column pair write-back
        for g in range(NN):
            r, k = divmod(g, N)
            wb_j = in_gain & (jr == _u(r, 3)) & (kc == _u(k, 3))
            wb_i = in_gain & (ir == _u(r, 3)) & (kc == _u(k, 3))
            W[g] <<= mux(rst, _u(0, IW),
                     mux(capture & (cap_idx == _u(g, 6)), load_v,
                     mux(wb_j, gx,
                     mux(wb_i, gy,
                         W[g]))))

        # ---- STORE / outputs ---------------------------------------------------
        sc6 = sc[0:6]
        store_val = w_at(fit_type(sc6[3:6], UInt(3)), fit_type(sc6[0:3], UInt(3)))
        io.rd_en <<= issue
        io.rd_addr <<= fit_type(lc, UInt(7))[0:6]
        io.wr_en <<= in_store
        io.wr_addr <<= sc6
        io.wr_data <<= store_val[F:F + DW]
        io.done <<= in_done


_net = Qr8Core().to_netlist("qr8_core", with_clock=True, with_reset=False)
for _p in _net._ports:
    if _p.name == "rst_i":
        _p.name = "rst"
_net.to_verilog_file("design.v")
