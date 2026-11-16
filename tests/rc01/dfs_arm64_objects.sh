#!/bin/sh
# Object-only GCC15 ARM64 validation, never an RC01 image build.
# Arguments: readonly prepared Linux, local toolchain root, writable replay,
#            NEW or reusable object output directory.
set -eu
kernel_src=$(realpath "$1")
dfs_toolchain=$(realpath "$2")
bp=$(realpath "$3")
obj=$(realpath -m "$4")
export LD_LIBRARY_PATH=$dfs_toolchain/usr/lib/x86_64-linux-gnu
export PATH=$dfs_toolchain/usr/bin:$PATH
export BISON_PKGDATADIR=$dfs_toolchain/usr/share/bison
export M4=$dfs_toolchain/usr/bin/m4
export YACC='bison -y'
export LEX=flex
make -s -C "$kernel_src" O="$obj" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15 defconfig
make -s -C "$kernel_src" O="$obj" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15 -j4 modules_prepare
# Match package/kernel/mac80211/Makefile Build/Prepare: these vendored
# headers must not shadow the kernel-provided APIs. Only bp is writable.
rm -rf "$bp/include/linux/ssb" "$bp/include/linux/bcma" "$bp/include/net/bluetooth"
rm -f "$bp/include/linux/cordic.h" "$bp/include/linux/crc8.h" \
    "$bp/include/linux/eeprom_93cx6.h" "$bp/include/linux/wl12xx.h" \
    "$bp/include/linux/mhi.h" "$bp/include/net/ieee80211.h" \
    "$bp/backport-include/linux/bcm47xx_nvram.h"
cat > "$bp/defconfig" <<'EOF'
CPTCFG_CFG80211=m
CPTCFG_MAC80211=m
CPTCFG_WLAN=y
CPTCFG_WLAN_VENDOR_ATH=y
CPTCFG_ATH_COMMON=m
CPTCFG_ATH12K=m
EOF
make -s -C "$bp" KLIB_BUILD="$obj" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15 usedefconfig
make -s -C "$bp" KLIB_BUILD="$obj" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15 backport-include/backport/autoconf.h
export BACKPORT_DIR=$bp
export KLIB_BUILD=$obj
"$bp/scripts/make" -s -C "$obj" M="$bp" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-15 -j4 \
    drivers/net/wireless/ath/ath12k/reg.o drivers/net/wireless/ath/ath12k/wmi.o \
    drivers/net/wireless/ath/ath12k/mac.o drivers/net/wireless/ath/ath12k/core.o
file "$bp"/drivers/net/wireless/ath/ath12k/reg.o "$bp"/drivers/net/wireless/ath/ath12k/wmi.o \
     "$bp"/drivers/net/wireless/ath/ath12k/mac.o "$bp"/drivers/net/wireless/ath/ath12k/core.o
