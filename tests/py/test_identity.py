# SPDX-License-Identifier: GPL-3.0-only
"""Permanent firmware identity (1968) and date-based rolling release version (VERSION) checks."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build_py():
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("frodhes_build", ROOT / "tools" / "build.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fm1_identity_every_build():
    b = build_py()
    assert b.PRODUCT == "FM-1_1968"
    assert b.VERSION == (ROOT / "VERSION").read_text().strip()
    assert b.package() == ("FM-1_1968", "frodhes.fwsc")
    for v in ("20261122.0", "20261122.1", "1.0.0", "1.0-rc1"):
        assert b.package("fm1", "pro", v) == ("FM-1_1968", f"frodhes-bark-{v}.fwsc")


def test_smk_builds_never_take_the_fm1_identity():
    b = build_py()
    want = {"pro": "SMK-37 Pro_1968", "elite": "SMK-37 Elite_1968", "mke": "MKE-P37_1968"}
    for model, ident_want in want.items():
        ident, name = b.package("smk37", model)
        assert ident == ident_want
        assert not ident.startswith("FM-1")
        assert name.startswith("frodhes-smk37")


def test_release_filenames_and_identities():
    b = build_py()
    v = "20261122.0"
    assert b.package("fm1", "pro", v) == ("FM-1_1968", f"frodhes-bark-{v}.fwsc")
    assert b.package("smk37", "pro", v) == ("SMK-37 Pro_1968", f"frodhes-bark-{v}-smk37.fwsc")
    assert b.package("smk37", "elite", v) == ("SMK-37 Elite_1968", f"frodhes-bark-{v}-smk37-elite.fwsc")
    assert b.package("smk37", "mke", v) == ("MKE-P37_1968", f"frodhes-bark-{v}-smk37-mke.fwsc")


def test_main_sets_the_identity_only_through_package():
    """main() may not assign the identity any other way (Dinghy's `PRODUCT = BOARD_PREFIX[...] + m[2]`)"""
    src = (ROOT / "tools" / "build.py").read_text()
    main = src[src.index("\ndef main():"):]
    assert re.findall(r"^\s*PRODUCT\b[^=\n]*=", main, re.M) == ["    PRODUCT, name ="]


def test_built_package_carries_it():
    """Built FM-1 package carries permanent FM-1_1968 in package header and app binary, plus APP_VER."""
    ver = (ROOT / "VERSION").read_text().strip()
    candidates = [
        ROOT / "build" / f"frodhes-bark-{ver}-c1.fwsc",
        ROOT / "build" / f"frodhes-bark-{ver}.fwsc",
        ROOT / "build" / "frodhes-c1.fwsc",
        ROOT / "build" / "frodhes.fwsc",
    ]
    pkg = next((p for p in candidates if p.exists()), None)
    app = ROOT / "build" / "frodhes.bin"
    if pkg is None or not app.exists():
        import pytest
        pytest.skip("no built FM-1 .fwsc / frodhes.bin")
    sys.path.insert(0, str(ROOT / "tools"))
    import fm1_install
    assert fm1_install.product_of(pkg.read_bytes()) == "FM-1_1968"
    img = app.read_bytes()
    assert b"FM-1_1968\0" in img
    assert ver.encode() + b"\0" in img
    assert not re.search(rb"FM-1_99\d", img)
