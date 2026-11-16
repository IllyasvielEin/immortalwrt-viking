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
# Explicit opt-in: baseline backports MHI header uses the post-6.18 path.
# This does not modify either source tree and is NOT a production fix.
if [ "${DFS_MHI_HEADER_BRIDGE:-0}" = 1 ]; then
    mkdir -p "$obj/dfs-compile-compat/linux/device-id"
    echo '#include <linux/mod_devicetable.h>' > "$obj/dfs-compile-compat/linux/device-id/mhi.h"
    export CFLAGS="-I$obj/dfs-compile-compat"
fi
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
