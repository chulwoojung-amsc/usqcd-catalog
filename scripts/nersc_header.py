#!/usr/bin/env python3
"""
Read the plain-text header of a NERSC-format gauge configuration.

    nersc_header.py /path/to/ckpoint_lat.1000                 # local file
    nersc_header.py hpss:/lhsm/.../archived/ckpoint_lat.1000  # via `hsi get -`
    nersc_header.py ... --toml                                # print volume/action hints for an ensemble TOML

NERSC gauge files begin with an ASCII block

    BEGIN_HEADER
    HDR_VERSION = 1.0
    DIMENSION_1 = 48
    ...
    ENSEMBLE_ID = ...
    ENSEMBLE_LABEL = ...
    SEQUENCE_NUMBER = 1000
    PLAQUETTE = 0.5...
    CHECKSUM = ...
    END_HEADER

followed by the binary links. This reads only the first few KB (default 64 KB),
so for an HPSS-resident file it streams that much and stops; the file still has
to be on the HPSS disk cache or it is staged from tape first (`hsi ls -X <file>`
shows which). The header is self-describing: it confirms the file really is
NERSC format, carries the lattice DIMENSION_1..4 (cross-check the volume),
SEQUENCE_NUMBER (cross-check the trajectory in the file name), and whatever
ENSEMBLE_ID / ENSEMBLE_LABEL the generators stamped in.

Output is JSON by default: every KEY = VALUE found, in file order. Nothing is
guessed; a key not in the header is absent from the output. Exit status is 0 if
a well-formed BEGIN_HEADER/END_HEADER block was found, 2 otherwise (so it
doubles as a "is this NERSC format?" check).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


def legal_name(s: str) -> str:
    """Sanitise a header string into a MetaCat-legal ensemble name
    (^[A-Za-z0-9._-]+$, see register_ensemble.py). '+' -> 'p' (so 2+1f -> 2p1f,
    b+c -> bpc); any other illegal char -> '_'; dots and dashes are kept."""
    s = s.replace("+", "p")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def read_head(src: str, nbytes: int) -> bytes:
    if src.startswith("hpss:"):
        p = subprocess.Popen(["hsi", "-q", f"get - : {src[5:]}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        data = p.stdout.read(nbytes)
        p.kill()
        return data
    with open(src, "rb") as fh:
        return fh.read(nbytes)


def parse_header(head: bytes) -> dict | None:
    """Return the ordered KEY -> VALUE map, or None if there is no NERSC header."""
    text = head.decode("latin-1", "replace")
    i = text.find("BEGIN_HEADER")
    if i < 0:
        return None
    j = text.find("END_HEADER", i)
    if j < 0:
        return None  # header is longer than what we read, or the file is truncated
    fields: dict[str, str] = {}
    for line in text[i + len("BEGIN_HEADER"):j].splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        fields[key.strip()] = val.strip()
    return fields


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="local path, or hpss:<absolute HPSS path>")
    ap.add_argument("--bytes", type=int, default=64_000, help="how much of the file to read (default 64 KB)")
    ap.add_argument("--toml", action="store_true", help="print volume/action hints for an ensemble TOML instead of JSON")
    ap.add_argument("--name", action="store_true", help="print MetaCat-legal ensemble-name candidates from ENSEMBLE_ID/ENSEMBLE_LABEL, for human confirmation")
    args = ap.parse_args()

    fields = parse_header(read_head(args.source, args.bytes))
    if fields is None:
        sys.stderr.write(
            f"{args.source}: no NERSC BEGIN_HEADER/END_HEADER found in the first "
            f"{args.bytes} bytes (not NERSC format, truncated, or raise --bytes)\n"
        )
        return 2

    if args.name:
        # Candidates from the header, sanitised to the ensemble-name charset.
        # Nothing is auto-applied: pick one (or neither) and set [ensemble] name.
        eid, elabel = fields.get("ENSEMBLE_ID"), fields.get("ENSEMBLE_LABEL")
        if eid:
            print(f"# from ENSEMBLE_ID = {eid!r}")
            print(f"name = \"{legal_name(eid)}\"")
        if elabel:
            print(f"# from ENSEMBLE_LABEL = {elabel!r}")
            print(f"name = \"{legal_name(elabel)}\"")
        if not (eid or elabel):
            sys.stderr.write(f"{args.source}: header has no ENSEMBLE_ID or ENSEMBLE_LABEL\n")
            return 2
        return 0

    if not args.toml:
        json.dump(fields, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0

    # TOML hints: only what the header actually states, mapped to ensemble-TOML names.
    dims = [fields.get(f"DIMENSION_{k}") for k in (1, 2, 3, 4)]
    print("# from NERSC header of", args.source)
    if all(dims):
        print("[lqcd]")
        print(f"Nx = {dims[0]}\nNy = {dims[1]}\nNz = {dims[2]}\nNt = {dims[3]}")
        print(f"volume = [{dims[0]}, {dims[1]}, {dims[2]}, {dims[3]}]")
    for key, comment in (
        ("ENSEMBLE_ID", "ensemble id stamped by the generators"),
        ("ENSEMBLE_LABEL", "ensemble label stamped by the generators"),
        ("SEQUENCE_NUMBER", "trajectory of THIS config; cross-check the file name"),
        ("FLOATING_POINT", "storage precision"),
        ("DATATYPE", "link storage datatype"),
        ("PLAQUETTE", "average plaquette"),
        ("LINK_TRACE", "average link trace"),
        ("CHECKSUM", "NERSC header checksum"),
    ):
        if key in fields:
            print(f"# {key} = {fields[key]}   # {comment}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
