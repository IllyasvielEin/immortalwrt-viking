#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

cd "$(dirname "$0")/.."
with_daed="${RC01_WITH_DAED:-false}"
case "$with_daed" in
	true|false) ;;
	*) echo 'RC01_WITH_DAED must be true or false' >&2; exit 1 ;;
esac
cp config/rc01/feeds.conf feeds.conf
./scripts/feeds update -a
./scripts/feeds uninstall -a
# Install the selected roots and their dependencies, rather than indexing
# every unrelated UI/audio package into this device's Kconfig namespace.
./scripts/feeds install mihomo-meta
./scripts/feeds install luci luci-ssl luci-i18n-base-zh-cn htop zram-swap \
	luci-app-nikki luci-i18n-nikki-zh-cn \
	luci-proto-wireguard luci-app-tailscale-community luci-i18n-tailscale-community-zh-cn \
	luci-app-cpufreq luci-i18n-cpufreq-zh-cn conntrack iperf3 pciutils usbutils
if [ "$with_daed" = true ]; then
	./scripts/feeds install luci-app-daed luci-i18n-daed-zh-cn
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
done < config/rc01/feeds.conf

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

mkdir -p files
cp -a config/rc01/files/. files/
chmod 755 files/etc/board.d/99-rc01-wireless files/etc/uci-defaults/89-rc01-bypass
cp defconfig/qualcommbe-rc01.config .config
if [ "$with_daed" = true ]; then
	# Replace symbols rather than creating contradictory duplicate entries.
	./scripts/kconfig.pl + .config config/rc01/daed.config > .config.rc01
	mv .config.rc01 .config
fi
make defconfig
RC01_WITH_DAED="$with_daed" sh scripts/validate-rc01-config.sh .config
