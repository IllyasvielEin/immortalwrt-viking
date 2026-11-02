#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Check the compiled kernel, not just OpenWrt's Kconfig requests.
set -eu
config="${1:?usage: validate-rc01-daed-kernel.sh kernel.config vmlinux}"
vmlinux="${2:?usage: validate-rc01-daed-kernel.sh kernel.config vmlinux}"
for symbol in \
	BPF BPF_SYSCALL BPF_JIT CGROUPS CGROUP_BPF KPROBES KPROBE_EVENTS \
	NET_INGRESS NET_EGRESS NET_CLS_ACT BPF_STREAM_PARSER DEBUG_INFO \
	DEBUG_INFO_BTF BPF_EVENTS NET_NS XDP_SOCKETS
do
	grep -Fqx "CONFIG_$symbol=y" "$config" || {
		echo "daed kernel prerequisite missing: CONFIG_$symbol=y" >&2; exit 1
	}
done
for symbol in NET_SCH_INGRESS NET_CLS_BPF VETH XDP_SOCKETS_DIAG; do
	grep -Eq "^CONFIG_$symbol=[ym]$" "$config" || {
		echo "daed kernel prerequisite missing: CONFIG_$symbol" >&2; exit 1
	}
done
! grep -Fqx 'CONFIG_DEBUG_INFO_REDUCED=y' "$config" || exit 1
readelf -SW "$vmlinux" | grep -Eq '[[:space:]]\.BTF[[:space:]]' || {
	echo 'Compiled vmlinux has no BTF section' >&2; exit 1
}
echo 'Compiled daed kernel prerequisites and vmlinux BTF verified'
