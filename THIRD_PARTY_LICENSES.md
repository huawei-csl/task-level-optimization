# Third-party material

This repository's own code is released under the BSD-3-Clause-Clear license (see `LICENSE`).
The benchmark designs derived from external projects keep their original licenses. The upstream
copies under `benchmarks/upstream/*/` and `validation/{qr8,ofdm}/orig/` are verbatim apart from
edits marked `// RTLScout:` in the file; the study benchmarks under `benchmarks/<bench>_{spire,verilog}/`
are recasts behind the study interfaces, with their provenance recorded in each `metadata.json`
(`source`) and in the file headers.

| benchmark | upstream project | license (full text) | included here |
|---|---|---|---|
| `jpeg_idct_2d_*` | [ultraembedded/core_jpeg](https://github.com/ultraembedded/core_jpeg) (`jpeg_idct*.v`) | Apache-2.0 ([`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt)) | verbatim pipeline with marked fixes in `benchmarks/upstream/jpeg_idct_2d_upstream/`; the Verilog study start wraps the verbatim pipeline in our controller, the Spire start is a bit-exact port -- both derived works under the same license |
| `cv32e40p_alu_divh_*` | [OpenHW Group CV32E40P](https://github.com/openhwgroup/cv32e40p) (`cv32e40p_alu.sv`, `cv32e40p_alu_div.sv`, `cv32e40p_popcnt.sv`, `cv32e40p_ff_one.sv`, `cv32e40p_pkg.sv`) | Solderpad Hardware License v0.51 ([`LICENSES/SHL-0.51.txt`](LICENSES/SHL-0.51.txt), an Apache-2.0 wrapper; Apache text in `LICENSES/Apache-2.0.txt`) | flattened verbatim ALU in `benchmarks/upstream/cv32e40p_alu_divh_upstream/`; the study starts are recasts of the same files |
| `qr8_*` | [Zero ASIC LogikBench](https://github.com/zeroasiccorp/logikbench) `benchmarks/large/qr` | MIT ([`LICENSES/MIT-LogikBench.txt`](LICENSES/MIT-LogikBench.txt)) | verbatim `qr.v` + submodules in `benchmarks/upstream/qr8_upstream/` and `validation/qr8/orig/`; the study starts serialize the same CORDIC array behind a 1R/1W port |
| `ofdm32_mod_*`, `ofdm32_demod_*` | [Zero ASIC LogikBench](https://github.com/zeroasiccorp/logikbench) `ofdm` (`ofdm_tx.v`, `ofdm_rx.v`, `fft.v`) | MIT ([`LICENSES/MIT-LogikBench.txt`](LICENSES/MIT-LogikBench.txt)) | tx / rx halves with two marked one-line parameter corrections in `benchmarks/upstream/ofdm32_*_upstream/`; pristine files in `validation/ofdm/orig/`; the study starts put the same halves behind 1R/1W ports |
| `matmul_16x16x16_*` | own design | BSD-3-Clause-Clear (`LICENSE`) | -- |

`LICENSES/` holds the verbatim license files of the upstream repositories (fetched 2026-10-13). The
LogikBench file headers say "MIT (see LICENSE file in LogikBench repository)"; that file is
`LICENSES/MIT-LogikBench.txt`. core_jpeg ships no NOTICE file.

The study engine is [RTLScout](https://github.com/huawei-csl/rtlscout) (BSD-3-Clause-Clear) and
the Spire HDL is the `spire-hdl` package on PyPI; both are installed as dependencies, not vendored.
The EDA flow (Yosys, ABC, OpenROAD/OpenSTA, Verilator, the ASAP7 PDK) comes with the RTLScout
container image under the respective projects' licenses.
