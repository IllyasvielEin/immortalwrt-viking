#!/bin/sh
# Compile this candidate against a prepared, private ARM64 kernel object tree.
# Usage: script KERNEL_SOURCE TOOLCHAIN WIRELESS_SOURCE KERNEL_OBJECTS
set -eu
kernel=$(realpath "$1")
tc=$(realpath "$2")
wireless=$(realpath "$3")
obj=$(realpath "$4")
export LD_LIBRARY_PATH="$tc/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$tc/usr/bin:$PATH"
export BISON_PKGDATADIR="$tc/usr/share/bison"
export M4="$tc/usr/bin/m4"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15
export KLIB_BUILD="$obj" BACKPORT_DIR="$wireless"
# Exactly the mac80211 Build/Prepare header deletion list. No header bridge.
uv run --no-project --offline python -B - "$wireless" <<'PY'
from pathlib import Path
import shutil, sys
root = Path(sys.argv[1])
for name in ('include/linux/ssb', 'include/linux/bcma', 'include/net/bluetooth'):
    path = root / name
    if path.is_dir():
        shutil.rmtree(path)
for name in ('include/linux/cordic.h', 'include/linux/crc8.h',
             'include/linux/eeprom_93cx6.h', 'include/linux/wl12xx.h',
             'include/linux/mhi.h', 'include/net/ieee80211.h',
             'backport-include/linux/bcm47xx_nvram.h'):
    (root / name).unlink(missing_ok=True)
PY
make -s -C "$wireless" HOSTCC=cc CC="$CC" YACC='bison -y' LEX=flex defconfig-ath11k
cat >> "$wireless/.config" <<'CONFIG'
CPTCFG_ATH12K=m
CPTCFG_ATH12K_AHB=y
CPTCFG_ATH12K_DEBUG=y
CPTCFG_ATH12K_THERMAL=y
CPTCFG_ATH12K_COREDUMP=y
CPTCFG_NL80211_TESTMODE=y
CONFIG
make -s -C "$wireless" HOSTCC=cc CC="$CC" YACC='bison -y' LEX=flex olddefconfig
make -s -C "$wireless" HOSTCC=cc CC="$CC" YACC='bison -y' LEX=flex backport-include/backport/autoconf.h
# An error hidden by the backports wrapper cannot count as configured.
test -s "$wireless/backport-include/backport/autoconf.h"
make -s --eval '-include $(BACKPORT_DIR)/.config' -C "$kernel" O="$obj" M="$wireless" \
    ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" CC="$CC" -j4 \
    drivers/net/wireless/ath/ath12k/core.o drivers/net/wireless/ath/ath12k/qmi.o \
    drivers/net/wireless/ath/ath12k/ce.o drivers/net/wireless/ath/ath12k/hal.o \
    drivers/net/wireless/ath/ath12k/dp.o drivers/net/wireless/ath/ath12k/mac.o \
    drivers/net/wireless/ath/ath12k/wmi.o drivers/net/wireless/ath/ath12k/reg.o \
    drivers/net/wireless/ath/ath12k/mhi.o drivers/net/wireless/ath/ath12k/pci.o \
    drivers/net/wireless/ath/ath12k/ahb.o drivers/net/wireless/ath/ath12k/thermal.o \
    drivers/net/wireless/ath/ath12k/testmode.o
file "$wireless"/drivers/net/wireless/ath/ath12k/*.o
