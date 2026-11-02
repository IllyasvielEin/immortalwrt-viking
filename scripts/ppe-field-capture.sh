#!/bin/sh
# Collect bounded, time-correlated AX PPE evidence without changing forwarding.
set -eu
umask 077

usage() {
	cat <<'EOF'
Usage: ppe-field-capture.sh CLIENT_IPV6 LAN_PORT PPP_DEVICE PHYSICAL_WAN [SECONDS]
Example: sh ppe-field-capture.sh fd42:618:1102:100::2 lan1 pppoe-wan wan 120

Run on the affected router after verifying its actual interface names.
Captures all protocols, including PPPoE, VLAN and ICMPv6, on three interfaces.
Each interface uses a 2 x 2 MB ring; files retain the most recent traffic.
Duration: 10..600 seconds (default 120). Ctrl-C stops and finalizes evidence.
No firewall, offload, network configuration or debug register writes are made.
EOF
}

[ "${1-}" != --help ] || { usage; exit 0; }
[ "$#" -ge 4 ] && [ "$#" -le 5 ] || { usage >&2; exit 2; }
client=$1
lan=$2
ppp=$3
physical=$4
seconds=${5-120}
case "$client" in
	''|*[!0-9a-fA-F:]*) echo 'Use an unscoped IPv6 literal.' >&2; exit 2 ;;
esac
case "$client" in *:*) ;; *) echo 'An IPv6 address is required.' >&2; exit 2 ;; esac
case "$seconds" in ''|*[!0-9]*) echo 'Invalid duration.' >&2; exit 2 ;; esac
[ "${#seconds}" -le 3 ] && [ "$seconds" -ge 10 ] && [ "$seconds" -le 600 ] || {
	echo 'Duration must be 10..600 seconds.' >&2; exit 2;
}
for dev in "$lan" "$ppp" "$physical"; do
	case "$dev" in ''|*[!a-zA-Z0-9_.:@-]*) echo 'Invalid interface name.' >&2; exit 2 ;; esac
	[ -d "/sys/class/net/$dev" ] || { echo "Interface absent: $dev" >&2; exit 2; }
done
[ "$lan" != "$ppp" ] && [ "$lan" != "$physical" ] && [ "$ppp" != "$physical" ] || {
	echo 'Specify three different interfaces.' >&2; exit 2;
}
for command in tcpdump ip awk df id; do
	command -v "$command" >/dev/null 2>&1 || { echo "Missing: $command" >&2; exit 2; }
done
[ "$(id -u)" -eq 0 ] || { echo 'Run as root on the router.' >&2; exit 2; }
ppe=/sys/kernel/debug/3a000000.ppe
[ -r "$ppe/offload_failures" ] || {
	echo 'AX PPE offload_failures interface is unavailable.' >&2; exit 2;
}
available=$(df -Pk /tmp | awk 'END { print $4 }')
case "$available" in ''|*[!0-9]*) echo 'Cannot check /tmp free space.' >&2; exit 2 ;; esac
[ "$available" -ge 65536 ] || { echo 'At least 64 MiB free in /tmp is required.' >&2; exit 2; }
out=$(mktemp -d /tmp/ppe-field-XXXXXX)
pids=''
interrupted=0
capture_failed=0

stamp() {
	date -u '+wallclock_utc %Y-%m-%dT%H:%M:%SZ'
	printf 'uptime_seconds '; cat /proc/uptime
	printf 'boot_id '; cat /proc/sys/kernel/random/boot_id
}

stop_captures() {
	for pid in $pids; do kill -INT "$pid" 2>/dev/null || true; done
	for pid in $pids; do wait "$pid" 2>/dev/null || true; done
	pids=''
}
trap stop_captures EXIT
trap 'interrupted=1; stop_captures' INT TERM

optional() {
	name=$1
	shift
	if "$@" > "$name" 2>&1; then
		printf '%s OK\n' "$name" >> "$out/command-status.txt"
	else
		status=$?
		printf '%s ERROR %s\n' "$name" "$status" >> "$out/command-status.txt"
	fi
}

sample() {
	folder=$out/$1
	mkdir "$folder"
	stamp > "$folder/time-before.txt"
	for name in offload offload_failures completion_mismatches; do
		[ ! -r "$ppe/$name" ] || optional "$folder/$name.txt" cat "$ppe/$name"
	done
	optional "$folder/proc-stat.txt" cat /proc/stat
	optional "$folder/softnet-stat.txt" cat /proc/net/softnet_stat
	optional "$folder/net-dev.txt" cat /proc/net/dev
	stamp > "$folder/time-after.txt"
}

state() {
	folder=$out/$1
	mkdir "$folder"
	stamp > "$folder/time-before.txt"
	optional "$folder/ipv6-addresses.txt" ip -6 address show
	optional "$folder/ipv6-rules.txt" ip -6 rule show
	optional "$folder/ipv6-routes.txt" ip -6 route show table all
	optional "$folder/ipv6-neighbours.txt" ip -6 neigh show
	optional "$folder/links.txt" ip -s -d link show
	optional "$folder/nft-ruleset.txt" nft -a list ruleset
	optional "$folder/bridge-vlan.txt" bridge -d vlan show
	optional "$folder/bridge-fdb.txt" bridge -s fdb show
	for dev in "$lan" "$ppp" "$physical"; do
		optional "$folder/$dev-qdisc.txt" tc -s qdisc show dev "$dev"
		optional "$folder/$dev-ingress.txt" tc -s filter show dev "$dev" ingress
		optional "$folder/$dev-egress.txt" tc -s filter show dev "$dev" egress
		optional "$folder/$dev-ethtool.txt" ethtool -k "$dev"
		optional "$folder/$dev-port-counters.txt" ethtool -S "$dev"
	done
	# An active hardware table scan can hold flow_lock for many MMIO reads.
	# Keep those scans out of periodic collection and leave them for a targeted run.
	for name in pipeline_state queue_drops offload_rejects; do
		[ ! -r "$ppe/$name" ] || optional "$folder/$name.txt" cat "$ppe/$name"
	done
	if [ -r /proc/net/nf_conntrack ]; then
		# Bound text output; it is evidence of the sample, not a full flow census.
		awk 'NR <= 20000 {print} END {if (NR > 20000) print "CAPTURE_TRUNCATED_AFTER_20000_LINES"}' \
			/proc/net/nf_conntrack > "$folder/conntrack.txt"
	fi
	optional "$folder/dmesg.txt" dmesg
	stamp > "$folder/time-after.txt"
}

{
	stamp
	printf 'client_ipv6 %s\nlan_port %s\nppp_device %s\nphysical_wan %s\nseconds %s\n' \
		"$client" "$lan" "$ppp" "$physical" "$seconds"
	printf 'capture_filter NONE\nring_per_interface 2_files_of_2_decimal_MB\n'
	for dev in "$lan" "$ppp" "$physical"; do
		printf 'interface %s ifindex=' "$dev"; cat "/sys/class/net/$dev/ifindex"
		printf 'mac '; cat "/sys/class/net/$dev/address"
	done
} > "$out/session.txt"
optional "$out/board.json" ubus call system board
optional "$out/release.txt" cat /etc/openwrt_release
optional "$out/tcpdump-version.txt" tcpdump --version
state state-before
sample before

for role in lan ppp physical; do
	case "$role" in lan) dev=$lan ;; ppp) dev=$ppp ;; physical) dev=$physical ;; esac
	# No BPF expression: retain control packets and every L2 encapsulation.
	tcpdump -p -nn -Z root -i "$dev" -s 0 -U -C 2 -W 2 -w "$out/$role.pcap" \
		> "$out/$role-tcpdump.txt" 2>&1 &
	pids="$pids $!"
done
sleep 1 || true
if [ "$interrupted" -eq 0 ]; then
	for pid in $pids; do
		if ! kill -0 "$pid" 2>/dev/null; then
			echo "Capture failed to start; inspect $out/*-tcpdump.txt" >&2
			exit 1
		fi
	done
fi
printf 'Collecting in %s; Ctrl-C stops and finalizes.\n' "$out" >&2
elapsed=0
while [ "$elapsed" -lt "$seconds" ] && [ "$interrupted" -eq 0 ]; do
	sleep 2 || true
	[ "$interrupted" -eq 0 ] || break
	elapsed=$((elapsed + 2))
	sample "sample-$elapsed"
	for pid in $pids; do
		if ! kill -0 "$pid" 2>/dev/null; then
			echo "Capture exited early; inspect $out/*-tcpdump.txt" >&2
			interrupted=1
			capture_failed=1
		fi
	done
done
stop_captures
sample after
state state-after
{
	stamp
	printf 'interrupted_or_early_exit %s\n' "$interrupted"
	printf 'capture_failed %s\n' "$capture_failed"
	printf '%s\n' \
		'Ring files retain recent traffic, not necessarily the full interval.' \
		'Each pcap records software taps; hardware-forwarded packets can bypass them.' \
		'Capture the PPPoE peer physical port to verify transmission beyond the DUT.' \
		'Inspect tcpdump logs for kernel capture drops and command-status.txt for missing evidence.'
} > "$out/completion.txt"
if command -v sha256sum >/dev/null 2>&1; then
	(cd "$out" && find . -type f ! -name SHA256SUMS -exec sha256sum '{}' \;) > "$out/SHA256SUMS"
fi
printf '%s\n' "$out"
[ "$capture_failed" -eq 0 ]
