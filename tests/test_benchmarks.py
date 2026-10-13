"""The released benchmark folders: naming, metadata, descriptions, HDL-twin and variant invariants."""
from __future__ import annotations

import json
import re

import pytest

from tasklevel import config as C
from tasklevel.benches import BENCHES, HDLS, LANGUAGE

ALL = [(b, h, e) for b in BENCHES for h in HDLS for e in (False, True)]
IDS = [b.name(h, e) for b, h, e in ALL]


def meta(d):
    return json.loads((d / "metadata.json").read_text())


def test_every_expected_folder_exists_and_nothing_else():
    expected = {b.name(h, e) for b, h, e in ALL} | {"upstream"}
    present = {p.name for p in C.BENCHMARKS.iterdir() if p.is_dir()}
    assert present == expected
    upstream = {p.name for p in C.UPSTREAM_BENCHMARKS.iterdir() if p.is_dir()}
    assert upstream == {b.upstream for b in BENCHES if b.upstream}


@pytest.mark.parametrize("b,hdl,equiv", ALL, ids=IDS)
def test_metadata(b, hdl, equiv):
    d = b.dir(hdl, equiv)
    m = meta(d)
    assert m["name"] == d.name, "metadata name must equal the folder name"
    assert m["language"] == LANGUAGE[hdl]
    sp = d / m["starting_point"]
    assert sp.exists()
    assert sp.suffix == (".py" if hdl == "spire" else sp.suffix) and (hdl == "spire" or sp.suffix in (".v", ".sv"))
    assert (d / "tb.sv").exists() and (d / "description.txt").exists()
    assert m.get("module_name")
    for key in ("verilog_sibling", "spire_sibling", "variant", "upstream_core"):
        assert key not in m, f"internal metadata key {key} leaked"
    if equiv:
        assert "equivalence track" in json.dumps(m.get("source", {}))


@pytest.mark.parametrize("b,hdl,equiv", ALL, ids=IDS)
def test_description_cites_existing_files_by_bare_name(b, hdl, equiv):
    """The harness copies context/ flat into the agent workspace, so descriptions name files bare."""
    d = b.dir(hdl, equiv)
    text = (d / "description.txt").read_text()
    assert "context/" not in text
    files = {p.name for p in d.iterdir()} | {p.name for p in (d / "context").iterdir()}
    cited = set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|sv|v|dat|txt))`", text))
    missing = {c for c in cited if "/" not in c and c not in files and not c.startswith("design")}
    assert not missing, f"description cites files that do not exist: {sorted(missing)}"


@pytest.mark.parametrize("b", BENCHES, ids=[b.key for b in BENCHES])
def test_hdl_twins_share_testbench_and_vectors(b):
    """Fairness invariant of the HDL comparison: only the starting design differs."""
    s, v = b.dir("spire"), b.dir("verilog")
    assert (s / "tb.sv").read_bytes() == (v / "tb.sv").read_bytes()
    for dat in s.glob("*.dat"):
        assert (v / dat.name).read_bytes() == dat.read_bytes(), dat.name
    assert meta(s)["module_name"] == meta(v)["module_name"]


@pytest.mark.parametrize("b,hdl", [(b, h) for b in BENCHES for h in HDLS], ids=[b.name(h) for b in BENCHES for h in HDLS])
def test_equiv_variant_is_the_task_benchmark_plus_a_trace_oracle(b, hdl):
    t, e = b.dir(hdl), b.dir(hdl, equiv=True)
    assert (t / "tb.sv").read_text() != (e / "tb.sv").read_text()
    assert (e / "vectors.dat").read_bytes() == (t / "vectors.dat").read_bytes()
    ms, me = meta(t), meta(e)
    assert ms["module_name"] == me["module_name"] and ms["starting_point"] == me["starting_point"]
    assert (t / ms["starting_point"]).read_bytes() == (e / me["starting_point"]).read_bytes()
    trace = {"mem": ["trace.dat", "trace_len.dat"], "lat": ["lat.dat"]}[b.oracle]
    for f in trace:
        assert (e / f).stat().st_size > 0, f
    assert "equivalence" in (e / "description.txt").read_text().lower()


@pytest.mark.parametrize("name", [b.upstream for b in BENCHES if b.upstream])
def test_upstream_measurement_benchmark(name):
    d = C.UPSTREAM_BENCHMARKS / name
    m = meta(d)
    assert m["name"] == name and m["language"] == "verilog"
    assert (d / m["starting_point"]).exists() and (d / "tb.sv").exists() and (d / "vectors.dat").exists()
    assert "origin" in m["source"]


def test_no_internal_references_in_released_text():
    pat = re.compile(r"internal/|_debug|upstream_orig|upstream_adp|pyenv_eda|STUDY\.md")
    hits = []
    for f in list(C.BENCHMARKS.rglob("*")) + list(C.VALIDATION.rglob("*")):
        if f.is_file() and f.suffix in (".txt", ".json", ".py", ".sv", ".v"):
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{f.relative_to(C.REPO)}:{i}")
    assert not hits, hits[:10]


def test_third_party_license_texts_are_shipped():
    lic = C.REPO / "LICENSES"
    expect = {"Apache-2.0.txt": "Apache License", "SHL-0.51.txt": "SOLDERPAD HARDWARE LICENSE version 0.51",
              "MIT-LogikBench.txt": "Copyright (c) 2025 Zero ASIC Corporation"}
    assert {p.name for p in lic.iterdir()} == set(expect)
    for name, marker in expect.items():
        assert marker in (lic / name).read_text()
    tp = (C.REPO / "THIRD_PARTY_LICENSES.md").read_text()
    for name in expect:
        assert f"LICENSES/{name}" in tp
