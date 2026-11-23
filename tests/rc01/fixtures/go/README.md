Pristine copies of `golang-compiler.mk`, `golang-host-build.mk` and `golang-values.mk`
from immortalwrt/packages `48f609040f2e4beb16c1aa62af6a6aaafd2eb6cd` (the pin in
`config/rc01/feeds.conf`), plus version-field stubs for the Go compiler and bootstrap
recipes. They let the offline tests exercise `config/rc01/patches/golang/` without a
feed checkout; this is not a snapshot system. Whether the patches apply to the real
pinned feed is checked by `git apply --check` in `scripts/prepare-rc01.sh`.
