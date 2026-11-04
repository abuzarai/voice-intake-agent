"""A fingerprint of the corpus a run actually used.

A baseline that records only the prompt hash cannot tell you whether the
scenarios, the gold or the recordings changed underneath it. This covers all
three, and marks a missing part rather than skipping it: a run without its
recordings is a different corpus, and the report should say so.
"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PARTS = ("scenarios/replay", "scenarios/behaviour", "gold", "audio")


def corpus_hash(root=ROOT):
    """A short digest over the scenarios, gold, recordings and manifest."""
    digest = hashlib.sha256()
    for part in PARTS:
        base = Path(root) / part
        if not base.exists():
            digest.update(f"{part}:missing".encode())
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    manifest = Path(root) / "manifest.yaml"
    if manifest.exists():
        digest.update(manifest.read_bytes())
    return digest.hexdigest()[:12]
