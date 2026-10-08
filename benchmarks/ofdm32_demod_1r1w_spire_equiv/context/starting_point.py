"""Spire starting point for `ofdm32_demod_core` — the RECEIVE half of the
OFDM modem (N=32, CP=8) behind a 1R/1W memory port.

The RX chain is the validated ofdm_spire port re-specialized to N=32
(LOG2N=5, R2SDF fft latency LAT=37, measured): CP discard, forward FFT via
the radix-2 DIF single-path delay-feedback pipeline (per-stage 1/2 scaling,
Q1.15 twiddles), 1/N rescale and per-subcarrier Gray-coded QAM demap
(mod: 0=QPSK, 1=16-QAM, 2=64-QAM; mod=3 masks every bit). Bit-exact
contract: input samples in vectors.dat are the upstream TX's loopback-tap
stream passed through a noisy channel; golden output bits were captured
from the (parameter-corrected) upstream RX fed those noisy samples.

Wrapper FSM over the 1R/1W port:

  LOAD  : 81 reads, one per cycle (word 0 = mod; words 1..80 = the noisy
          time-domain symbol, sample s: word 1+2s = I, 2+2s = Q,
          two's-complement 16-bit; the CP samples s<8 are read and
          discarded; registered read)
  RUN   : stream the 32 data samples into the FFT, wait for the spectrum
          (~1 + (LAT+N) + 1 cycles), demap
  STORE : 12 writes of out_bits[16k +: 16], one per cycle
  DONE  : sticky

~170 cycles/job with everything serialized — load, the FFT pass and the
store phase run strictly one after another. NO overlap, NO pipelining across
jobs, NO restructuring of the transform schedule — finding those is the
point.

Fairness of the recast: the wrapper only reconstructs the upstream RX's
native interface (one complex sample per beat, mod held stable) from the
serial port and drains the demapped bits — the compute pipeline and its
schedule are untouched, so the cycle gap vs the upstream's native ~111/job
is exactly the contract's data movement (81 reads + 12 writes, I and Q as
separate words), charged identically to every design in the study. The
noisy channel exists for oracle soundness, not realism: the clean loopback
is an identity on the payload, which a task-level oracle cannot tell from a
plain copy — seeded noise below the decision threshold makes the golden
bits depend on the actual demodulation arithmetic (they were captured from
the upstream RX fed the same noisy samples), while any correct demodulator
still recovers them bit-exactly.

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
NBW = 6 * N                 # 192 payload bits
NIN, NOUT = 81, 12          # memory words per job (mod + 80 samples in / 12 out)

SINTAB = [round(math.sin(2 * math.pi * k / 256) * 32767) for k in range(256)]
assert SINTAB[1] == 804 and SINTAB[32] == 23170 and SINTAB[64] == 32767


def _u(v: int, w: int) -> Expr:
    return Const(v, UInt(w))


def _s(v: int, w: int) -> Expr:
    return Const(v, SInt(w))


def _asr(x: Expr, k: int) -> Expr:
    """Arithmetic shift right by constant (slice + sign-extend)."""
    w = x.typ.width
    if k == 0:
        return x
    return fit_type(reinterpret(x[k:w], SInt(w - k)), SInt(w))


def _shl16(x: Expr, k: int) -> Expr:
    """(x <<< k) truncated to 16 bits (the original's in-width shift)."""
    return reinterpret(cat(_u(0, k), fit_type(x, UInt(16))[0:16 - k]), SInt(16))


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


def _demap_axis(x: Expr, doff: Expr, dlim: Expr) -> Expr:
    """Per-axis QAM demap: (x<<<LOG2N + doff) >>> 12, clamp, bin->gray."""
    lr = _shl16(x, LOG2N)
    sr = fit_type(lr, SInt(DW + 2)) + doff
    shr = _asr(sr, 12)
    dlim_s = fit_type(z_ext(dlim, DW + 2), SInt(DW + 2))
    idr = mux(shr < _s(0, DW + 2), _u(0, 3),
              mux(shr > dlim_s, dlim, fit_type(shr, UInt(DW + 2))[0:3]))
    d2 = idr[2:3] & dlim[2:3]
    d1 = (idr[1:2] ^ idr[2:3]) & dlim[1:2]
    d0 = (idr[0:1] ^ idr[1:2]) & dlim[0:1]
    return cat(d0, d1, d2)


class Ofdm32DemodCore(Component):
    """OFDM receiver (FFT + rescale + QAM demap) behind a 1R/1W memory port."""

    def __init__(self) -> None:
        self.io = IORecord(
            rd_data=Input(UInt(DW)),
            rd_en=Output(UInt(1)),
            rd_addr=Output(UInt(7)),
            wr_en=Output(UInt(1)),
            wr_addr=Output(UInt(4)),
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
        lc = Register(UInt(7), name="wr_lc")     # load counter 0..81
        sc = Register(UInt(4), name="wr_sc")     # store counter 0..11
        mod_q = Register(UInt(2), name="wr_mod_q")
        piv = Register(UInt(1), name="wr_piv")   # 1-cycle start pulse

        w_load = wstate == W_LOAD
        w_run = wstate == W_RUN
        w_store = wstate == W_STORE
        w_done = wstate == W_DONE

        issue = w_load & (lc < _u(NIN, 7))
        capture = w_load & (lc >= _u(1, 7))
        cap_idx = fit_type(lc - _u(1, 7), UInt(7))
        load_last = w_load & (lc == _u(NIN, 7))  # word 80 captured this cycle

        mod_q <<= mux(capture & (cap_idx == _u(0, 7)), io.rd_data[0:2], mod_q)
        piv <<= load_last                        # start pulse in RUN's 1st cycle

        # ================= receiver =====================================
        rx_state = Register(UInt(3), name="rx_state")
        rx_cnt = Register(UInt(10), name="rx_cnt")
        rxiv = Register(UInt(1), name="rxiv")
        rxir = Register(SInt(DW), name="rxir")
        rxii = Register(SInt(DW), name="rxii")
        out_v = Register(UInt(1), name="rx_out_valid")
        out_b = Register(UInt(NBW), name="rx_out_bits")
        rr = [Register(SInt(DW), name=f"rx_rr_{k}") for k in range(N)]
        ri = [Register(SInt(DW), name=f"rx_ri_{k}") for k in range(N)]
        xr = [Register(SInt(DW), name=f"rx_xr_{k}") for k in range(N)]
        xi = [Register(SInt(DW), name=f"rx_xi_{k}") for k in range(N)]

        r_idle = rx_state == IDLE
        r_rx = rx_state == ST2
        r_out = rx_state == ST3

        # sample capture during LOAD: word 1+2s = I, 2+2s = Q; the CP samples
        # (s < CP) are read and discarded, data samples land in rr/ri[s-CP]
        for g in range(N):
            s = g + CP
            hit_i = capture & (cap_idx == _u(1 + 2 * s, 7))
            hit_q = capture & (cap_idx == _u(2 + 2 * s, 7))
            rr[g] <<= mux(hit_i, reinterpret(io.rd_data, SInt(DW)), rr[g])
            ri[g] <<= mux(hit_q, reinterpret(io.rd_data, SInt(DW)), ri[g])

        rx_fft_rst = r_idle
        _, rxor, rxoi = _fft_engine("fft", rx_fft_rst, rxiv, rxir, rxii)

        rm5 = fit_type(rx_cnt - _u(LAT, 10), UInt(10))[0:LOG2N]
        rcnt_lt_n = rx_cnt < _u(N, 10)
        rcnt_ge_lat = ~(rx_cnt < _u(LAT, 10))
        rx_last_fft = rx_cnt == _u(LAT + N - 1, 10)

        for g in range(N):
            hit_x = r_rx & rcnt_ge_lat & (rm5 == _u(_bitrev5(g), LOG2N))
            xr[g] <<= mux(hit_x, rxor, xr[g])
            xi[g] <<= mux(hit_x, rxoi, xi[g])

        rxiv <<= mux(r_idle, _u(0, 1), mux(r_rx, ~rx_last_fft, rxiv))
        rxir <<= mux(r_rx, mux(rcnt_lt_n, _sel(rr, rx_cnt[0:LOG2N], LOG2N), _s(0, DW)), rxir)
        rxii <<= mux(r_rx, mux(rcnt_lt_n, _sel(ri, rx_cnt[0:LOG2N], LOG2N), _s(0, DW)), rxii)

        rx_state <<= mux(r_idle, mux(piv, ST2, IDLE),
                     mux(r_rx, mux(rx_last_fft, ST3, ST2),
                     mux(r_out, IDLE, IDLE)))
        rx_cnt <<= mux(r_idle, _u(0, 10),
                   mux(r_rx, mux(rx_last_fft, rx_cnt, rx_cnt + _u(1, 10)), rx_cnt))

        # QAM demap (one unit per subcarrier) + output register
        dlim = mux(mod_q == _u(0, 2), _u(1, 3),
               mux(mod_q == _u(1, 2), _u(3, 3),
               mux(mod_q == _u(2, 2), _u(7, 3), _u(0, 3))))
        doff = mux(mod_q == _u(0, 2), _s(2 * AMP, DW + 2),
               mux(mod_q == _u(1, 2), _s(4 * AMP, DW + 2),
               mux(mod_q == _u(2, 2), _s(8 * AMP, DW + 2), _s(AMP, DW + 2))))
        dem = []
        for g in range(N):
            dem.append(_demap_axis(xr[g], doff, dlim))
            dem.append(_demap_axis(xi[g], doff, dlim))
        out_b <<= mux(r_out, cat(*dem), out_b)
        out_v <<= r_out

        # ================= wrapper FSM / outputs ========================
        wstate <<= mux(load_last, W_RUN,
                   mux(w_run & (out_v == _u(1, 1)), W_STORE,
                   mux(w_store & (sc == _u(NOUT - 1, 4)), W_DONE,
                       wstate)))
        lc <<= mux(issue, lc + _u(1, 7), lc)
        sc <<= mux(w_store, sc + _u(1, 4), sc)

        out_slices = [out_b[16 * k:16 * (k + 1)] for k in range(NOUT)]
        io.rd_en <<= issue
        io.rd_addr <<= lc
        io.wr_en <<= w_store
        io.wr_addr <<= sc
        io.wr_data <<= _sel(out_slices, sc, 4)
        io.done <<= w_done


_net = Ofdm32DemodCore().to_netlist("ofdm32_demod_core", with_clock=True, with_reset=True)
_net.to_verilog_file("design.v")
