"""Offline guard: no tracked file may contain common secret patterns."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Patterns are assembled at runtime so this file holds no key-shaped literal.
PATTERNS = {
    "openai/anthropic key": re.compile("sk-" + r"(?:ant-)?[A-Za-z0-9_-]{20,}"),
    "github token": re.compile("gh" + r"[pousr]_[A-Za-z0-9]{30,}"),
    "github pat": re.compile("github" + r"_pat_[A-Za-z0-9_]{20,}"),
    "aws key": re.compile("AK" + r"IA[0-9A-Z]{16}"),
    "private key": re.compile("-----BEGIN [A-Z ]*PRIVATE" + " KEY-----"),
    "jwt": re.compile("ey" + r"J[A-Za-z0-9_-]{10,}\.ey" + r"J[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
    "slack token": re.compile("xox" + r"[baprs]-[A-Za-z0-9-]{10,}"),
}
FORBIDDEN_NAMES = {".env", "auth.json", "config.toml", "pinger.log", ".pinger-state.json"}


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


@pytest.mark.smoke
def test_no_secret_patterns_in_tracked_files():
    hits = []
    for rel in tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        hits += [f"{rel}: {name}" for name, rx in PATTERNS.items() if rx.search(text)]
    assert not hits, hits


@pytest.mark.smoke
def test_no_credential_or_state_files_tracked():
    bad = [r for r in tracked_files() if Path(r).name in FORBIDDEN_NAMES or r.endswith((".pem", ".key"))]
    assert not bad, bad
