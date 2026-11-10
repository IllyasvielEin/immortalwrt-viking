#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check publishable text and decoded source-lock payloads without printing secrets."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PUBLIC_MAINTAINERS = {"openwrt", "immortalwrt", "vikingyfy", "kenzok8", "metacubex",
                      "tailscale", "nikkinikki-org", "daeuniverse"}
RULES = {
    "private-key": re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED)?\s*PRIVATE KEY-----"),
    "credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|tskey-(?:auth|api)-[A-Za-z0-9_-]{12,})\b"),
    "personal-home": re.compile(r"(?:/home/(?!runner/|<)[A-Za-z0-9_.-]+/|(?:[A-Za-z]:[\\/]|/mnt/[a-z]/)Users[\\/](?!<)[A-Za-z0-9_. -]+[\\/])"),
}
TEXT_SUFFIXES = {".json", ".patch", ".conf", ".lock", ".md", ".sh", ".log", ".buildinfo",
                 ".manifest", ".txt", ".config", ".pem", ".pub"}


def identities():
    values = {os.environ.get(name, "").strip() for name in ("GITHUB_REPOSITORY_OWNER", "GITHUB_ACTOR")}
    # Local checks also cover the configured builder without putting that
    # identity into a command argument, report, or generated source artifact.
    result = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    if result.returncode == 0:
        values.add(result.stdout.strip())
    return [re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_-])", re.I)
            for value in values if len(value) >= 3 and value.casefold() not in PUBLIC_MAINTAINERS]


def inspect_text(text, owner_patterns):
    issues = {name for name, pattern in RULES.items() if pattern.search(text)}
    if any(pattern.search(text) for pattern in owner_patterns):
        issues.add("builder-identity")
    return issues


def inspect_payloads(value, owner_patterns):
    issues = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "content" and isinstance(child, str):
                try:
                    decoded = base64.b64decode(child, validate=True).decode("utf-8")
                except (ValueError, UnicodeError):
                    pass  # Binary upstream icons are not text metadata.
                else:
                    issues |= inspect_text(decoded, owner_patterns)
            issues |= inspect_payloads(child, owner_patterns)
    elif isinstance(value, list):
        for child in value:
            issues |= inspect_payloads(child, owner_patterns)
    return issues


def check(paths, owner_patterns=None):
    owner_patterns = identities() if owner_patterns is None else owner_patterns
    checked = 0
    findings = []
    for source in paths:
        source = Path(source)
        if not source.exists():
            continue
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in files:
            if path.suffix not in TEXT_SUFFIXES and path.name not in ("lock.json", ".config", "SHA256SUMS"):
                continue
            if path.is_symlink():
                findings.append({"file": path.name, "category": "symlink-text-artifact"})
                continue
            if not path.is_file():
                continue
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                findings.append({"file": path.name, "category": "invalid-text-artifact"})
                continue
            checked += 1
            issues = inspect_text(text, owner_patterns)
            if path.suffix == ".json":
                try:
                    value = json.loads(text)
                except ValueError:
                    issues.add("invalid-json-artifact")
                else:
                    issues |= inspect_payloads(value, owner_patterns)
            findings.extend({"file": path.name, "category": category} for category in sorted(issues))
    return {"checked_text_files": checked, "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    result = check(args.paths)
    # Never include matched values, text snippets, or personal directory paths.
    print(json.dumps(result, sort_keys=True))
    return bool(result["findings"])


if __name__ == "__main__":
    sys.exit(main())
