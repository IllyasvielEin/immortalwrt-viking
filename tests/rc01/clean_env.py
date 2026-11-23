# SPDX-License-Identifier: GPL-2.0-only
"""Environment for test subprocesses that must not depend on the caller's RC01_* variables.

The build workflow exports RC01_GO_BOOTSTRAP_MODE and the RC01_HONK_* pins in its
top-level env, and a developer may export anything else. Scripts under test read
RC01_* variables, so a subprocess starts from the inherited environment minus
every RC01_* name and each test adds the ones it means to exercise.
"""
import os


def clean_environ():
    return {name: value for name, value in os.environ.items() if not name.startswith("RC01_")}
