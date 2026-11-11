# SPDX-License-Identifier: GPL-2.0-only
# Sourced by sysupgrade. Every preflight operation is read-only on NAND.
# Alternate read roots let offline tests exercise the same checks.
RC01_PROC="${RC01_PROC:-/proc}"
RC01_SYSFS="${RC01_SYSFS:-/sys}"
RC01_ENV_CONFIG="${RC01_ENV_CONFIG:-/etc/fw_env.config}"
RC01_LAYOUT_FILE="${RC01_LAYOUT_FILE:-/lib/upgrade/rc01-layout.sh}"

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
	local root= env= map value machid config name offset size index count bad
	[ -r "$RC01_LAYOUT_FILE" ] || { rc01_fail 'layout contract is missing'; return 1; }
	# This generated projection is installed and copied into upgrade ramfs.
	. "$RC01_LAYOUT_FILE" || return 1
	count="$(awk '/^mtd[0-9]+:/ { count++ } END { print count+0 }' "$RC01_PROC/mtd")"
	[ "$count" = "$RC01_PARTITION_COUNT" ] || { rc01_fail 'unexpected partition count'; return 1; }
	while IFS='|' read -r name offset size; do
		index="$(rc01_mtd_index "$name")" || { rc01_fail 'partition is missing or ambiguous'; return 1; }
		[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/offset")" = "$offset" ] &&
			[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/size")" = "$size" ] &&
			[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/erasesize")" = "$RC01_NAND_ERASE" ] &&
			[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/writesize")" = "$RC01_NAND_PAGE" ] || {
			rc01_fail 'partition geometry does not match the identity-preserved layout'; return 1
		}
		case "$name" in
			rootfs) root="$index" ;;
			0:APPSBLENV) env="$index" ;;
			0:LICENSE|bdata)
				[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/bad_blocks")" = 0 ] &&
					[ "$(cat "$RC01_SYSFS/class/mtd/mtd$index/ecc_failures")" = 0 ] || {
					rc01_fail 'identity partition bad-block/ECC check failed'; return 1
				}
				;;
		esac
	done <<EOF
$RC01_LAYOUT_PARTITIONS
EOF
	[ -n "$root" ] && [ -n "$env" ] || { rc01_fail 'required layout names missing'; return 1; }
	bad="$(cat "$RC01_SYSFS/class/mtd/mtd$root/bad_blocks")"
	case "$bad" in ''|*[!0-9]*) rc01_fail 'invalid rootfs bad-block count'; return 1 ;; esac
	[ "$bad" -le "$RC01_BAD_BLOCK_RESERVE" ] &&
		[ "$(cat "$RC01_SYSFS/class/mtd/mtd$root/ecc_failures")" = 0 ] || {
		rc01_fail 'rootfs exceeds the bad-block reserve or has uncorrectable ECC'; return 1
	}
	[ "$(cat "$RC01_SYSFS/class/ubi/ubi0/mtd_num" 2>/dev/null)" = "$root" ] &&
		[ "$(cat "$RC01_SYSFS/class/ubi/ubi0_0/name" 2>/dev/null)" = kernel ] &&
		[ "$(cat "$RC01_SYSFS/class/ubi/ubi0_1/name" 2>/dev/null)" = rootfs ] &&
		[ "$(cat "$RC01_SYSFS/class/ubi/ubi0_2/name" 2>/dev/null)" = rootfs_data ] || {
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
	[ "$(($1))" -eq 0 ] && [ "$(($2))" -eq "$RC01_ENV_BYTES" ] &&
		[ "$(($3))" -eq "$RC01_ENV_ERASE" ] && [ "$(($4))" -eq "$RC01_ENV_RANGE" ] || {
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
	# Reserve whole-NAND bad-block capacity, UBI overhead and writable data.
	payload_pebs=$(( (kernel_bytes + RC01_LEB_BYTES - 1) / RC01_LEB_BYTES + (root_bytes + RC01_LEB_BYTES - 1) / RC01_LEB_BYTES ))
	[ $((payload_pebs + RC01_RESERVE_PEBS + (RC01_MIN_DATA_BYTES + RC01_LEB_BYTES - 1) / RC01_LEB_BYTES)) -le "$RC01_ROOTFS_PEBS" ] || {
		rc01_fail 'image leaves less than the required rootfs_data budget'; exit 1
	}
)
