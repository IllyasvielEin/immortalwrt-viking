#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Check the prepared kernel and compiled diagnostics, not the target defaults.
set -eu
kernel_dir="${1:?usage: validate-rc01-nand.sh kernel_dir vmlinux}"
vmlinux="${2:?usage: validate-rc01-nand.sh kernel_dir vmlinux}"
core="$kernel_dir/drivers/mtd/nand/spi/core.c"
qpic="$kernel_dir/drivers/spi/spi-qpic-snand.c"
for symbol in MTD_SPI_NAND SPI_QPIC_SNAND; do
	grep -Fqx "CONFIG_$symbol=y" "$kernel_dir/.config" || {
		echo "RC01 NAND driver is not built in: CONFIG_$symbol" >&2
		exit 1
	}
done
# Reuse the table/comparator/diagnostic checks on the prepared kernel, rather
# than approving a global three-byte workaround or trusting a patch filename.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$script_dir/validate-rc01-platform.py" "$kernel_dir" <<'PY'
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("rc01_platform", sys.argv[1])
platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform)
platform.verify_nand(Path(sys.argv[2]))
PY
for marker in 'rc01-nand: matched model=' 'rc01-nand: QPIC READID' 'rc01-nand: ECC strength='; do
	grep -Fq "$marker" "$core" "$qpic"
	grep -aFq "$marker" "$vmlinux" || {
		echo "Compiled kernel is missing NAND diagnostics: $marker" >&2
		exit 1
	}
done
echo 'Prepared SPI NAND full-ID matching and compiled RC01 NAND probe diagnostics verified'
