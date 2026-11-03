# SPDX-License-Identifier: GPL-2.0-only
# Sourced by sysupgrade. Every preflight operation is read-only on NAND.
# Alternate read roots let offline tests exercise the same checks.
RC01_PROC="${RC01_PROC:-/proc}"
RC01_SYSFS="${RC01_SYSFS:-/sys}"
RC01_ENV_CONFIG="${RC01_ENV_CONFIG:-/etc/fw_env.config}"

rc01_fail() {
	echo "RC01 preflight: $*" >&2
	return 1
}

rc01_mtd_index() {
	awk -v wanted="$1" '
		{ name=$4; gsub(/"/, "", name) }
		tolower(name) == tolower(wanted) {
			idx=$1; sub(/^mtd/, "", idx); sub(/:$/, "", idx); count++
		}
		END { if (count == 1) print idx; else exit 1 }
	' "$RC01_PROC/mtd"
}

rc01_check_layout() {
	local root env map value machid config
	root="$(rc01_mtd_index rootfs)" || { rc01_fail 'rootfs partition is missing or ambiguous'; return 1; }
	env="$(rc01_mtd_index 0:appsblenv)" || { rc01_fail 'APPSBLENV partition is missing or ambiguous'; return 1; }
	[ "$(cat "$RC01_SYSFS/class/mtd/mtd$root/size")" = 125829120 ] &&
		[ "$(cat "$RC01_SYSFS/class/mtd/mtd$root/erasesize")" = 131072 ] &&
		[ "$(cat "$RC01_SYSFS/class/mtd/mtd$root/writesize")" = 2048 ] || {
		rc01_fail 'requires the verified 120 MiB rootfs / 128 KiB erase / 2 KiB page layout'
		return 1
	}
	[ "$(cat "$RC01_SYSFS/class/mtd/mtd$env/size")" = 524288 ] &&
		[ "$(cat "$RC01_SYSFS/class/mtd/mtd$env/erasesize")" = 131072 ] || {
		rc01_fail 'unexpected APPSBLENV NAND geometry'; return 1
	}
	[ "$(cat "$RC01_SYSFS/class/ubi/ubi0/mtd_num" 2>/dev/null)" = "$root" ] &&
		[ "$(cat "$RC01_SYSFS/class/ubi/ubi0_0/name" 2>/dev/null)" = kernel ] &&
		[ "$(cat "$RC01_SYSFS/class/ubi/ubi0_1/name" 2>/dev/null)" = rootfs ] || {
		rc01_fail 'expected kernel/rootfs at UBI0 volume IDs 0/1'; return 1
	}
	map="$(awk 'NF && $1 !~ /^#/ { print }' "$RC01_ENV_CONFIG")"
	set -- $map
	[ "$#" -eq 5 ] && [ "$1" = "/dev/mtd$env" ] || {
		rc01_fail 'environment map must contain one non-redundant entry resolved by partition name'
		return 1
	}
	shift
	for value in "$@"; do
		echo "$value" | grep -Eq '^(0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)$' || {
			rc01_fail 'invalid numeric field in environment map'; return 1
		}
	done
	[ "$(($1))" -eq 0 ] && [ "$(($2))" -eq 65536 ] &&
		[ "$(($3))" -eq 131072 ] && [ "$(($4))" -eq 2 ] || {
		rc01_fail 'environment map must use offset 0, size 64 KiB, erase 128 KiB, range 2 sectors'
		return 1
	}
	machid="$(FW_ENV_STRICT_CRC=1 fw_printenv -n machid 2>/dev/null)" || {
		rc01_fail 'environment CRC/read failed; refusing defaults'; return 1
	}
	case "$machid" in 8050301|0x8050301) ;; *) rc01_fail 'unexpected bootloader machid'; return 1 ;; esac
	config="$(FW_ENV_STRICT_CRC=1 fw_printenv -n config_name 2>/dev/null || :)"
	case "$config" in
		''|config@al02-c4|config@al02-c6) ;;
		*) rc01_fail 'unsupported explicit bootloader config_name'; return 1 ;;
	esac
}

rc01_check_image() (
	local work directory config kernel_ref fdt_ref first_kernel= first_fdt=
	local kernel_bytes root_bytes payload_pebs
	rc01_check_layout || exit 1
	command -v fdtget >/dev/null || { rc01_fail 'fdtget is required'; exit 1; }
	work="$(mktemp -d /tmp/rc01-preflight.XXXXXX)" || exit 1
	trap 'rm -rf "$work"' EXIT
	trap 'exit 1' INT TERM
	get_image "$1" | tar -tf - > "$work/members" || {
		rc01_fail 'not a readable sysupgrade tar'; exit 1
	}
	directory="$(awk '/^sysupgrade-[^\/]+\/kernel$/ { sub(/\/kernel$/, ""); dir=$0; count++ }
		END { if (count == 1) print dir; else exit 1 }' "$work/members")" || {
		rc01_fail 'expected exactly one sysupgrade kernel'; exit 1
	}
	get_image "$1" | tar -xOf - "$directory/kernel" > "$work/kernel" || exit 1
	kernel_bytes="$(wc -c < "$work/kernel")"
	[ "$kernel_bytes" -gt 0 ] && [ "$kernel_bytes" -le 16777216 ] || {
		rc01_fail 'unexpected FIT kernel size'; exit 1
	}
	for config in config@al02-c4 config@al02-c6; do
		kernel_ref="$(fdtget -t s "$work/kernel" "/configurations/$config" kernel)" &&
			fdt_ref="$(fdtget -t s "$work/kernel" "/configurations/$config" fdt)" || {
			rc01_fail 'image must provide both C4 and C6 FIT configurations'; exit 1
		}
		if [ -n "$first_kernel" ]; then
			[ "$kernel_ref" = "$first_kernel" ] && [ "$fdt_ref" = "$first_fdt" ] || {
				rc01_fail 'C4/C6 aliases must reference the same kernel and DTB'; exit 1
			}
		fi
		first_kernel="$kernel_ref"; first_fdt="$fdt_ref"
	done
	get_image "$1" | tar -xOf - "$directory/root" > "$work/root" || exit 1
	[ "$(head -c 4 "$work/root")" = hsqs ] || {
		rc01_fail 'expected a SquashFS rootfs'; exit 1
	}
	root_bytes="$(wc -c < "$work/root")"
	[ "$root_bytes" -gt 0 ] || { rc01_fail 'missing rootfs payload'; exit 1; }
	# 960 PEBs total. Reserve 40 for the 256 MiB NAND bad-block budget,
	# four for UBI overhead, and at least 32 MiB of data-volume LEBs.
	payload_pebs=$(( (kernel_bytes + 126975) / 126976 + (root_bytes + 126975) / 126976 ))
	[ $((payload_pebs + 44 + (33554432 + 126975) / 126976)) -le 960 ] || {
		rc01_fail 'image leaves less than the required rootfs_data budget'; exit 1
	}
)
