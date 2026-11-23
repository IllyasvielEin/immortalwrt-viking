#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
umask 022

cd "$(dirname "$0")/.."
root="$PWD"
python3 -B scripts/rc01-layout.py --check-generated

# honk (daeuniverse eBPF proxy, runtime unverified) is always built; the old
# switch is rejected rather than silently ignored.
[ -z "${RC01_WITH_HONK+x}" ] || { echo 'RC01_WITH_HONK was removed; honk is always built' >&2; exit 1; }

# Feeds are pinned to full commit IDs in config/rc01/feeds.conf; nothing is
# resolved to a newer revision at build time.
cp config/rc01/feeds.conf feeds.conf
./scripts/feeds update -a

# Controlled patches, one "<feed>:<patch>" per line. Each one must apply
# cleanly to the pinned checkout of its feed; any mismatch stops the build.
# New controlled patches are added here.
rc01_patches='packages:config/rc01/patches/golang/100-rc01-local-toolchain.patch
packages:config/rc01/patches/packages/100-tailscale-1.104.6.patch
nikki:config/rc01/patches/packages/110-mihomo-1.19.34.patch
daeuniverse:config/rc01/patches/honk/100-rc01-honk-source.patch'

for entry in $rc01_patches; do
	feed="${entry%%:*}"
	patch="$root/${entry#*:}"
	pin="$(sed -n "s/^src-git $feed .*\\^\\([0-9a-f]\\{40\\}\\)\$/\\1/p" config/rc01/feeds.conf)"
	[ -n "$pin" ] && [ "$(git -C "feeds/$feed" rev-parse HEAD)" = "$pin" ] || {
		echo "Feed $feed is not at its pinned commit" >&2; exit 1
	}
	if git -C "feeds/$feed" apply --reverse --check "$patch" 2>/dev/null; then
		echo "Already applied to $feed: ${entry#*:}"
		continue
	fi
	git -C "feeds/$feed" apply --check "$patch"
	git -C "feeds/$feed" apply "$patch"
	echo "Applied to $feed: ${entry#*:}"
done

./scripts/feeds update -i
./scripts/feeds uninstall -a
# Install the selected roots and their dependencies, rather than indexing
# every unrelated UI/audio package into this device's Kconfig namespace.
./scripts/feeds install mihomo-meta
./scripts/feeds install luci luci-ssl luci-i18n-base-zh-cn htop zram-swap \
	luci-app-nikki luci-i18n-nikki-zh-cn \
	luci-proto-wireguard luci-app-tailscale-community luci-i18n-tailscale-community-zh-cn \
	luci-app-cpufreq luci-i18n-cpufreq-zh-cn conntrack iperf3 pciutils usbutils

# Only honk and its LuCI page; the feed's dae, luci-app-dae, vmlinux-btf and
# vmlinux-btf-builtin stay uninstalled (honk's recipe no longer depends on
# vmlinux-btf, see the controlled patch).
./scripts/feeds install -p daeuniverse honk luci-app-honk luci-lib-daeuniverse \
	luci-i18n-daeuniverse-zh-cn

# Nikki's stable and alpha providers have symmetric CONFLICTS. Keeping both
# installed in the build package index creates a Kconfig dependency cycle.
./scripts/feeds uninstall mihomo-alpha

mkdir -p files
cp -a config/rc01/files/. files/
chmod 755 files/etc/board.d/99-rc01-wireless files/etc/uci-defaults/89-rc01-bypass
cp defconfig/qualcommbe-rc01.config .config
# Override the combined fragments, then verify the normalized selection below.
python3 -B scripts/rc01-go-bootstrap.py configure .config
make defconfig
sh scripts/validate-rc01-config.sh .config
# Parse expanded install recipes before spending time compiling a toolchain.
make -C package/emortal/rc01-platform TOPDIR="$PWD" -n compile >/dev/null
