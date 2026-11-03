#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
cd "$(dirname "$0")/.."
feed="${1:-feeds/packages}"
expected=48f609040f2e4beb16c1aa62af6a6aaafd2eb6cd
[ "$(git -C "$feed" rev-parse HEAD)" = "$expected" ] || {
	echo 'Unexpected packages revision; review the RC01 Go patch before updating' >&2
	exit 1
}
patch="$PWD/config/rc01/patches/golang/100-rc01-local-toolchain.patch"
if git -C "$feed" apply --reverse --check "$patch" 2>/dev/null; then
	exit 0
fi
git -C "$feed" apply --check "$patch"
git -C "$feed" apply "$patch"
