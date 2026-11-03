#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Only retain cache contents after the expected key was completely restored."""
import os
from pathlib import Path
import shutil


def clear(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def prepare_cache(path, cold, lookup_outcome, expected_key, restore_outcome, restored_key):
    if cold:
        clear(path)
        return False, "explicit-cold"
    if not expected_key:
        # A lookup never extracts data. A miss (or unavailable lookup) starts
        # empty, and a normal successful lookup miss can seed a new cache.
        clear(path)
        return lookup_outcome == "success", "fresh-after-lookup-miss-or-unavailable"
    if lookup_outcome == "success" and restore_outcome == "success" and restored_key == expected_key:
        return True, "restored-expected-key"
    # actions/cache can swallow download/extraction errors and still report
    # success. A missing/mismatched restored key must therefore fail closed.
    clear(path)
    return False, "incomplete-restore-discarded"


def main():
    cold = os.environ.get("RC01_COLD_CACHE") == "true"
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for name, directory in (("downloads", "dl"), ("compiler", ".ccache")):
            prefix = "RC01_" + name.upper()
            save, state = prepare_cache(Path(directory), cold,
                os.environ.get(prefix + "_LOOKUP_OUTCOME", ""),
                os.environ.get(prefix + "_EXPECTED_KEY", ""),
                os.environ.get(prefix + "_RESTORE_OUTCOME", ""),
                os.environ.get(prefix + "_RESTORED_KEY", ""))
            print(f"{name}: {state}; save={str(save).lower()}")
            output.write(f"{name}_save={str(save).lower()}\n")
        if cold:
            clear(Path("tmp/go-build"))


if __name__ == "__main__":
    main()
