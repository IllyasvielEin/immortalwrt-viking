# 上游 UP02 / FW-BRIDGE：firewall4 桥卸载范围与 nft-bridge 依赖

基线 `ccb3270cdc1040abcbf960f92e5490c90ded7b6e`，分支 `dev/simplify-02-process-title`，单个本地提交。
未构建目标包或固件，未运行 nft，未连接设备。等待 Astra 独立验收，本分支不自行合入。与 UP01 无依赖，未读取其 worktree。

## 来源选取

- 上游：VIKINGYFY/immortalwrt 提交 `43d9a24aab2cebab2d9bdb47a194ea28872c9cb6`，固定读取点 `dd8de87169f1c1d091a81eb237341b2de09bd5bf`。
  （UP05 更正：本项原记为 `67bb668a`；该提交实际是 769，004 由其子提交 43d9a24a 引入，见 `upstream-05-firewall-restart.md`。补丁 hunk 与行为未变。）
  只取 `package/network/config/firewall4/patches/004-scope-bridge-flowtable-offload.patch`（blob `b81a38fb62cb…`，
  sha256 `a037fd29…b032`，与 `rc01-nss-audit-20261112/download-manifest.json` 一致）和 Makefile 中的 `+kmod-nft-bridge`。
  原作者头和 Signed-off-by 保留。
- 不取：005/006（留给 UP05）、firewall4 源升到 `3722ead`、该提交的其他文件。
- `scripts/rc01-platform-sources.json`：`selected_commits` 和 `followup_selection` 加入 partial 记录（原误记 67bb668a，UP05 已改为 43d9a24a）；
  `local_adjustments` 记录 helper 适配。`reviewed_head` 仍为历史值 `7b3b0f0f…`，`full_merge` 仍为 false。
  该提交位于 reviewed_head 之后，scope 文字写明读取点是 dd8de871。工作流的 `viking_selected_commits` 同步追加。
  已知的 HW1 build-ID 文字偏差（版本号精确比较，build ID 用子串）本项没有处理，也没有改无线。

## 改动

- `patches/004-scope-bridge-flowtable-offload.patch`：上游原文只改了一行，另加一段 RC01 说明。
  - 上游调用为 `resolve_lower_devices(devstatus, entry, this.kernel >= 0x06110000)`，第三个参数 `use_prefix` 属于
    3722ead 的新 helper（含 WDS 前缀解析）。旧 e6f600c 的 helper 只有 `(devstatus, devname)`，ucode 会忽略多余参数，
    但保留这个参数会让人误以为两边行为相同，所以改成两参数调用。桥端口仍按旧方式解析：vlan/bridge 递归找 `lower_*`，
    其他设备取自身。本项没有引入 WDS 前缀或新的 helper。
  - 其余与上游一致：`resolve_bridge_offload()` 返回 `{devices, bridges}`；桥名取自端口的 `master` 链接；
    allow 列表非空时只保留列表里的桥，exclude 优先于 allow；规则 `meta ibrname {桥} meta l4proto {tcp,udp} flow offload @fb`，
    放在 `chain flow_offload`，forward hook 优先级 2147483647（排在其他 forward 过滤之后）；
    新增 defaults 选项 `flow_offloading_bridge`（bool，默认 1）、`flow_offloading_bridge_devices`、`flow_offloading_bridge_exclude`（list）。
- `Makefile`：`PKG_SOURCE_VERSION`/`PKG_SOURCE_DATE`/`PKG_MIRROR_HASH` 不变，`PKG_RELEASE` 13→14，DEPENDS 加 `+kmod-nft-bridge`。
  `meta ibrname` 需要 `nft_meta_bridge`。`include/netfilter.mk` 把 `CONFIG_NFT_BRIDGE_META` 归在 NFT_BRIDGE，
  只有 `kmod-nft-bridge` 打包这个模块；generic config 里 `NF_TABLES_BRIDGE=y` 但 `NFT_BRIDGE_META` 关闭，所以基础桥表支持不够用。
- `scripts/rc01-source-metadata.py`：`local_patchset` 的哈希范围加入 `package/network/config/firewall4`。
- `.github/workflows/build-rc01.yml`：构建后回归时找到 `build_dir/target-*/firewall4-*` 并传入 `RC01_FIREWALL4_SOURCE`，
  避免新测试在 CI 中被跳过（CI 规定跳过即失败）。

## 行为边界

- 默认值为 1，allow/exclude 为空时，和以前一样发现全部桥的端口；只有明确配置才会缩小范围。
  RC01 的默认 LAN/WAN/旁路配置没有改，也没有加入任何默认 allow/exclude。不能说默认已经隔离。
- 单个桥也可以用 exclude 排除，或者把 `flow_offloading_bridge` 设为 0 整体关闭；没有可用的桥或端口时，不生成 bridge 表的内容。
- 共用下层口的情况：例如 `br-vlan` 的端口 `lan1.20` 下层是 `lan1`。只允许 `br-lan` 时 `lan1` 仍会挂进 flowtable，
  但 `ibrname` 不匹配 `br-vlan`，所以 br-vlan 的流量不会进入缓存。
- 学习规则在最后优先级，所以首批包会先经过普通 forward 过滤。之后已缓存的流仍会绕过逐包检查。
  本项不提供逐包 ACL，也不对已缓存流逐包过滤；依赖不对称或逐包 ACL 的桥需要用户自己排除。
- 现有的 DS、桥撤流、`50-fw4-bridge-reload`（仍查 `bridge fw4 fb`）和 `check_bridge_flowtable` 都没有改。
- 升级后如果没有重启就直接 reload：`flush table bridge fw4` 只清规则不删链，旧版留下的 `chain forward` 会变成空链，
  其 policy 为 accept。上游也是这样，本项不处理（stop/重建表属于 005）。

## 验证（实际执行）

工作目录 `build_dir/up02-bridge-scope/`（gitignored）：复制 004 和 `firewall4-local-e6f600c….tar.gz`（sha256 `b2a2baaa…f246`，
与 `firewall4-inputs.json` 一致）后重新解压。临时文件放在专用的 `/var/tmp/up02-bridge-scope.*`。

- 补丁：本地 001-add-bridge、001-sonic、002、003 依次用 `patch -p1 --fuzz=0` 打上，没有 offset。
  004（上游原文和本地版本都试过）同样 fuzz 0；ruleset.uc 无 offset，fw4.uc 的两个 hunk 分别为 offset −33 和 −102。
  在 pristine 树上打本地补丁和在 CI 形式的已准备树上打，两次得到的 `root/` 完全相同。
- 新测试 `tests/rc01/test_firewall4_bridge.py`（4 个方法），使用固定 ucode
  `rc01-verify-inputs/ucode/prefix/bin/ucode`，以及 fw4 自带的 mocklib（fs/ubus/uci 都是 mock）。
  通过 `include(main.uc)` 以 ACTION=print 渲染真实的 `templates/ruleset.uc`。另外只加了很少的桩：
  `/sys/class/net` 列表、brport、vlan 的 `lower_*`、network.device 的 devtype，以及 `fs.readlink` 返回的 master 链接。
  覆盖的情况：
  - 默认：全部三个桥，端口为 `lan1 lan2 lan4 sfp`，有 `flags offload`，链和优先级为 2147483647，没有旧的 `chain forward`；
  - allow 单桥、allow 共用下层口的 VLAN 桥、exclude、allow 和 exclude 同时写同一个桥（exclude 优先）、allow 不存在的桥；
  - `flow_offloading_bridge=0`、`flow_offloading=0`；
  - 单桥时默认、排除、禁用，以及没有任何桥端口的情况。
  包检查：`+kmod-nft-bridge` 存在、源版本仍为 e6f600c、NFT_BRIDGE_META 归入 kmod-nft-bridge，以及 004 里是两参数调用。
- 反证：去掉 004 后 8 个子用例报错、1 个失败；包检查也会失败。
- 回归（uv，`--offline --no-project`）：test_firewall4_bridge、test_ci_build、test_flowtable、test_router、test_status、
  test_network_defaults、test_software_sources、test_firmware_pairing、test_platform、test_package_updates，共运行 256 个方法（含跳过，不能称 256 个实际执行成功）。
  有 10 个跳过，都是已有的环境条件（需要已准备的 kernel/wireless 树，或 pinned feed 对象），与本项无关。
  按要求没有跑全部 597 项。
- 语法：用固定 BusyBox 1.38.0 的 `sh -n` 检查 `root/sbin/fw4` 和 `50-fw4-bridge-reload`，均通过；`git diff --check` 通过。

## 未验证 / 待验

- 没有构建 firewall4 或 kmod 包，也没有生成 `.config`/manifest。`kmod-nft-bridge` 是否真的被选入，要看生成配置和镜像 manifest。
- 初审已确认原候选的重复包归属：两包定义虽原已存在，本次增加的强依赖使它们共同选择，因此撤回“不是本地新引入差异”的笼统说法。主线程按 R1 修为专用包单一归属，详见下节。尚未构建或安装真实 APK，不声称复现安装器报错；同批实际包清单仍待目标构建确认。
- 没有用 nft 检查规则（`meta ibrname`、flowtable 挂载、priority 取值），没有运行 ubus/ip/netns，没有测试流量、PPE 硬件卸载或实机。


## Astra R1 的主线程最小修复

初审报告：外层 `docs/reviews/upstream-02-bridge-scope-astra.md`。修复基线 `247aad5956edab82e5c0db76cec0898008664ac1`，等待独立复验，未自行标记通过。

- 删除 `include/netfilter.mk` 中把 `nf_conntrack_bridge` 再加入 `NFT_BRIDGE` 的 `nf_add`；专用 `nf-conntrack-bridge` 继续提供该模块、KCONFIG 与 autoload。
- `KernelPackage/nft-bridge` 显式依赖 `+kmod-nf-conntrack-bridge`，保留 meta/reject 模块，不引入 replaces、强制覆盖或新包框架。
- 本模块 `local_adjustments` 记录本地归属适配；`local_patchset()` 纳入修改的两份 netfilter.mk，以来源摘要覆盖实际输入。
- 新增一个 GNU make 展开回归，读取真实生产定义与 `nf_add`，使用三个 `m` 配置确认专用包唯一拥有文件、nft 包依赖它且保留 `nft_meta_bridge`。该测试在原 `247aad5` 两份定义上得到一个预期失败、零执行错误，在修复定义上通过。
- 根线程运行 `test_firewall4_bridge.py` 的5项及 `test_ci_build.py` 的7项，合计12项通过、0失败/错误/跳过；输入为固定 host ucode 与原始旧源复制后应用当前包补丁。第一次 source 路径误指外层 pristine 目录，缺 `root/` 报错，改为其真实子目录后上述检查通过；没有修改源码输入来规避失败。
- `local_patchset()` 实际结果含两份归属定义；JSON与 `git diff --check` 通过。相关 CI 测试中的 ENOSPC 是预设夹具，不是本轮实际磁盘错误。

本次未执行目标 Kbuild/包打包/APK 安装、nft 或实机，构建与硬件边界保持。桥范围补丁本身、firewall4源/date/mirrorhash/release14、helper行为均未再次改动。
