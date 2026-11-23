#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")/.."
config="${1:-.config}"
[ -f "$config" ] || { echo "Configuration not found: $config" >&2; exit 1; }

# Check actual Kconfig output and the external binary, not only the CI environment.
python3 -B scripts/rc01-go-bootstrap.py validate-config "$config"

require_y() {
	grep -Fqx "$1=y" "$config" || { echo "Required symbol is not enabled: $1" >&2; exit 1; }
}

for symbol in \
	CONFIG_TARGET_qualcommbe CONFIG_TARGET_qualcommbe_ipq95xx \
	CONFIG_TARGET_qualcommbe_ipq95xx_DEVICE_xiaomi_be10000 \
	CONFIG_TARGET_ROOTFS_SQUASHFS CONFIG_USE_APK CONFIG_CCACHE \
	CONFIG_PACKAGE_kmod-qcom-ppe CONFIG_PACKAGE_kmod-ath12k \
	CONFIG_ATH12K_THERMAL CONFIG_PACKAGE_MAC80211_DEBUGFS \
	CONFIG_PACKAGE_ath12k-firmware-qcn9274-ddwrt CONFIG_PACKAGE_ipq-wifi-xiaomi_be10000 \
	CONFIG_PACKAGE_wpad-openssl CONFIG_PACKAGE_rc01-platform CONFIG_PACKAGE_rc01-rgb \
	CONFIG_PACKAGE_kmod-hwmon-emc2305 CONFIG_PACKAGE_kmod-thermal CONFIG_PACKAGE_kmod-ramoops \
	CONFIG_KERNEL_DEBUG_INFO \
	CONFIG_PACKAGE_fdt-utils CONFIG_PACKAGE_uboot-envtools \
	CONFIG_PACKAGE_mihomo-meta CONFIG_PACKAGE_nikki CONFIG_PACKAGE_luci-app-nikki \
	CONFIG_PACKAGE_luci-app-router-tool CONFIG_PACKAGE_luci-app-rc01 \
	CONFIG_LUCI_LANG_zh_Hans CONFIG_PACKAGE_luci-i18n-rc01-zh-cn \
	CONFIG_PACKAGE_luci-app-rc01-rgb CONFIG_PACKAGE_luci-i18n-rc01-rgb-zh-cn \
	CONFIG_PACKAGE_luci-i18n-base-zh-cn CONFIG_PACKAGE_luci-i18n-nikki-zh-cn \
	CONFIG_PACKAGE_luci-i18n-tailscale-community-zh-cn CONFIG_PACKAGE_luci-ssl \
	CONFIG_PACKAGE_htop CONFIG_PACKAGE_tcpdump CONFIG_PACKAGE_zram-swap \
	CONFIG_PACKAGE_ca-certificates CONFIG_PACKAGE_kmod-wireguard CONFIG_PACKAGE_wireguard-tools \
	CONFIG_PACKAGE_luci-proto-wireguard CONFIG_PACKAGE_tailscale CONFIG_PACKAGE_luci-app-tailscale-community \
	CONFIG_PACKAGE_ethtool-full CONFIG_PACKAGE_tc-full CONFIG_PACKAGE_ip-bridge \
	CONFIG_PACKAGE_conntrack CONFIG_PACKAGE_iw-full CONFIG_PACKAGE_hostapd-utils \
	CONFIG_PACKAGE_iperf3 CONFIG_PACKAGE_pciutils CONFIG_PACKAGE_usbutils \
	CONFIG_PACKAGE_cpufreq CONFIG_PACKAGE_luci-app-cpufreq \
	CONFIG_PACKAGE_kmod-usb-storage CONFIG_PACKAGE_kmod-usb-storage-uas \
	CONFIG_PACKAGE_kmod-fs-ext4 CONFIG_PACKAGE_block-mount CONFIG_PACKAGE_e2fsprogs
do
	require_y "$symbol"
done

# Crash symbols need full DWARF (faddr2line); reduced debug info drops it.
! grep -Fqx 'CONFIG_KERNEL_DEBUG_INFO_REDUCED=y' "$config" || {
	echo 'CONFIG_KERNEL_DEBUG_INFO_REDUCED=y is not allowed: crash symbols need full debug information' >&2; exit 1
}

profiles="$(grep -Ec '^CONFIG_TARGET_.+_DEVICE_.+=y$' "$config" || :)"
[ "$profiles" -eq 1 ] || { echo "Expected one RC01 device, found $profiles" >&2; exit 1; }
grep -Fqx 'CONFIG_FEED_nikki=m' "$config" || { echo 'Do not mix the Nikki release binary feed into this source build' >&2; exit 1; }

for package in \
	mihomo-alpha sing-box luci-app-homeproxy luci-app-openclash \
	luci-app-passwall luci-app-passwall2 \
	dae daed daed-geoip daed-geosite luci-app-dae luci-app-daed luci-app-daede luci-i18n-daed-zh-cn vmlinux-btf \
	irqbalance luci-app-qca-ppe-qos qca-ppe-qos qosify sqm-scripts \
	tcpdump-mini ethtool iw tc-tiny tc-bpf \
	samba4-server ksmbd-server luci-app-samba4 \
	miniupnpd miniupnpd-nftables luci-app-upnp
do
	if grep -Eq "^CONFIG_PACKAGE_$package=[ym]$" "$config"; then
		echo "Unwanted package selected: $package" >&2
		exit 1
	fi
done

grep -Fq 'ATH12K_PPE_DS_SUPPORT' package/kernel/mac80211/ath.mk || {
	echo 'Native ath12k Direct Switch support disappeared' >&2; exit 1
}
grep -Fq 'DEVICE_DTS_CONFIG_ALIASES := config@al02-c6' target/linux/qualcommbe/image/ipq95xx.mk || exit 1
python3 -B scripts/rc01-layout.py --check-generated >/dev/null
grep -Fq 'include $(TOPDIR)/config/rc01/layout.mk' target/linux/qualcommbe/image/ipq95xx.mk || exit 1
grep -Fq 'IMAGE_SIZE := $(RC01_ROOTFS_SIZE_KIB)k' target/linux/qualcommbe/image/ipq95xx.mk || exit 1
grep -Fq 'fw_env_strict_crc_required()' package/boot/uboot-tools/patches/015-envtools-strict-crc.patch || exit 1
echo "RC01 configuration validation passed"
