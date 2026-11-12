#!/bin/sh
# Prepare public, pinned inputs for the source-level RC01 regressions that
# otherwise skip: prepared Linux/backports trees (DTB, platform, wireless),
# BusyBox ash, the locked LuCI form/validation sources and a host ucode.
# Pins and patches come from this tree; downloads are hash-checked.
# Only download archives are reused. Recreate sources/tools on every run so
# old markers, local patches or tool options cannot silently survive. The
# work directory is generated output, not a place for manual source edits.
# This is not a firmware build and produces no image or APK.
# Usage: sh scripts/rc01-verify-inputs.sh [work-dir]
#        (default build_dir/rc01-verify-inputs), then: . <work-dir>/env.sh
set -eu
TREE=$(cd "$(dirname "$0")/.." && pwd)
W=${1:-$TREE/build_dir/rc01-verify-inputs}
mkdir -p "$W/dl" "$W/src"
W=$(cd "$W" && pwd)
KPATCH="$TREE/scripts/patch-kernel.sh"
rm -f "$W/env.sh"

pin() { sed -n "s/^$2[[:space:]]*[:]*=[[:space:]]*//p" "$1" | head -n1; }
fetch() { # url file sha256
	[ -f "$W/dl/$2" ] || curl -sSfL -o "$W/dl/$2.part" "$1" && { [ ! -f "$W/dl/$2.part" ] || mv "$W/dl/$2.part" "$W/dl/$2"; }
	echo "$3  $W/dl/$2" | sha256sum -c --quiet -
}
patchdir() { # tree dir
	[ -d "$2" ] || return 0
	if [ -s "$2/series" ]; then
		grep -v '^#' "$2/series" | xargs -n1 sh "$KPATCH" "$1" "$2" >/dev/null
	else
		sh "$KPATCH" "$1" "$2" >/dev/null
	fi
}

# Linux 6.18.60 + generic files/backport/pending/hack + qualcommbe platform patches
KV=6.18$(pin "$TREE/target/linux/generic/kernel-6.18" 'LINUX_VERSION-6.18')
KH=$(pin "$TREE/target/linux/generic/kernel-6.18" "LINUX_KERNEL_HASH-$KV")
fetch "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$KV.tar.xz" "linux-$KV.tar.xz" "$KH"
rm -rf "$W/src/linux-$KV"
tar -C "$W/src" -xJf "$W/dl/linux-$KV.tar.xz"
for d in "$TREE/target/linux/generic/files" "$TREE/target/linux/generic/files-6.18"; do
	[ -d "$d" ] && cp -fpR "$d"/. "$W/src/linux-$KV/"
done
for d in backport-6.18 pending-6.18 hack-6.18; do
	patchdir "$W/src/linux-$KV" "$TREE/target/linux/generic/$d"
done
patchdir "$W/src/linux-$KV" "$TREE/target/linux/qualcommbe/patches-6.18"

# mac80211 backports + package patches (series per subdirectory, as mac80211 Makefile)
BV=$(pin "$TREE/package/kernel/mac80211/Makefile" PKG_SOURCE_VERSION)
BH=$(pin "$TREE/package/kernel/mac80211/Makefile" PKG_HASH)
fetch "https://github.com/openwrt/backports/releases/download/backports-v$BV/backports-$BV.tar.zst" "backports-$BV.tar.zst" "$BH"
rm -rf "$W/src/backports-$BV"
tar -C "$W/src" --zstd -xf "$W/dl/backports-$BV.tar.zst"
# Order and ath11k host-only selection follow mac80211 Build/Patch (no NSS on qualcommbe).
M="$TREE/package/kernel/mac80211/patches"
for d in build subsys ath ath5k ath9k ath10k; do patchdir "$W/src/backports-$BV" "$M/$d"; done
mkdir -p "$W/src/ath11k-host" && rm -f "$W/src/ath11k-host"/*
for p in "$M"/ath11k/*.patch; do case "${p##*/}" in nss-*) ;; *) cp "$p" "$W/src/ath11k-host/";; esac; done
patchdir "$W/src/backports-$BV" "$W/src/ath11k-host"
for d in ath12k rt2x00 mt7601u mwl brcm rtl; do patchdir "$W/src/backports-$BV" "$M/$d"; done

# BusyBox 1.38.0 + package patches, built with the package's ash/applet config
BBV=$(pin "$TREE/package/utils/busybox/Makefile" PKG_VERSION)
BBH=$(pin "$TREE/package/utils/busybox/Makefile" PKG_HASH)
fetch "https://www.busybox.net/downloads/busybox-$BBV.tar.bz2" "busybox-$BBV.tar.bz2" "$BBH"
rm -rf "$W/src/busybox-$BBV"
tar -C "$W/src" -xjf "$W/dl/busybox-$BBV.tar.bz2"
patchdir "$W/src/busybox-$BBV" "$TREE/package/utils/busybox/patches"
# Target applet/ash defaults from the package's Config-defaults.in
# (IPV6 on, TARGET_bcm53xx off), as Build/Configure would see them.
awk '/^config BUSYBOX_DEFAULT_/ { sym = substr($2, 17); seen = 0; next }
     /^[ \t]+default / && !seen { seen = 1; sub(/^[ \t]+default /, "")
       if ($0 ~ / if TARGET_bcm53xx$/) v = "n"; else { sub(/ if IPV6$/, ""); v = $0 }
       if (v == "n") print "# CONFIG_" sym " is not set"; else print "CONFIG_" sym "=" v }' \
  "$TREE/package/utils/busybox/Config-defaults.in" |
  # Test-host deviation: without this, applets such as timeout exec their
  # own ip/ping instead of the PATH stubs (real host routing/ICMP).
  sed 's/^CONFIG_FEATURE_PREFER_APPLETS=y$/# CONFIG_FEATURE_PREFER_APPLETS is not set/' \
  > "$W/src/busybox-$BBV/.config"
yes n | make -s -C "$W/src/busybox-$BBV" oldconfig >/dev/null
make -s -C "$W/src/busybox-$BBV" -j"$(nproc)" >/dev/null

# Locked LuCI feed (form.js / ui.js / validation.js) at the feeds.conf pin
LC=$(sed -n 's/^src-git luci [^^]*\^//p' "$TREE/config/rc01/feeds.conf")
rm -rf "$W/src/luci"
git init -q "$W/src/luci"
git -C "$W/src/luci" fetch -q --depth 1 https://github.com/immortalwrt/luci.git "$LC"
git -C "$W/src/luci" -c advice.detachedHead=false checkout -q FETCH_HEAD
test "$(git -C "$W/src/luci" rev-parse HEAD)" = "$LC"

# Host ucode at the package pin, with the package's host module selection.
# Installed to a prefix so test -L stubs take precedence over native modules.
UC=$(pin "$TREE/package/utils/ucode/Makefile" PKG_SOURCE_VERSION)
rm -rf "$W/ucode"
git init -q "$W/ucode/src"
git -C "$W/ucode/src" fetch -q --depth 1 https://github.com/ucode-lang/ucode.git "$UC"
git -C "$W/ucode/src" -c advice.detachedHead=false checkout -q FETCH_HEAD
test "$(git -C "$W/ucode/src" rev-parse HEAD)" = "$UC"
cmake -S "$W/ucode/src" -B "$W/ucode/build" -DCMAKE_INSTALL_PREFIX="$W/ucode/prefix" \
	-DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_RPATH="$W/ucode/prefix/lib" \
	-DFS_SUPPORT=ON -DMATH_SUPPORT=ON -DSTRUCT_SUPPORT=ON -DDEBUG_SUPPORT=ON -DZLIB_SUPPORT=ON \
	-DNL80211_SUPPORT=OFF -DRESOLV_SUPPORT=OFF -DRTNL_SUPPORT=OFF -DUBUS_SUPPORT=OFF \
	-DUCI_SUPPORT=OFF -DULOOP_SUPPORT=OFF -DLOG_SUPPORT=OFF -DDIGEST_SUPPORT=OFF \
	-DFFI_SUPPORT=OFF -DNETADDR_SUPPORT=OFF -DSERIAL_SUPPORT=OFF -DSOCKET_SUPPORT=OFF >/dev/null
cmake --build "$W/ucode/build" -j"$(nproc)" >/dev/null
cmake --install "$W/ucode/build" >/dev/null

cat > "$W/env.sh" <<EOF
export UCODE=$W/ucode/prefix/bin/ucode
export RC01_KERNEL_SOURCE=$W/src/linux-$KV
export RC01_WIRELESS_SOURCE=$W/src/backports-$BV
export BUSYBOX=$W/src/busybox-$BBV/busybox
export LUCI_VALIDATION_JS=$W/src/luci/modules/luci-base/htdocs/luci-static/resources/validation.js
export LUCI_FORM_JS=$W/src/luci/modules/luci-base/htdocs/luci-static/resources/form.js
EOF
echo "prepared: $W/env.sh"
