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
grep -Fq 'memcmp(id + 1, info->devid.id, min(3, info->devid.len))' "$core" || {
	echo 'Prepared kernel is missing the four-byte QPIC ID workaround' >&2
	exit 1
}
for marker in 'rc01-nand: matched model=' 'rc01-nand: QPIC READID' 'rc01-nand: ECC strength='; do
	grep -Fq "$marker" "$core" "$qpic"
	grep -aFq "$marker" "$vmlinux" || {
		echo "Compiled kernel is missing NAND diagnostics: $marker" >&2
		exit 1
	}
done
echo 'Prepared SPI ID workaround and compiled RC01 NAND probe diagnostics verified'
