# Anonymous development integration

2026-11-12. Local integration branch: `rc01/anonymous-rebase-20261112`.
Source integration result: `a7fc1b49238f392bfdd074749e0b1680098b256c`.

## History and scope

- Original repository: `IllyasvielEin/immortalwrt-viking`.
- Original `rc01/custom`: `cf8e09591ffeef7d748914962865cee74aea6046`.
- Existing followup/PR #1 head: `d1a7fdc6c41c7a1121c5ffb09e8267fc17c66067`.
- Anonymous snapshot root: `f4d05b5f675eff4278eaadaa6b613d90c0974460`;
  developed head: `b74e6cde6f8196639805d2f309d8b3d706be3740`.
- Rebased the development history onto the original followup head using
  `git rebase --rebase-merges --onto <followup-head> <snapshot-root>`.
  Existing module branch structure and authorship remain. Conflicts were resolved
  against the reviewed source commits. A final integration correction restores
  three source-note/document resolutions that automatic merge recreation omitted.
- All 79 development delta paths match the anonymous head's blobs and modes.
  The original network-default test fixture and editor configuration are preserved;
  neither belongs to the development delta. The snapshot root is not introduced
  as an ancestor of the integrated branch.
- The existing `rc01/custom` checkout and its uncommitted build-document edit
  remain unchanged. The existing followup worktree now checks out the integration
  branch; the old followup branch pointer is retained.

## PR assessment

[PR #1](https://github.com/IllyasvielEin/immortalwrt-viking/pull/1) was draft/open
at inspection, with base/head equal to the original commits listed above. Its
five commits are already ancestors of this integration result and were the source
of the anonymous snapshot. They must not be applied a second time here.

Remote `rc01/custom` still lacks those five commits and the subsequent development.
The PR therefore remains relevant for eventual remote delivery, but its current
head does not contain the new development. No remote branch, PR state, or CI run
was changed by this integration.

## Validation

- Full offline regression on the integrated source: **620 tests passed**,
  42.005 seconds, zero failures/errors/skips. This includes the newer application
  source recipes present in `b74e6cd`.
- Reused the anonymous workspace's prepared Linux 6.18.60, backports 7.2,
  patched firewall4/dnsmasq, BusyBox, host ucode, fixed LuCI inputs and packages
  feed. Only generated inputs were reused; the tests ran from this integrated
  checkout. `uv run --offline --no-project python -B` used a writable temporary
  cache. Log: `build_dir/rc01-local-rebase-20261112/full-regression.log`.
- Exact development path/blob/mode comparison and original-only file preservation
  passed. PR head ancestry passed. Layout generated-file check and
  `git diff --check d1a7fdc6c4 HEAD` passed.

No target firmware/package build or hardware validation was performed. No push,
publication, flashing, device access, or remote PR merge was performed.
