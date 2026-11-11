# 模块 08：集成验证与开发交付（准备部分）

基线 `7c63f50e890a26c6362eec72c69a4eba25a36a6d`（模块 01—06 已合入）；分支 `dev/module-08-integration`。
本记录只覆盖不依赖模块 07 的准备和 01—06 集成核对。模块 07 已在主线 `24ef8e5` 合入，本分支未包含；
全模块合并后的最终检查由主线程执行。未构建固件/APK，未连接设备、刷写或实机验证。

## 验收层次

| 层次 | 本次状态 |
| --- | --- |
| 源码开发验收（`tests/rc01` 单测、语法/静态检查） | 已执行，见下文 |
| 离线 DTB / 平台源码检查（`validate-rc01-platform.py`，预置 Linux/backports 源码） | 已执行，通过 |
| 包配方 / 安装宏 / ACL / 翻译核对 | 源码核对；未实际打包 APK |
| 实际 APK / factory / sysupgrade 镜像检查（`verify-rc01-images.py` 等） | 未执行：没有本次构建产物 |
| 实机（启动、无线、风扇、LED、网络、升级） | 未执行 |

## 1. 无线首启禁用的已知基线失败

`test_router.test_first_generated_rc01_ap_is_disabled_before_any_service_reload` 的失败不是实现缺陷。

- 生产链：preinit `02_sysinfo` 按 DT 首个 compatible（`xiaomi,be10000`）写 `/tmp/sysinfo/board_name`；
  `init.d/boot` 在 `board.json` 存在时执行 `wifi config`，即 `ucode /lib/wifi/mac80211.uc | uci -q batch`。
  `mac80211.uc` 对该板名把新生成的 `radioN` 和 `default_radioN` 都设为 `disabled='1'`；已有无线节（`radio_exists`）不改，
  所以只影响首次生成，升级保留配置时不变。实现与 CONTRACTS 一致，未改实现。
- 失败原因：测试用 `-L <夹具>/*.uc` 提供 `fs` 桩；ucode 按 `-L` 出现顺序搜索。此前使用的包装器在 `"$@"` 前预置
  `-L build/*.so`，真实 `fs.so` 遮蔽了桩，`readfile` 读到宿主不存在的 `/tmp/sysinfo/board_name`，于是不禁用。
  安装到 prefix 的 host ucode（默认搜索路径排在 `-L` 之后，与 `staging_dir/hostpkg/bin/ucode` 相同）下测试通过。
- 修正（仅测试）：先用无副作用探针确认 `fs` 解析到桩；否则以明确原因失败，不再读取宿主文件。
- 反证：把 `mac80211.uc` 的板名临时改错后该测试失败；使用旧包装器时失败信息为“native fs 先于桩”。

## 2. 模块 01—06 集成核对

核对 `rc01-platform`、`rc01-rgb`、`luci-app-rc01`、`luci-app-rc01-rgb`：defconfig、`validate-rc01-config.sh`
与 `DEVICE_PACKAGES` 的包选择一致；`INSTALL_BIN/CONF/DATA` 与文件角色一致；conffiles 为
`/etc/config/rc01_netled`、`/etc/config/rc01_rgb`；init START/STOP 与 reload trigger 合理（led 96 之后 97）；
rpcd `rc01.status/connectivity` 与 ACL、菜单、视图调用一致，RGB 只读状态文件 + `rc list` + uci `rc01_rgb`；
i18n 包名 `luci-i18n-rc01-zh-cn`、`luci-i18n-rc01-rgb-zh-cn` 与 po 目录一致；`CONFIG_LEDS_PWM`、`CONFIG_PWM_IPQ` 已启用；
板级 `01_leds` 不占用 network/RGB LED。未发现集成回归，未改实现。RGB 测试的 stderr 句柄
`ResourceWarning` 不影响重复运行，按要求未改。

## 3. 测试隔离问题：BusyBox `FEATURE_PREFER_APPLETS`

目标 BusyBox 配置（`package/utils/busybox/Config-defaults.in`）启用 `FEATURE_PREFER_APPLETS`。在此配置下，
BusyBox 的 `timeout` 直接执行自己的 `ip`/`ping` applet，不经过 PATH，因此 `test_netled` 的 `ip/ping` 桩被绕过。

已发生的情况（只依据本次自己的日志）：

1. 首轮：`. build_dir/rc01-verify-inputs/env.sh && uv run --offline --no-project python -B -m unittest discover -s tests/rc01 -v`
   （`BUSYBOX` 为启用 PREFER_APPLETS 的夹具）。BusyBox 变体共 27 个 `LedsBusyBox`/`ProbingBusyBox` 用例（22 失败、5 通过），
   另有 `Lifecycle` 的两个 busybox 子测试失败。
2. 复现：`uv run ... -m unittest tests.rc01.test_netled.ProbingBusyBox.test_ipv6_only`（1 个用例）。

两轮中守护进程可能通过 BusyBox applet 在宿主执行了 `ip route get` 和 `ping`。目标只来自测试夹具中的
文档地址段：RFC 5737（`203.0.113.0/24`、`198.51.100.0/24`、`192.0.2.0/24`）和 RFC 3849（`2001:db8::/32`）。
守护进程把命令输出写进随后删除的临时目录，stderr 丢弃，所以 `ip`/`ping` 的实际调用次数、是否发出 ICMP、
是否有回复都**未知**。事后检查没有残留的 fixture 进程或 `rc01-netled-test-*` 临时目录。
确认机制只用了无网络探针（`timeout 1 echo`）；没有再做任何网络探测，也没有检查宿主网络、路由或配置。

修正：

- `tests/rc01/test_netled.py`：任何 Fixture 运行前，先用无网络探针（PATH 中的 `echo` 桩配合 BusyBox `timeout`）检查；
  如果 `BUSYBOX` 绕过 PATH，所有 Fixture 用例在启动守护进程前就失败，并给出原因。已用目标配置的 BusyBox 验证：
  83 个用例中 73 个在 setUp 阶段失败，未运行守护进程。
- `scripts/rc01-verify-inputs.sh` 构建的夹具 BusyBox 关闭了 `FEATURE_PREFER_APPLETS`。**与目标的差异**：只差这一项，
  它影响 applet 执行外部命令时的查找顺序，不改变 ash 语法、语义或 applet 行为。
  已验证的是：BusyBox 1.38.0 加本树全部 busybox 补丁，使用目标默认 applet/ash 配置（除上述一项），在 x86_64 宿主 glibc 上运行；
  也就是 ash、`timeout`、`sleep`、`mv` 等 applet 的行为。**未验证**的是：目标 PREFER_APPLETS 下真实 `ip`/`ping` applet 的路径，
  以及交叉编译后在设备上的行为。不能写成“目标配置全部通过”。

## 4. 可复用公开输入

一条命令：`sh scripts/rc01-verify-inputs.sh [工作目录]`。默认工作目录是 `build_dir/rc01-verify-inputs`（已被 git 忽略），
完成后执行 `. <工作目录>/env.sh`。固定来源全部从本树读取，下载后校验哈希：

| 输入 | 来源 / 固定值 | 本次路径（本 worktree） |
| --- | --- | --- |
| Linux 6.18.60 | cdn.kernel.org，SHA-256 `3c2f44ef…f621e`（`kernel-6.18`）；generic files + backport/hack + `qualcommbe/patches-6.18`，0 rej | `build_dir/rc01-verify-inputs/src/linux-6.18.60` |
| backports 7.2 | openwrt/backports release，SHA-256 `6ec76a4c…9454`；mac80211 Build/Patch 顺序，ath11k 不含 nss | `build_dir/rc01-verify-inputs/src/backports-7.2` |
| BusyBox 1.38.0 | busybox.net，SHA-256 `34f9ea6f…3bb2`；本树补丁 + Config-defaults（见第 3 节差异） | `build_dir/rc01-verify-inputs/src/busybox-1.38.0/busybox` |
| LuCI | immortalwrt/luci `df794c7f69361641195a4b336634d1b1c918099c`（`config/rc01/feeds.conf`） | `build_dir/rc01-verify-inputs/src/luci` |
| host ucode | ucode-lang/ucode `dfc64d41fd720163ac21d9feb9cb224d1a80fd75`，包内 host 模块选项，安装到 prefix | `build_dir/rc01-verify-inputs/ucode/prefix/bin/ucode` |

worktree 根目录：`/home/illya/.t3/worktrees/immortalwrt/dev-module-08-integrati-901d9669cf9f7545`。
`/tmp` 是共享 tmpfs，空间不足以容纳内核源码，所以放在磁盘上的 `build_dir` 中（约 2.2 GiB）。
主线可在合并后的源码树上重新执行该脚本，或通过参数指向已有的工作目录来复用下载结果。

## 5. 实际执行与结果

- `UCODE=<prefix ucode> uv run --offline --no-project python -B -m unittest discover -s tests/rc01`
  （未设置其他输入）：481 项通过，跳过 64 项。
- 设置全部输入后（`. env.sh`，夹具 BusyBox 已关闭 PREFER_APPLETS）运行同一命令，加 `-v`：**521 项通过，跳过 3 项**。
  跳过原因：dnsmasq 预置源码、packages feed 未准备、固定 feed git 对象。这三项需要 `prepare-rc01.sh` 生成 feeds。
- `uv run --offline --no-project python -B scripts/validate-rc01-platform.py "$RC01_KERNEL_SOURCE" "$RC01_WIRELESS_SOURCE"`：
  通过（Linux 6.18.60、NAND、HW1 MLO 关闭、DS ABI、CMA 320、RGB PWM、预留区域）。
  `control_port_ingress_config` 为 `not-built; source-only validation`；`physical_acceptance` 为 `not-performed`。
- `shellcheck -s sh scripts/rc01-verify-inputs.sh`：无输出。

## 6. 主线合入 07 后需重跑

在最终主线源码树执行：

```sh
sh scripts/rc01-verify-inputs.sh            # 或传入已有工作目录以复用下载
. build_dir/rc01-verify-inputs/env.sh
uv run --offline --no-project python -B -m unittest discover -s tests/rc01 -v
uv run --offline --no-project python -B scripts/validate-rc01-platform.py "$RC01_KERNEL_SOURCE" "$RC01_WIRELESS_SOURCE"
```

剩余缺口及下一步：

- 3 个 feeds 相关跳过项：在专用构建工作区执行 `RC01_UPDATE_MODE=locked sh scripts/prepare-rc01.sh`，再运行单测。
- 实际 APK / 镜像：需要在完整构建（`make download && make`）后对 `bin/targets/qualcommbe/ipq95xx` 执行
  `verify-rc01-images.py`、`verify-rc01-wireless-artifacts.py`、`validate-rc01-nand.sh`、`validate-rc01-public-artifacts.py`，
  并用实际内核/无线构建目录做构建后无跳过回归（见 `docs/rc01-build.md`）。
- 引导器物料检查需要开发者另行提供的候选文件；本任务未读取任何候选文件。
- 实机验证全部未做。
