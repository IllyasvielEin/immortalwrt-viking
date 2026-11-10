These recipe/helper preimages come from the reviewed software feeds:

- `tailscale.Makefile`, Go recipes/helpers and `inputs/go/`: immortalwrt/packages `48f609040f2e4beb16c1aa62af6a6aaafd2eb6cd`.
- `mihomo.Makefile`: nikkinikki-org/OpenWrt-nikki `78e1a751d55ed0762811f054ee699049473da7b8`.
- dae/daed/LuCI recipes, `daede-pins.env` and `inputs/daede/`: kenzok8/openwrt-daede `3520a79bd9c68843801dc89d7d6e155d2c43c833`.

Copyright and license notices remain in the files. `recipe-closure.json` binds the complete selected Go build subtree and the dae/daed/LuCI/BTF build and lifecycle trees, including filenames and executable modes. A file addition/deletion, changed build helper, dependency, installation rule, initialization script, source patch or BTF recipe requires review. Unused daemon feed CI workflows are outside this build closure.

Only the manifest's explicit fields may vary: the stable Go 1.27 patch/source hash/package release; daemon recipe versions, content-addressed filenames, source hashes and package releases; LuCI version/package release; and known component pin values. The bootstrap source remains 1.24.13. Pin keys, comments and structure remain exact. Unsupported changes fail before applying the local patches; successful patch application alone is insufficient.

Snapshots include the complete Go inputs and the daemon files needed to exercise the real local patch offline. The remaining daemon closure files are bound by their source commit, path, mode and SHA256. Replay derives recipes and provenance again from these inputs instead of trusting auxiliary facts in an external source lock.
