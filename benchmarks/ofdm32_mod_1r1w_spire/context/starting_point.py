"""Spire starting point for `ofdm32_mod_core` — the TRANSMIT half of the
OFDM modem (N=32, CP=8) behind a 1R/1W memory port.

The TX chain is the validated ofdm_spire port re-specialized to N=32
(LOG2N=5, R2SDF fft latency LAT=37, measured): per-subcarrier Gray-coded QAM
map (mod: 0=QPSK, 1=16-QAM, 2=64-QAM; mod=3 masks every bit), IFFT via the
radix-2 DIF single-path delay-feedback pipeline (per-stage 1/2 scaling,
Q1.15 twiddles), and cyclic-prefix assembly. Bit-exact contract: golden
output samples in vectors.dat were captured from the (parameter-corrected)
upstream RTL's TX at the loopback tap.

Wrapper FSM over the 1R/1W port:

  LOAD  : 13 reads, one per cycle (word 0 = mod, words 1..12 = in_bits,
          word k holding in_bits[16(k-1) +: 16]; registered read)
  RUN   : pulse the TX's in_valid once, wait for the assembled symbol
          (~1 + (LAT+N) + 1 cycles through map/IFFT/CP)
  STORE : 80 writes, one per cycle — sample s: word 2s = I, word 2s+1 = Q
          (two's-complement 16-bit)
  DONE  : sticky

~165 cycles/job with everything serialized — load, the IFFT pass and the
store phase run strictly one after another. NO overlap, NO pipelining across
jobs, NO restructuring of the transform schedule — finding those is the
point.

Fairness of the recast: the wrapper only reconstructs the upstream's native
interface (192-bit payload + mod in one in_valid pulse; parallel I/Q out)
from the serial port and drains the result — the compute pipeline and its
schedule are untouched, so the cycle gap vs the upstream's native ~112/job
is exactly the contract's data movement (13 reads + 80 writes, I and Q as
separate words), charged identically to every design in the study.

Reset: async active-high `rst` (spire with_reset=True); the harness asserts
it between jobs.
"""
from __future__ import annotations

import math
from typing import List, Tuple

from spire import Component, IORecord, Input, Output, selection_topology
from spire.expr import Const, Expr, Register, SInt, UInt, cat, fit_type, mux, reinterpret, z_ext

DW = 16
N = 32
CP = 8
LOG2N = 5
LAT = 37                    # fft pipeline latency (N=32, measured)
AMP = 2048                  # base PAM level
SCW = 6                     # clog2(N+CP) = clog2(40)
NBW = 6 * N                 # 192 payload bits
NIN, NOUT = 13, 80          # memory words per job (mod + 12 in / 80 samples out)

SINTAB = [round(math.sin(2 * math.pi * k / 256) * 32767) for k in range(256)]
assert SINTAB[1] == 804 and SINTAB[32] == 23170 and SINTAB[64] == 32767


def _u(v: int, w: int) -> Expr:
    return Const(v, UInt(w))


def _s(v: int, w: int) -> Expr:
    return Const(v, SInt(w))


def _bitrev5(v: int) -> int:
    return int(f"{v:05b}"[::-1], 2)


def _sel(items: List[Expr], idx: Expr, nbits: int) -> Expr:
    """Balanced binary select tree: items[idx] (pads with items[0])."""
    items = list(items) + [items[0]] * ((1 << nbits) - len(items))

    def build(off: int, bit: int) -> Expr:
        if bit < 0:
            return items[off]
        return mux(idx[bit:bit + 1], build(off + (1 << bit), bit - 1),
                   build(off, bit - 1))

    return build(0, nbits - 1)


def _sinrom(idx: Expr) -> Expr:
    """256-entry Q1.15 sine ROM."""
    with selection_topology("onehot"):
        out = _s(SINTAB[255], 16)
        for k in range(254, -1, -1):
            out = mux(idx == _u(k, 8), _s(SINTAB[k], 16), out)
    return out


def _fft_engine(prefix: str, frst: Expr, in_valid: Expr, in_r: Expr,
                in_i: Expr) -> Tuple[Expr, Expr, Expr]:
    """One R2SDF FFT (radix-2 DIF, LOG2N stages). frst is the engine's
    synchronous clear. Returns (out_valid, out_real, out_imag)."""
    v, r, i = in_valid, in_r, in_i
    for s in range(LOG2N):
        fblog = LOG2N - 1 - s
        fbdepth = 1 << fblog

        cnt = Register(UInt(LOG2N), name=f"{prefix}_s{s}_cnt")
        fb_r = [Register(SInt(DW), name=f"{prefix}_s{s}_fbr_{k}") for k in range(fbdepth)]
        fb_i = [Register(SInt(DW), name=f"{prefix}_s{s}_fbi_{k}") for k in range(fbdepth)]
        ov = Register(UInt(1), name=f"{prefix}_s{s}_ov")
        our = Register(SInt(DW), name=f"{prefix}_s{s}_or")
        oui = Register(SInt(DW), name=f"{prefix}_s{s}_oi")

        cb = cnt[fblog:fblog + 1]                       # 0=load, 1=butterfly
        p = z_ext(cnt[0:fblog], LOG2N) if fblog > 0 else _u(0, LOG2N)
        fbo_r, fbo_i = fb_r[fbdepth - 1], fb_i[fbdepth - 1]

        # butterfly, scaled by 1/2 (compute in DW+1 bits, then >>> 1)
        sum_r = fit_type(fbo_r, SInt(DW + 1)) + fit_type(r, SInt(DW + 1))
        sum_i = fit_type(fbo_i, SInt(DW + 1)) + fit_type(i, SInt(DW + 1))
        dif_r = fit_type(fbo_r, SInt(DW + 1)) - fit_type(r, SInt(DW + 1))
        dif_i = fit_type(fbo_i, SInt(DW + 1)) - fit_type(i, SInt(DW + 1))
        sum_r2 = reinterpret(fit_type(sum_r, UInt(DW + 1))[1:DW + 1], SInt(DW))
        sum_i2 = reinterpret(fit_type(sum_i, UInt(DW + 1))[1:DW + 1], SInt(DW))
        dif_r2 = reinterpret(fit_type(dif_r, UInt(DW + 1))[1:DW + 1], SInt(DW))
        dif_i2 = reinterpret(fit_type(dif_i, UInt(DW + 1))[1:DW + 1], SInt(DW))

        # twiddle W_N^(p << STAGE): addr = p << (STAGE + 8 - LOG2N), 8 bits
        shift = s + 8 - LOG2N
        addr = fit_type(cat(_u(0, shift), p[0:8 - shift]), UInt(8))
        cos_idx = fit_type(addr + _u(64, 8), UInt(8))[0:8]
        wr = _sinrom(cos_idx)                           # cos = sin(x + 90deg)
        wi = fit_type(_s(0, DW) - _sinrom(addr), SInt(DW))
        # complex multiply (dif2 * W), Q1.15
        m_rr = dif_r2 * wr
        m_ii = dif_i2 * wi
        m_ri = dif_r2 * wi
        m_ir = dif_i2 * wr
        tw_r = reinterpret(fit_type(m_rr - m_ii, UInt(2 * DW))[15:15 + DW], SInt(DW))
        tw_i = reinterpret(fit_type(m_ri + m_ir, UInt(2 * DW))[15:15 + DW], SInt(DW))

        nd_r = mux(cb, tw_r, r)
        nd_i = mux(cb, tw_i, i)

        def _f(reg: Register, nxt: Expr, hold: Expr) -> None:
            reg <<= mux(frst, Const(0, reg.typ), mux(v, nxt, hold))

        _f(cnt, cnt + _u(1, LOG2N), cnt)
        _f(fb_r[0], nd_r, fb_r[0])
        _f(fb_i[0], nd_i, fb_i[0])
        for k in range(1, fbdepth):
            _f(fb_r[k], fb_r[k - 1], fb_r[k])
            _f(fb_i[k], fb_i[k - 1], fb_i[k])
        _f(our, mux(cb, sum_r2, fbo_r), our)
        _f(oui, mux(cb, sum_i2, fbo_i), oui)
        ov <<= mux(frst, _u(0, 1), v)                   # tracks valid every cycle

        v, r, i = ov, our, oui
    return v, r, i


def _pam_axis(bits3: Expr, pmask: Expr) -> Expr:
    """Per-axis PAM map: mask, gray->binary, index -> (2*idx - mask) * AMP."""
    gr = bits3 & pmask
    b2 = gr[2:3]
    b1 = gr[1:2] ^ b2
    b0 = gr[0:1] ^ b1
    ir3 = cat(b0, b1, b2) & pmask
    irs = fit_type(z_ext(ir3, 6), SInt(6))
    pms = fit_type(z_ext(pmask, 6), SInt(6))
    lr = fit_type(irs * _s(2, 3) - pms, SInt(6))
    return fit_type(lr * _s(AMP, 13), SInt(DW))


class Ofdm32ModCore(Component):
    """OFDM transmitter (QAM map + IFFT + CP) behind a 1R/1W memory port."""

    def __init__(self) -> None:
        self.io = IORecord(
            rd_data=Input(UInt(DW)),
            rd_en=Output(UInt(1)),
            rd_addr=Output(UInt(4)),
            wr_en=Output(UInt(1)),
            wr_addr=Output(UInt(7)),
            wr_data=Output(UInt(DW)),
            done=Output(UInt(1)),
        )
        self.elaborate()

    def elaborate(self) -> None:
        io = self.io
        IDLE, ST1, ST2, ST3 = (_u(k, 3) for k in range(4))

        # ================= 1R/1W wrapper =================================
        W_LOAD, W_RUN, W_STORE, W_DONE = (_u(k, 2) for k in range(4))
        wstate = Register(UInt(2), name="wr_state")
        lc = Register(UInt(5), name="wr_lc")     # load counter 0..13
        sc = Register(UInt(7), name="wr_sc")     # store counter 0..79
        mod_q = Register(UInt(2), name="wr_mod_q")
        inw = [Register(UInt(DW), name=f"wr_inw_{k}") for k in range(12)]
        piv = Register(UInt(1), name="wr_piv")   # 1-cycle in_valid pulse

        w_load = wstate == W_LOAD
        w_run = wstate == W_RUN
        w_store = wstate == W_STORE
        w_done = wstate == W_DONE

        issue = w_load & (lc < _u(NIN, 5))
        capture = w_load & (lc >= _u(1, 5))
        cap_idx = fit_type(lc - _u(1, 5), UInt(5))[0:4]
        load_last = w_load & (lc == _u(NIN, 5))  # word 12 captured this cycle

        mod_q <<= mux(capture & (cap_idx == _u(0, 4)), io.rd_data[0:2], mod_q)
        for k in range(12):
            inw[k] <<= mux(capture & (cap_idx == _u(k + 1, 4)), io.rd_data, inw[k])
        piv <<= load_last                        # in_valid pulse in RUN's 1st cycle

        m_in_valid = piv
        m_mod = mod_q
        m_in_bits = cat(*inw)                    # inw[0] = in_bits[15:0]

        # ================= transmitter =================================
        tx_state = Register(UInt(3), name="tx_state")
        tx_cnt = Register(UInt(10), name="tx_cnt")
        txiv = Register(UInt(1), name="txiv")
        txir = Register(SInt(DW), name="txir")
        txii = Register(SInt(DW), name="txii")
        fr = [Register(SInt(DW), name=f"tx_fr_{k}") for k in range(N)]
        fi = [Register(SInt(DW), name=f"tx_fi_{k}") for k in range(N)]
        tr = [Register(SInt(DW), name=f"tx_tr_{k}") for k in range(N)]
        ti = [Register(SInt(DW), name=f"tx_ti_{k}") for k in range(N)]
        sym = [Register(SInt(DW), name=f"tx_sym_{k}") for k in range(N + CP)]
        symi = [Register(SInt(DW), name=f"tx_symi_{k}") for k in range(N + CP)]

        t_idle = tx_state == IDLE
        t_tx = tx_state == ST1
        t_cp = tx_state == ST2
        t_ready = tx_state == ST3                # symbol assembled in sym/symi
        tx_capture = t_idle & m_in_valid

        # QAM map (one unit per subcarrier); mod=3 wraps the 2-bit shift ->
        # pmask is 0 (all bits masked), exactly as the original RTL.
        pmask = mux(m_mod == _u(0, 2), _u(1, 3),
                mux(m_mod == _u(1, 2), _u(3, 3),
                mux(m_mod == _u(2, 2), _u(7, 3), _u(0, 3))))
        for g in range(N):
            fr[g] <<= mux(tx_capture, _pam_axis(m_in_bits[g * 6:g * 6 + 3], pmask), fr[g])
            fi[g] <<= mux(tx_capture, _pam_axis(m_in_bits[g * 6 + 3:g * 6 + 6], pmask), fi[g])

        # IFFT pass (conjugate in, conjugate out) through the fft engine
        tx_fft_rst = t_idle
        _, txor, txoi = _fft_engine("ifft", tx_fft_rst, txiv, txir, txii)

        m5 = fit_type(tx_cnt - _u(LAT, 10), UInt(10))[0:LOG2N]
        cnt_lt_n = tx_cnt < _u(N, 10)
        cnt_ge_lat = ~(tx_cnt < _u(LAT, 10))
        tx_last_fft = tx_cnt == _u(LAT + N - 1, 10)

        txiv <<= mux(t_idle, _u(0, 1), mux(t_tx, ~tx_last_fft, txiv))
        txir <<= mux(t_tx, mux(cnt_lt_n, _sel(fr, tx_cnt[0:LOG2N], LOG2N), _s(0, DW)), txir)
        txii <<= mux(t_tx, mux(cnt_lt_n, fit_type(_s(0, DW) - _sel(fi, tx_cnt[0:LOG2N], LOG2N), SInt(DW)), _s(0, DW)), txii)

        # collect the IFFT output at bit-reversed indices (negated imag)
        for g in range(N):
            hit = t_tx & cnt_ge_lat & (m5 == _u(_bitrev5(g), LOG2N))
            tr[g] <<= mux(hit, txor, tr[g])
            ti[g] <<= mux(hit, fit_type(_s(0, DW) - txoi, SInt(DW)), ti[g])

        # cyclic prefix assembly. The enable is REPLICATED into 8 registered
        # copies, each driving a 5-sample slice: a single shared enable ends
        # up as one flop bit driving ~2000 mux selects unbuffered (synthesis
        # cannot duplicate a register the way it duplicates a decode cone),
        # which alone cost ~2.4 ns of clock period.
        cp_en = [Register(UInt(1), name=f"tx_cp_en_{k}") for k in range(8)]
        for k in range(8):
            # the (tx_cnt >= k) term is always true at the assembly cycle
            # (tx_cnt = LAT+N-1 = 68); it exists ONLY to make the replicas
            # structurally distinct so yosys opt_merge cannot collapse them
            # back into a single flop
            cp_en[k] <<= t_tx & tx_last_fft & ~(tx_cnt < _u(k, 10))
        def _cpe(k: int) -> Expr:
            return cp_en[k // 5][0:1] == _u(1, 1)
        for k in range(CP):
            sym[k] <<= mux(_cpe(k), tr[N - CP + k], sym[k])
            symi[k] <<= mux(_cpe(k), ti[N - CP + k], symi[k])
        for k in range(N):
            sym[CP + k] <<= mux(_cpe(CP + k), tr[k], sym[CP + k])
            symi[CP + k] <<= mux(_cpe(CP + k), ti[k], symi[CP + k])

        tx_state <<= mux(t_idle, mux(m_in_valid, ST1, IDLE),
                     mux(t_tx, mux(tx_last_fft, ST2, ST1),
                     mux(t_cp, ST3, tx_state)))
        tx_cnt <<= mux(t_idle, _u(0, 10),
                   mux(t_tx, mux(tx_last_fft, tx_cnt, tx_cnt + _u(1, 10)),
                       tx_cnt))

        # ================= wrapper FSM / outputs ========================
        wstate <<= mux(load_last, W_RUN,
                   mux(w_run & t_ready, W_STORE,
                   mux(w_store & (sc == _u(NOUT - 1, 7)), W_DONE,
                       wstate)))
        lc <<= mux(issue, lc + _u(1, 5), lc)
        sc <<= mux(w_store, sc + _u(1, 7), sc)

        samp_i = _sel(sym, sc[1:7], SCW)          # sample index = sc >> 1
        samp_q = _sel(symi, sc[1:7], SCW)
        io.rd_en <<= issue
        io.rd_addr <<= fit_type(lc, UInt(5))[0:4]
        io.wr_en <<= w_store
        io.wr_addr <<= sc
        io.wr_data <<= reinterpret(mux(sc[0:1], samp_q, samp_i), UInt(DW))
        io.done <<= w_done


_net = Ofdm32ModCore().to_netlist("ofdm32_mod_core", with_clock=True, with_reset=True)
_net.to_verilog_file("design.v")
