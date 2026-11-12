# 上游 UP05 / FW-RESTART：firewall4 重启连续性（005 + 006）与结果边界

基线 `3c92b21d2b298123402f246fffcaae264f377e10`（UP01–03 已合入），分支 `dev/simplify-01-test-shells`，单个本地提交。
未构建目标包或固件，未运行真实 nft/ip/ubus，未连接设备。等待 Astra 独立验收，本分支不自行合入。
不依赖 UP04 候选，也没有读取它的 worktree。

## 来源

- 上游 VIKINGYFY/immortalwrt，固定读取点 `dd8de87169f1c1d091a81eb237341b2de09bd5bf`。004、005、006 三份 firewall4 补丁都是
  提交 `43d9a24aab2cebab2d9bdb47a194ea28872c9cb6` 新增的（父提交 `67bb668a`），证据为 `rc01-upstream-20261112/inventory/43d9….json`
  的文件列表与 blob。
  - 005 `restart-owned-tables-atomically`：blob `12169701ebf2…`，与 `download-manifest.json` 一致，字节不变。
  - 006 `keep-established-state-matching-live-on-restart`：blob `aaf2cc06fbfa…`，字节不变。
  - 作者头和 Signed-off-by 都保留。
- 不取：firewall4 源整体升级到 `3722ead`、上游 helper/WDS 前缀；`67bb668a`（据主线程核实，它引入的是 769）的任何内容。
  本树当前没有 769，所以不声称已经移植 769。
- 出处更正：UP02 把 004 误记为 `67bb668a`。本项把 `scripts/rc01-platform-sources.json` 的 `selected_commits`、
  `followup_selection` 和工作流的 `viking_selected_commits` 中的 67bb 换成 43d9，并把 43d9 的 scope 写成 004+005+006；
  另外更正了 004 补丁头的 RC01 说明和 UP02 文档里的出处。004 的 hunk 和行为都没有改。
  **合入时注意**：本树去掉 67bb 是因为它在这里是错误归属，不表示 769 被排除。UP04 合入时，要保留它为真正的 769 加上的 67bb 选择。
- `reviewed_head` 仍为 `7b3b0f0f…`，`full_merge` 仍为 false。`rc01-source-metadata.py` 的 `local_patchset` 原本已经覆盖
  `package/network/config/firewall4`，新补丁会计入摘要（已实测列出 005/006/100）。
- Makefile：只把 `PKG_RELEASE` 14→15。`PKG_SOURCE_VERSION` e6f600c、`PKG_SOURCE_DATE`、`PKG_MIRROR_HASH` 不变。
  桥范围、helper、kmod 单一归属以及网络默认都没有改。

## 改动

- `patches/005-…`、`patches/006-…`：上游原文。
  - restart 在同一把锁内只渲染一次 batch，batch 开头先 `delete` 自有的 inet/bridge 表，经 `nft -c` 检查后用一次 `nft -f` 应用。
  - `FW4_RESTART=1` 时，`check_flowtable`/`check_bridge_flowtable` 返回 false，不再生成对已删表里 flowtable 的重复删除。
  - stop 同时删除 inet 和 bridge 两张自有表；restart 不再调用 stop，所以已经停止的防火墙也可以直接 restart。
  - 006 把三条链的 `ct state vmap {…}` 改为 `ct state established,related accept`，可选的 `ct state invalid drop` 作为单独一条规则。
- `patches/100-rc01-report-restart-result-boundaries.patch`（本地，排在 006 之后）：
  - 把 check 和 apply 拆开记录阶段。渲染、check 或 apply 失败时，stderr 输出
    `Firewall restart: ruleset <render|check|apply> failed; owned nft tables were not replaced.`，然后尝试恢复旧 state
    （原来没有 state 时删掉新渲染的 state）。复制或删除失败时，另外输出 `unable to restore previous state file …` 或
    `unable to remove new state file …`，不会让人以为已经恢复。返回值保持失败。
  - `main.uc` 的 `run_includes()` 仍然会跑完全部 script include，但只要有一个失败就 `exit(1)`。restart 把这个状态写回 result，
    并输出 `new ruleset is active, but firewall includes failed.`。start/reload 也因此会返回 include 的失败状态（以前总是返回 0）。
  - CLI 总失败文案从上游的 “previous ruleset was kept” 改为 `Firewall restart failed.`：include 失败时新规则已经生效，原文案不对。
    没有加状态系统、备份框架，也不回滚已经成功应用的 nft 规则。

## 三个成功边界

1. **nft 应用**：只有 render、check、apply 都成功，表才会被替换。原子的是这一次 nft 事务；失败时新 batch 没有提交，
   旧的自有表保留（这里依据 nft 批处理语义，本轮没有用真实 nft 验证）。
2. **state 恢复**：state 文件不在 nft 事务里。失败时只是“尝试恢复”，恢复失败会明确报错，不会提升为成功。
   断电或进程中途被杀时，state 可能和规则对不上，本项不处理。
3. **include**：script include 在 nft 应用成功之后单独执行，不在事务里，也不回滚。它失败时，restart 返回失败，
   但新规则已经生效。nft 类型的 include 会被渲染进 batch，属于第 1 条。

006 的动机是旧规则在重启过程中可能还引用已退役的匿名 vmap。本轮没有复现这个竞态，只能确认这三条状态匹配不再依赖匿名 map，
established/related accept 和可选 invalid drop 的匹配意图不变。005+006 不承诺重启时零丢包，也不保证硬件流表不会重建。

## 验证（实际执行）

工作目录为 `build_dir/up05-fw-restart/`（gitignored）。从 `rc01-nss-audit-20261112` 复制旧 tar，sha256 为 `b2a2baaa…f246`，
与 `firewall4-inputs.json` 一致，复制 005/006 后重新解压。临时目录放在专用的 `/var/tmp/up05-fw-restart/`。

- 补丁：001-add-bridge、001-sonic、002、003、004、005、006、100 依次用 `patch -p1 --fuzz=0` 应用，全部成功。
  有 offset 的只有：004 的 fw4.uc 两个 hunk（−33、−102），005 的 fw4.uc 两个 hunk（都是 −33）。fw4 脚本、ruleset.uc 和 main.uc 没有 offset。
  仓库补丁重新应用到另一份全新解压的树上，得到的 `root/` 与开发树完全相同。
- 新测试 `tests/rc01/test_firewall4_restart.py`，共 9 个方法。复用 `test_firewall4_bridge` 的源准备和 `render()`，
  `render()` 只加了 `files`/`env`/`whole` 三个参数：
  - 真实 ucode + fw4 mocklib 渲染：`FW4_RESTART=1` 时不生成两条 `delete flowtable`，其余输出与普通渲染逐字相同；
    `drop_invalid` 为 0 或 1 时，input/forward/output 三条链都没有 vmap，invalid drop 只在开启时出现，并排在 established,related 之后；
    直接运行 `main.uc` 的 includes（用 fixture 模块替换 mocklib 的 `system()`），全部成功返回 0，任一失败返回 1，并且其余 include 照样执行。
  - 真实 `root/sbin/fw4` 由固定 BusyBox 1.38.0 ash 执行（`PREFER_APPLETS`、`SH_STANDALONE` 都关闭）。测试会断言 MAIN/LOCK/STATE
    三行各替换一次，并且脚本里不再含 `/var/run`；PATH 中只有 utpl、nft、mktemp、cp、rm、flock 的桩。每个用例都先做 preflight，
    确认 `command -v` 解析到的是这些桩。覆盖：
    - 有 state 和无 state 时成功：只渲染一次、check 一次、apply 一次，check 和 apply 用同一份 batch，之后跑一次 includes；
    - render、check、apply 失败（有/无 state 各一次）：没有 stop，也没有额外的 nft 调用，不跑 includes，state 恢复原样，临时文件清理掉；
    - 恢复复制失败、删除失败时的明确文案；
    - include 失败：返回 1，新 state 保留，文案不含 “kept”；
    - stop 一次 batch 删除两张表、清除 state，然后从停止状态 restart 成功。
  - 包检查：005/006 的 git blob 与 43d9 一致；release 为 15，source/date/mirror hash 不变。
- 反证：在只打 001–006（不含 100）的树上有 10 个子用例失败，都是阶段文案、恢复失败报告和 include 返回值；
  在只打 001–004 的树上有 16 个失败，FW4_RESTART、006、stop 和单次 batch 都覆盖到了。在原始 tar 树上，测试会自己应用全部补丁，连同桥测试共 14 项通过。
- 回归（`uv run --offline --no-project`，UCODE/BUSYBOX/RC01_FIREWALL4_SOURCE 指向上述输入）：test_firewall4_restart、
  test_firewall4_bridge、test_ci_build、test_software_sources、test_platform、test_package_updates、test_firmware_pairing、
  test_flowtable、test_router、test_status、test_network_defaults，共运行 271 个方法，OK。其中 10 个跳过，都是既有环境条件
  （需要已准备的 kernel/wireless 树，或 pinned feed 对象），与本项无关。按要求没有跑全部回归。
- `busybox sh -n root/sbin/fw4` 通过；`git diff --check` 通过；JSON 可以解析。

## 未验证 / 待验

- 没有构建 firewall4 包或镜像，没有生成 `.config`/manifest。
- 没有用真实 nft 检查或应用渲染出的 batch：`delete table` 加完整 ruleset 能否被 nft 接受、事务失败时是否真的不提交、
  `ct state established,related` 的实际语义，都要在目标系统或隔离的 netns 中确认。
- 锁只是桩，没有测并发；没有测真实 include 脚本、procd 触发、断电或中途被杀时的 state 一致性。
- 没有实机重启压力测试，没有验证 PPE/硬件流表行为。不承诺零丢包，也不说 006 的竞态已经复现或已经消除。
