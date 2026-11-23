#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Validate fmhub.json and publish a Frodhes-Bark rolling release to GitHub.

The FM-1 Firmware Hub (https://thefirmwarehub.com) polls GitHub releases and fmhub.json
every ~3 hours automatically — so publishing a tagged GitHub release with the versioned
.fwsc assets attached updates the Firmware Hub card without any manual submission.

Usage:
  tools/publish.py --check-only              # Validate fmhub.json, card image, and repo readiness
  tools/publish.py --dry-run                 # Validate and print the publish commands without running them
  tools/publish.py [--notes-file FILE]       # Validate, build (make dist), push main, create GitHub release
  tools/publish.py --no-build [--notes TEXT] # Publish already-built artifacts in build/
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FMHUB = ROOT / "fmhub.json"
VERSION_FILE = ROOT / "VERSION"
BUILD = ROOT / "build"

ALLOWED_TOP = {"what", "author", "kinds", "play", "editor", "cheat", "image", "color", "sounds", "smk37"}
ALLOWED_SMK = {"what", "image", "install", "guide"}
ALLOWED_KINDS = {"synth", "groove", "drums", "chords", "tracker", "other"}
AUDIO_EXTS = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".syx", ".sf2")
FORBIDDEN_TEXT_RE = re.compile(r"[<>{}`]|https?://|\b\w+\.(com|org|net|io|dev)\b", re.I)


def die(msg):
    print(f"publish: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def check_image(rel_path, label):
    p = ROOT / rel_path
    if not p.is_file():
        return [f"{label}: image file not found: {rel_path}"]
    errs = []
    sz = p.stat().st_size
    if sz > 300 * 1024:
        errs.append(f"{label}: image too large ({sz} B > 300 KB): {rel_path}")
    try:
        from PIL import Image
        with Image.open(p) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            if fmt not in ("PNG", "WEBP", "JPEG"):
                errs.append(f"{label}: image format {fmt!r} must be PNG, WEBP, or JPEG")
            if w != h:
                errs.append(f"{label}: image must be square, got {w}x{h}")
            if not (240 <= w <= 1024):
                errs.append(f"{label}: image size {w}x{h} outside 240..1024 px")
    except Exception as exc:
        errs.append(f"{label}: could not read image {rel_path}: {exc}")
    return errs


def check_what(text, label):
    errs = []
    if not isinstance(text, str) or len(text) < 20:
        errs.append(f"{label}: must be a string >= 20 chars")
        return errs
    if len(text) > 300:
        errs.append(f"{label}: {len(text)} chars exceeds 300-char limit")
    if FORBIDDEN_TEXT_RE.search(text):
        errs.append(f"{label}: contains forbidden HTML, braces, backticks, or links")
    return errs


def validate_fmhub():
    if not FMHUB.is_file():
        return ["fmhub.json missing at repository root"]
    try:
        data = json.loads(FMHUB.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"fmhub.json invalid JSON: {exc}"]

    errs = []
    unknown = set(data) - ALLOWED_TOP
    if unknown:
        errs.append(f"fmhub.json has disallowed keys (hub ignores whole file!): {sorted(unknown)}")

    for req in ("what", "kinds", "image"):
        if req not in data:
            errs.append(f"fmhub.json missing required key: {req}")

    if "what" in data:
        errs.extend(check_what(data["what"], "what"))

    if "author" in data:
        a = data["author"]
        if not isinstance(a, str) or not (2 <= len(a) <= 60) or FORBIDDEN_TEXT_RE.search(a):
            errs.append("author: must be plain text 2..60 chars without links/HTML")

    if "kinds" in data:
        k = data["kinds"]
        if not isinstance(k, list) or not k or k[0] not in ALLOWED_KINDS:
            errs.append(f"kinds: must be a list starting with one of {sorted(ALLOWED_KINDS)}")

    if "image" in data:
        errs.extend(check_image(data["image"], "image"))

    if "color" in data:
        c = data["color"]
        if not isinstance(c, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
            errs.append(f"color: must be #RRGGBB, got {c!r}")
        else:
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if luma < 70:
                errs.append(f"color {c} may be too dark for dark hub background (luma={luma:.1f})")

    for url_key in ("play", "editor", "cheat"):
        if url_key in data and (not isinstance(data[url_key], str) or not data[url_key].startswith("https://")):
            errs.append(f"{url_key}: must be an https:// URL")

    if "sounds" in data:
        s = data["sounds"]
        if not isinstance(s, str) or len(s) > 300:
            errs.append("sounds: must be a string <= 300 chars")
        elif s == "" or s.lower().startswith("none"):
            tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
            bad = [f for f in tracked if f.lower().endswith(AUDIO_EXTS)]
            if bad:
                errs.append(f"sounds is 'none' but tracked audio files exist: {bad[:5]}")

    if "smk37" in data:
        smk = data["smk37"]
        if not isinstance(smk, dict):
            errs.append("smk37: must be an object")
        else:
            unk_smk = set(smk) - ALLOWED_SMK
            if unk_smk:
                errs.append(f"smk37 has disallowed keys: {sorted(unk_smk)}")
            if "what" in smk:
                errs.extend(check_what(smk["what"], "smk37.what"))
            if "image" in smk:
                errs.extend(check_image(smk["image"], "smk37.image"))
            for url_key in ("install", "guide"):
                if url_key in smk and (not isinstance(smk[url_key], str) or not smk[url_key].startswith("https://")):
                    errs.append(f"smk37.{url_key}: must be an https:// URL")

    if not (ROOT / "LICENSE").is_file():
        errs.append("LICENSE file missing at repository root")

    return errs


def verify_fwsc_identities(ver):
    sys.path.insert(0, str(ROOT / "tools"))
    import fm1_install
    expected = {
        f"frodhes-bark-{ver}-c1.fwsc": "FM-1_1968",
        f"frodhes-bark-{ver}-smk37.fwsc": "SMK-37 Pro_1968",
        f"frodhes-bark-{ver}-smk37-elite.fwsc": "SMK-37 Elite_1968",
        f"frodhes-bark-{ver}-smk37-mke.fwsc": "MKE-P37_1968",
    }
    for fn, want_id in expected.items():
        p = BUILD / fn
        if not p.is_file():
            die(f"missing artifact: {p}")
        got = fm1_install.product_of(p.read_bytes())
        if got != want_id:
            die(f"{fn}: expected identity {want_id!r}, got {got!r}")
        print(f"  OK  {fn}  (identity: {got})")


def main():
    ap = argparse.ArgumentParser(description="Validate fmhub.json and publish Frodhes-Bark to GitHub / Firmware Hub")
    ap.add_argument("--version", help="override version (default: read from VERSION)")
    ap.add_argument("--check-only", action="store_true", help="only validate fmhub.json and repo readiness")
    ap.add_argument("--dry-run", action="store_true", help="validate and print commands without building/pushing")
    ap.add_argument("--no-build", action="store_true", help="skip 'make dist' and publish existing build/ artifacts")
    ap.add_argument("--skip-git-check", action="store_true", help="allow dirty working tree")
    ap.add_argument("--notes", help="inline release notes text")
    ap.add_argument("--notes-file", help="path to markdown release notes file")
    args = ap.parse_args()

    errs = validate_fmhub()
    if errs:
        for e in errs:
            print(f"  FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(FMHUB.read_text(encoding="utf-8"))
    print(f"fmhub.json OK  (what: {len(data['what'])}/300 chars, image: {data['image']}, color: {data.get('color')})")
    if args.check_only:
        print("Check passed! After pushing, verify live at: https://thefirmwarehub.com/kb/#fmhub")
        return 0

    ver = (args.version or (VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.is_file() else "")).strip()
    if not ver:
        die("no VERSION file found and --version not given")
    tag = f"v{ver}"

    if not args.skip_git_check:
        st = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        if st:
            die("working tree is dirty; commit your changes (including VERSION & fmhub.json) first, or pass --skip-git-check")

    assets = [
        f"build/frodhes-bark-{ver}-c1.fwsc",
        f"build/frodhes-bark-{ver}-smk37.fwsc",
        f"build/frodhes-bark-{ver}-smk37-elite.fwsc",
        f"build/frodhes-bark-{ver}-smk37-mke.fwsc",
        f"build/frodhes-bark-{ver}-mpc.zip",
        "build/SHA256SUMS.txt",
    ]

    if args.dry_run:
        print(f"[dry-run] Version: {ver}  Tag: {tag}")
        if not args.no_build:
            print("[dry-run] make dist")
        print("[dry-run] git push origin main")
        print(f"[dry-run] gh release create {tag} " + " ".join(assets) + f' --title "Frodhes-Bark {tag}" --generate-notes')
        return 0

    if not args.no_build:
        subprocess.run(["make", "dist"], cwd=ROOT, check=True)

    verify_fwsc_identities(ver)
    for rel in assets:
        if not (ROOT / rel).is_file():
            die(f"missing release asset: {rel}")

    subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, check=True)

    cmd = ["gh", "release", "create", tag, *assets, "--title", f"Frodhes-Bark {tag}"]
    if args.notes_file:
        cmd += ["--notes-file", args.notes_file]
    elif args.notes:
        cmd += ["--notes", args.notes]
    else:
        cmd += ["--generate-notes"]
    subprocess.run(cmd, cwd=ROOT, check=True)

    print(f"\nPublished {tag} to GitHub!")
    print("The FM-1 Firmware Hub polls GitHub releases & fmhub.json automatically every ~3 hours.")
    print("Check your card preview anytime at: https://thefirmwarehub.com/kb/#fmhub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
