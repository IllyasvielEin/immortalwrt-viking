#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
umask 022

cd "$(dirname "$0")/.."
[ -z "${RC01_WITH_DAED+x}" ] || {
	echo 'RC01_WITH_DAED was replaced by RC01_WITH_DAE (dae + daede)' >&2; exit 1
}
with_dae="${RC01_WITH_DAE:-false}"
case "$with_dae" in
	true|false) ;;
	*) echo 'RC01_WITH_DAE must be true or false' >&2; exit 1 ;;
esac
# Resolve software feeds once at build start; all following steps use that lock.
python3 -B scripts/rc01-software-sources.py plan
cp .rc01-sources/feeds.conf feeds.conf
SOURCE_DATE_EPOCH="$(python3 -c 'import json; print(json.load(open(".rc01-sources/lock.json"))["source_date_epoch"])')"
export SOURCE_DATE_EPOCH
./scripts/feeds update -a
python3 -B scripts/rc01-software-sources.py prepare
./scripts/feeds update -i
source_mode="$(python3 -c 'import json; print(json.load(open(".rc01-sources/lock.json"))["mode"])')"
./scripts/feeds uninstall -a
# Install the selected roots and their dependencies, rather than indexing
# every unrelated UI/audio package into this device's Kconfig namespace.
./scripts/feeds install mihomo-meta
./scripts/feeds install luci luci-ssl luci-i18n-base-zh-cn htop zram-swap \
	luci-app-nikki luci-i18n-nikki-zh-cn \
	luci-proto-wireguard luci-app-tailscale-community luci-i18n-tailscale-community-zh-cn \
	luci-app-cpufreq luci-i18n-cpufreq-zh-cn conntrack iperf3 pciutils usbutils
if [ "$with_dae" = true ]; then
	# Override the old dae provider before resolving the UI dependency.
	if [ "$source_mode" = locked ]; then
		sh scripts/patch-rc01-daede.sh feeds/daede
	fi
	./scripts/feeds uninstall dae daed luci-app-dae luci-app-daed
	./scripts/feeds install -f -p daede dae luci-app-daede
fi

# Nikki's stable and alpha providers have symmetric CONFLICTS. Keeping both
# installed in the build package index creates a Kconfig dependency cycle.
./scripts/feeds uninstall mihomo-alpha

while read -r type name url; do
	[ "$type" = src-git ] || continue
	expected="${url##*^}"
	actual="$(git -C "feeds/$name" rev-parse HEAD)"
	[ "$actual" = "$expected" ] || {
		echo "Feed revision mismatch: $name" >&2
		exit 1
	}
done < .rc01-sources/feeds.conf

if [ "$source_mode" = locked ]; then
# Keep Go toolchain selection local at the pinned feed call sites.
sh scripts/patch-rc01-golang.sh feeds/packages

# Retain Nikki's source and attribution; make only this recorded local patch.
core_makefile=feeds/nikki/mihomo-meta/Makefile
old='PKG_BUILD_TIME:=$(shell date -u -Iseconds)'
new='PKG_BUILD_TIME:=$(shell date -u -d "@$(SOURCE_DATE_EPOCH)" -Iseconds)'
if grep -Fqx "$old" "$core_makefile"; then
	python3 - "$core_makefile" "$old" "$new" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
assert text.count(sys.argv[2]) == 1
p.write_text(text.replace(sys.argv[2], sys.argv[3]))
PY
else
	grep -Fqx "$new" "$core_makefile" || {
		echo 'Mihomo timestamp recipe changed; review it before building' >&2
		exit 1
	}
fi

# Replay reviewed package source updates after the feed and timestamp guards.
python3 -B scripts/patch-rc01-packages.py feeds/packages feeds/nikki

fi
python3 -B scripts/rc01-software-sources.py finish-locked
python3 -B scripts/rc01-software-sources.py verify

mkdir -p files
cp -a config/rc01/files/. files/
chmod 755 files/etc/board.d/99-rc01-wireless files/etc/uci-defaults/89-rc01-bypass
cp defconfig/qualcommbe-rc01.config .config
if [ "$with_dae" = true ]; then
	# Replace symbols rather than creating contradictory duplicate entries.
	./scripts/kconfig.pl + .config config/rc01/dae.config > .config.rc01
	mv .config.rc01 .config
fi
# Override the combined fragments, then verify the normalized selection below.
python3 -B scripts/rc01-go-bootstrap.py configure .config
make defconfig
RC01_WITH_DAE="$with_dae" sh scripts/validate-rc01-config.sh .config
# Parse expanded install recipes before spending time compiling a toolchain.
make -C package/emortal/rc01-platform TOPDIR="$PWD" -n compile >/dev/null
