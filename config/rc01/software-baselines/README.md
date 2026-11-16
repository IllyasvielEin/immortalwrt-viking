These recipe/helper preimages come from the reviewed software feeds:

- `tailscale.Makefile`, Go recipes/helpers and `inputs/go/`: immortalwrt/packages `48f609040f2e4beb16c1aa62af6a6aaafd2eb6cd`.
- `mihomo.Makefile`: nikkinikki-org/OpenWrt-nikki `78e1a751d55ed0762811f054ee699049473da7b8`.
- dae/daed/LuCI recipes, `daede-pins.env` and `inputs/daede/`: kenzok8/openwrt-daede `3520a79bd9c68843801dc89d7d6e155d2c43c833`.

Copyright and license notices remain in the files. `recipe-closure.json` binds the complete selected Go build subtree and the dae/daed/LuCI/BTF build and lifecycle trees, including filenames and executable modes. A file addition/deletion, changed build helper, dependency, installation rule, initialization script, source patch or BTF recipe requires review. Unused daemon feed CI workflows are outside this build closure.

Only the manifest's explicit fields may vary: the stable Go 1.27 patch/source hash/package release; daemon recipe versions, content-addressed filenames, source hashes and package releases; LuCI version/package release; and known component pin values. The bootstrap source remains 1.24.13. Pin keys, comments and structure remain exact. Unsupported changes fail before applying the local patches; successful patch application alone is insufficient.

Snapshots include the complete Go inputs and the daemon files needed to exercise the real local patch offline. The remaining daemon closure files are bound by their source commit, path, mode and SHA256. Replay derives recipes and provenance again from these inputs instead of trusting auxiliary facts in an external source lock.
The production Python resolver owns all three local patch adapters; standalone
`patch-rc01-*` entry points have been removed. Offline tests exercise the same
derivation, guarded writer and verifier used by preparation.

`locks/with-dae.json` is a completed locked-mode source lock for the current
reviewed platform inputs and the five commits in `config/rc01/feeds.conf`.
It is generated through `scripts/prepare-rc01.sh` with `RC01_UPDATE_MODE=locked`
and `RC01_WITH_DAE=true`; it is source provenance, not firmware build acceptance.
Replay with the same variant still checks every platform input and re-derives
the recipes from pinned feed objects. A platform change requires a newly
prepared and reviewed lock; historical locks are deliberately not migrated.
The build guide documents replay and refresh commands.
The lock is committed after the change it binds, so its `build_commit` is the
parent of the commit that records it. `build_commit` and `resolved_at`
describe that original plan; replay preserves
its `source_date_epoch` rather than using the replay checkout's commit time.
Only the selected `PLATFORM_PATHS` are bound, not the complete source tree
(in particular, the `luci-app-rc01` status UI is outside this set).
