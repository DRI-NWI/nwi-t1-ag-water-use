"""Shared script setup: import path, config, run paths, logging of inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from nwi_agwu import config as C  # noqa: E402


def parse(description: str, extra=None):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--config", default=str(C.DEFAULT_CONFIG))
    if extra:
        extra(ap)
    args = ap.parse_args()
    cfg = C.load(args.config)
    return args, cfg, C.RunPaths(cfg).make()


def file_receipt(path: str) -> dict:
    """mtime, size and a fast fingerprint (sha256 of the first and last 4 MB)."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    rec = {"path": str(p), "exists": True, "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}
    if p.is_file():
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            h.update(fh.read(4 << 20))
            if st.st_size > 8 << 20:
                fh.seek(-(4 << 20), os.SEEK_END)
                h.update(fh.read(4 << 20))
        rec.update(size_bytes=st.st_size, sha256_head_tail=h.hexdigest())
    return rec


def write_json(obj, path: Path):
    path.write_text(json.dumps(obj, indent=2, default=str))
