#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Keep the existing parallel/serial retry, recording each attempt separately."""
import json
from pathlib import Path
import subprocess
import sys
import time


def build(work=Path(".")):
    attempts = []
    result = 1
    try:
        with (work / "build.log").open("w") as log:
            for args in (["make", "-j4"], ["make", "-j1", "V=s"]):
                started = time.monotonic()
                print("RC01 build attempt: " + " ".join(args), flush=True)
                with subprocess.Popen(args, cwd=work, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True,
                                      errors="replace") as process:
                    try:
                        for line in process.stdout:
                            log.write(line)
                            sys.stdout.write(line)
                        result = process.wait()
                    except BaseException:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise
                attempts.append({"command": args, "exit_code": result,
                                 "elapsed_seconds": round(time.monotonic() - started, 3)})
                log.flush()
                if result <= 0:
                    break
    finally:
        try:
            (work / "rc01-build-timings.json").write_text(json.dumps(
                {"attempts": attempts, "exit_code": result}, indent=2) + "\n")
        except OSError as error:
            print(f"Warning: could not write build timings: {error}", file=sys.stderr)
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    sys.exit(build())
