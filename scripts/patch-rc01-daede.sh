#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")/.."
feed="${1:-feeds/daede}"
expected=1eedcafd1cd4d8a89ac928be6608df76ffb38f78
[ "$(git -C "$feed" rev-parse HEAD)" = "$expected" ] || {
	echo 'Unexpected daede revision; review the local patches before updating' >&2
	exit 1
}
for patch in config/rc01/patches/daede/*.patch; do
	patch="$(pwd)/$patch"
	if git -C "$feed" apply --reverse --check "$patch" 2>/dev/null; then
		continue
	fi
	git -C "$feed" apply --check "$patch"
	git -C "$feed" apply "$patch"
done
