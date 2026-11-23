#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Check the compiled kernel for the honk variant, not just OpenWrt's Kconfig
# requests: honk's eBPF datapath needs these options and the kernel's own BTF.
set -eu
usage='usage: validate-rc01-honk-kernel.sh kernel.config vmlinux'
config="${1:?$usage}"
vmlinux="${2:?$usage}"
[ -f "$config" ] || { echo "Kernel configuration not found: $config" >&2; exit 1; }
[ -f "$vmlinux" ] || { echo "vmlinux not found: $vmlinux" >&2; exit 1; }

# Built into the kernel (the NET_CLS_ACT and NF_TABLES_INET switches are bool).
for symbol in \
	BPF BPF_SYSCALL BPF_JIT CGROUPS CGROUP_BPF NET_CLS_ACT NET_XGRESS \
	DEBUG_INFO_BTF NET_NS NF_TABLES_INET
do
	grep -Fqx "CONFIG_$symbol=y" "$config" || {
		echo "honk kernel prerequisite missing: CONFIG_$symbol=y" >&2; exit 1
	}
done
# OpenWrt normally ships these as modules.
for symbol in VETH NF_TABLES NFT_CT NFT_QUEUE NETFILTER_NETLINK_QUEUE NET_SCH_INGRESS NET_CLS_BPF; do
	grep -Eq "^CONFIG_$symbol=[ym]\$" "$config" || {
		echo "honk kernel prerequisite missing: CONFIG_$symbol=y or =m" >&2; exit 1
	}
done
! grep -Fqx 'CONFIG_DEBUG_INFO_REDUCED=y' "$config" || {
	echo 'CONFIG_DEBUG_INFO_REDUCED=y leaves vmlinux without usable BTF' >&2; exit 1
}
readelf -SW "$vmlinux" | grep -Eq '[[:space:]]\.BTF[[:space:]]' || {
	echo 'Compiled vmlinux has no BTF section' >&2; exit 1
}
echo 'Compiled honk kernel prerequisites and vmlinux BTF verified'
