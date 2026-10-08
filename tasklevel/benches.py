"""The six benchmarks and their released folders.

Folder names carry the HDL explicitly: `<bench>_spire` / `<bench>_verilog` are the task-level
starting designs, `<bench>_<hdl>_equiv` the generated equivalence-track variants (trace oracle).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config as C

HDLS = ("spire", "verilog")
LANGUAGE = {"spire": "spirehdl", "verilog": "verilog"}      # rtlscout --language values


@dataclass(frozen=True)
class Bench:
    key: str            # short key used in job ids and manifests
    folder: str         # released folder stem; <folder>_<hdl>[_equiv]
    label: str          # for tables/figures
    tex: str            # LaTeX row label
    memory_port: bool   # 1R/1W port (reads/writes counted) vs. the ALU's handshake
    upstream: str | None  # Table 3 measurement benchmark under benchmarks/upstream/, or None
    oracle: str         # equivalence oracle flavour: "mem" (trace of the memory port) or "lat" (per-op latency)

    def dir(self, hdl: str, equiv: bool = False):
        return C.BENCHMARKS / f"{self.folder}_{hdl}{'_equiv' if equiv else ''}"

    def name(self, hdl: str, equiv: bool = False) -> str:
        return f"{self.folder}_{hdl}{'_equiv' if equiv else ''}"


BENCHES = [
    Bench("matmul", "matmul_16x16x16_1r1w", "matmul 16³", "MatMul $16^3$", True, None, "mem"),
    Bench("jpeg", "jpeg_idct_2d_1r1w", "jpeg idct-2d", "jpeg idct-2d", True, "jpeg_idct_2d_upstream", "mem"),
    Bench("cv32", "cv32e40p_alu_divh", "cv32e40p alu", "cv32e40p alu", False, "cv32e40p_alu_divh_upstream", "lat"),
    Bench("qr8", "qr8_1r1w", "qr 8×8", "qr $8{\\times}8$", True, "qr8_upstream", "mem"),
    Bench("ofdm_tx", "ofdm32_mod_1r1w", "ofdm-32 tx", "ofdm-32 tx", True, "ofdm32_mod_upstream", "mem"),
    Bench("ofdm_rx", "ofdm32_demod_1r1w", "ofdm-32 rx", "ofdm-32 rx", True, "ofdm32_demod_upstream", "mem"),
]
BY_KEY = {b.key: b for b in BENCHES}


def rescore_metric(b: Bench) -> str:
    """Uniform rescore metric: the access-weighted product where a memory port exists (gives
    cycles, reads and writes in one evaluation), plain ARP for the ALU."""
    return "access_area_runtime_product" if b.memory_port else "area_runtime_product"
