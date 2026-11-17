# RC01 构建指南

本页说明构建方法与验收契约；具体镜像的验收状态以对应构建产物和验证结果为准。

适用于小米万兆 RC01 / BE10000 / IPQ9570。平台基于 VIKINGYFY/immortalwrt `owrt`，
合并来源与本地覆盖以 `scripts/rc01-platform-sources.json` 为准；保留上游作者与许可。
无线固件包含二进制组件。内核为 6.18.60，mac80211 使用 backports 7.2 上游合并及本地生命周期保护，
平台、驱动、无线固件和引导器不随软件 feeds 自动更新。

## 唯一分区布局

区间左闭右开，索引从 0 开始：

| 分区 | 索引 | 范围（MiB） | 大小（MiB） |
| --- | --- | --- | --- |
| 0:LICENSE | 23 | 23.25..23.5 | 0.25 |
| bdata | 24 | 23.5..23.75 | 0.25 |
| rootfs | 25 | 23.75..254 | 230.25 |

LICENSE 及之前条目的名称、位置、属性和次序保留，LICENSE 属性为 `0xffff`。
rootfs 起点 `0x017c0000`，长度 `0x0e640000`，共 1842 个 128 KiB 擦除块。
该物理容量不等于 UBI 可用容量；254..256 MiB 不作为越界写入或坏块跳过的缓冲。
UBI 中 kernel/rootfs/rootfs_data 卷 ID 为 0/1/2。

配套候选身份：

- U-Boot：`openwrt-ipq9574_xiaomi_be10000-u-boot.mbn`，520684 字节，SHA-256
  `86092e052a2dcc734e3a84572f544bd4f00061f8746d75d659c2d063511316cc`。
- MIBIB：`mibib-front-bdata-r1.bin`，262144 字节，SHA-256
  `697764db15a752a61e9aed8820f7b78593a65521f3491b8d4aa5062201e36b31`。
  仓库包含该表及完整静态解析器；生成器、模板来源及许可随准备资料另行交付。

U-Boot 的附加修改尚未完整对应到源码，因此仓库和公开 artifact 不分发该二进制。
CI 仅在物料验证步骤通过仓库 Actions 私密输入读取候选，内存校验后丢弃；
编译、feeds 和单测不接收该输入。配置方式见 `config/rc01/boot/README.md`。

候选引导器的静态依据包括 C4 FIT、四个电口及 64 KiB 非冗余 ENV。
APPSBLENV 分区为 512 KiB，擦除块 128 KiB，环境工具搜索范围为两个擦除块；
按分区名称解析，不能固定 MTD 编号。有效 ENV 的启动命令须单独核对。

## 构建与软件选择

工作流 `.github/workflows/build-rc01.yml` 使用手动 `workflow_dispatch`。
目标是只构建上述布局；Actions 默认 `include_dae=true`，包含 dae 与 daede UI。
可显式选择 `include_dae=false` 构建不含 dae 的同布局变体。本地准备脚本仍须显式设置
`RC01_WITH_DAE=true` 才包含 dae。
工作流不执行设备刷写。

`software_updates` 默认 `latest`：解析 packages、luci、routing、nikki、daede 的最新 HEAD，
立即固定本次 commit；Tailscale、Mihomo、Nikki 解析最新稳定 Release，拒绝 draft/prerelease。
源码归档校验 SHA-256，配方不兼容或来源获取失败时停止，不静默回退。
`locked` 使用仓库审核基线。每次构建记录 `.rc01-sources/lock.json`，
产物附带软件来源锁、feeds 锁、配置及 package manifest。

来源准备职责集中在 `rc01-software-sources.py`：`plan` 固定 feeds、平台输入、变体和构建时间，
`prepare` 从固定对象推导并校验配方后统一写入，`verify` 检查已准备来源。
锁定模式直接使用同一推导和写入路径，不再由 shell 先应用补丁再重建校验。
原独立的 `patch-rc01-golang.sh`、`patch-rc01-daede.sh`、`patch-rc01-packages.py`
已移除；固定补丁、完整前后像及权限检查由 Python 适配器统一执行。
`prepare-rc01.sh` 负责更新和安装 feeds、合并配置并执行配置检查；
CI 负责选择 bootstrap、下载重放锁、缓存和发布校验。
`rc01-source-metadata.py` 在构建后再次核验来源锁并记录实际配方及产物元数据。

本地安装项目要求的 OpenWrt 主机依赖后执行：

```sh
python3 -B scripts/rc01-layout.py --check-generated
python3 -B scripts/verify-rc01-boot-materials.py --uboot <本地候选文件>
sh scripts/prepare-rc01.sh
python3 -B -m unittest discover -s tests/rc01 -v
make -j4 download
make -j4
python3 -B scripts/verify-rc01-images.py bin/targets/qualcommbe/ipq95xx
```

构建前回归先运行 `sh scripts/rc01-verify-inputs.sh --busybox-only /tmp/rc01-test-host`，
再执行 `. /tmp/rc01-test-host/env.sh`。该模式只构建测试用 host BusyBox，不准备内核或无线源码。
CI 将同一 `BUSYBOX` 绝对路径传给构建前及构建后的回归；系统 BusyBox 的 ash 内建 sleep 可能绕过测试桩。
构建前允许缺少尚未构建的源码和工具而跳过对应用例，最终回归仍须零失败、零错误、零跳过。

风扇内存回归按实际完成的发布次数采样，保留增长门槛和执行超时；不得用调用次数替代实际发布数量，也不得绕过最终验收门槛。

不做完整构建时，`sh scripts/rc01-verify-inputs.sh` 会按本树固定来源准备打过补丁的 Linux/backports 源码、
BusyBox、锁定的 LuCI 和 host ucode。工作目录仅保存生成物，每次只复用已校验的下载归档，重新准备源码与工具；
然后执行 `. build_dir/rc01-verify-inputs/env.sh` 再运行单测和
`scripts/validate-rc01-platform.py`。这只是源码级/离线 DTB 检查，不产生 APK 或镜像；夹具 BusyBox 与目标配置的差异
由准备脚本的 host 配置定义；不能替代目标镜像验证。

构建后的零跳过回归须同时传入本次 LuCI feed 的 `LUCI_VALIDATION_JS` 和 `LUCI_FORM_JS`，与 CI 的调用一致
（RGB 页面校验未设置时回退到 `feeds/luci`）；仅提供内核、无线源码和 ucode 不足以运行新增的页面保存校验。

可选准备方式（各自用于一次独立构建）：

```sh
RC01_UPDATE_MODE=locked sh scripts/prepare-rc01.sh
RC01_WITH_DAE=true sh scripts/prepare-rc01.sh
RC01_REPLAY_LOCK=/tmp/rc01-source-lock/lock.json sh scripts/prepare-rc01.sh
. .rc01-sources/environment.sh
```

只更新一个应用时，在同一准备入口设置 `RC01_APP`，例如：

```sh
RC01_UPDATE_MODE=latest RC01_APP=tailscale=1.104.6 sh scripts/prepare-rc01.sh
# 或 RC01_APP=mihomo=1.19.34；版本可带 v 前缀
```

这是相对于仓库 `config/rc01/patches/packages/manifest.json` 的定向选择：五个 feeds
保持 `config/rc01/feeds.conf` 的固定提交，未选应用沿用 manifest，Nikki、Go 和可选 dae
保持审核基线，不滚动到 latest。它不继承上次临时 latest 构建的选择；需要保留某次构建的
全部来源时使用该次来源锁 replay。低层规划入口为
`python3 -B scripts/rc01-software-sources.py plan --mode latest --app tailscale=1.104.6`；
它只建立计划，不安装 feeds、不准备来源。完整准备应使用上面的环境变量入口，避免 shell
重新规划时丢失选择。

`--app`/`RC01_APP` 只能选择 Tailscale 或 Mihomo 的一个稳定版本，与 `locked` 或显式
replay 同用会报错，包括看似相同的选择也不能覆盖 replay。规划保存可变选择，来源解析
把 tag 固定为 commit、实际归档 SHA256、go.mod 摘要及配方前后像；metadata 标记
`policy=single-application` 并记录 `application_selection`，不会把混合 manifest 表述为
整组软件已更新。replay 按记录对象重建并核对，不再查询浮动 release。

版本选择不会自动升级 compiler。实际归档中的 go.mod 最低版本必须由本次固定 feed 的
Go 满足，且实际 compiler 与来源记录一致；例如 Tailscale 1.104.7 要求 Go 1.27.4，当前
基线 Go 1.27.3 会明确拒绝。若要更换 compiler/feed，需另行审核对应来源和适配器。
配方仅允许已列字段变化，以及 define、续行、tab recipe 之外的独立列首注释；不折叠空白、
不改 recipe 字符串。实际补丁仍检查前像并应用：即使注释本身不改变语义，若改到补丁的
context 行也需要明确调整补丁。新计划采集当前平台，不要求匹配仓内历史锁；本次构建内
漂移和显式历史 replay 的平台不匹配仍拒绝。新计划清理前次生成的软件文件时，不要求
旧平台与当前平台相同，但仍核对 feed 身份、原始前像、完整输出及权限；用户修改不会被覆盖。

Actions 的 `replay_run_id` 用于重放已保存来源锁；重放仍核验平台输入、软件变体、
feed commit、归档和配方摘要。生成目录 `.rc01-sources/` 不提交版本控制。
仓库的 `config/rc01/software-baselines/locks/with-dae.json` 是历史平台输入、固定 feeds 和
审核配方生成的完整来源记录（包含 dae，尚无对应固件构建验收）。本地重放：

```sh
RC01_WITH_DAE=true RC01_REPLAY_LOCK=config/rc01/software-baselines/locks/with-dae.json sh scripts/prepare-rc01.sh
```

平台输入有意变更后，旧锁的显式 replay 仍须拒绝；新计划不受它阻断。如需另存新的历史记录，先以 `RC01_UPDATE_MODE=locked RC01_WITH_DAE=true`
运行准备入口，核验新锁并审核差异，再更新该基线。不要只修改平台摘要使旧锁通过。
不含 dae 的本地锁使用 `RC01_UPDATE_MODE=locked RC01_WITH_DAE=false` 独立生成。
锁中的 `build_commit`、`resolved_at` 记录最初规划来源时的 HEAD 和 UTC 时间，
`source_date_epoch` 默认取该 HEAD 的提交时间；重放保留这些值，产物元数据另记录实际构建 HEAD 的提交和树对象（`source_commit`、`source_tree`）；
这只是 HEAD 身份记录，不检查工作树是否有未提交改动，也不约束重放时锁外路径与原构建一致。
仓库锁在被绑定的改动提交之后生成并另行提交，故其 `build_commit` 是收录提交的父提交；它不要求重放 HEAD 相同，
但仍逐项严格比较 `platform_inputs`。该清单不覆盖 `luci-app-rc01` 状态 UI，
不应将通过来源锁校验理解为全部工作树一致。
准备的最终校验或附属文件写入失败时，原计划保留；锁定模式可直接重试 Python `prepare`。
若进程恰在锁与阶段文件之间中断，阶段摘要仍会拒绝；重新执行准备入口的 `plan`
会先校验并还原上一次完整记录的生成文件，再建立新计划。
准备脚本重建 feeds 链接、`feeds.conf`、`.config` 和 RC01 的 `files` 覆盖文件；
源配置放在 `defconfig/qualcommbe-rc01.config` 与 `config/rc01/`。

基础软件包含 Nikki/Mihomo、WireGuard、Tailscale Community、中文 LuCI、CA 证书、tcpdump、
htop 和 zram。dae 变体增加 dae/luci-app-daede、实际内核 BTF、cgroup/BPF 及 TC/XDP 依赖，
保留 Nikki；两者首启均不接管流量。核心与 LuCI 随固件重编，避免在线覆盖 ROM 核心占用 overlay。

Actions 的 `go_bootstrap=external` 校验官方 Go 1.24.13 bootstrap 后从锁定源码构建工具链；
`source` 使用源码链。`cold_cache=true` 跳过缓存恢复与保存。缓存、并行及串行重试统计
随构建记录；软件版本与完整来源应从当次来源锁读取。

## CI 验收范围

CI 静态检查候选 MBN 与 MIBIB 的大小、摘要、格式和布局一致性，
核对最终 DTB、环境映射、FIT 引用与 hash、ARM64 解压内存范围，以及 UBI CRC、卷 ID、
factory/sysupgrade 载荷一致性、坏块预算和至少 32 MiB data 卷 LEB 空间。
最终 SquashFS 中的布局契约、升级预检和状态采集脚本须与本次源码一致；
升级前检查全部 26 个分区的名称、位置和几何，以及身份区 ECC 和坏块状况。
dae 变体还检查最终内核配置与实际 `vmlinux` BTF。两种软件变体采用相同布局和容量规则。

通过的构建交付 factory、sysupgrade、校验和、配置、来源锁、manifest 及同批 APK/公钥，
不包含签名私钥。未完成验收的 artifact 不能作为合格刷写材料。
镜像包命名为 `rc01-identity-preserved-dhcp-complete-{nikki|dae-test}-{run_number}`，
包含同批布局契约、MIBIB 和静态验证记录；静态物料 artifact 同样不包含 U-Boot 二进制。
`.github/workflows/verify-rc01.yml` 可用 `source_run_id` 对通过原生检查的当前布局产物重新验收，
并核对提交、布局与物料身份。

**验证边界：CI 通过仅证明对应提交和产物通过构建及静态检查，不模拟硬件启动。**
当前布局的实际前级接受、ENV 兼容、ECC/坏块写入边界、RAM 恢复、迁移、启动、升级和回厂
仍需独立验收；本指南不预报新 CI 或现场成功。风扇、无线、网络和代理共存也须在新镜像上实测。

## 迁移和恢复条件

先准备设备外独立保存的原表、身份区和必要引导状态，以及经验证的 RAM 恢复环境。
恢复环境必须无 NAND/UBI 自动附着、挂载或后台写入，且表损坏时仍有独立恢复入口。
备份若为 ECC 纠正的主数据，不得作为 raw+OOB 镜像写入；工具的 ECC 和坏块语义必须一致。

新 bdata 覆盖原 rootfs 开头，写入后不能依赖原系统启动。整区擦写新 rootfs 会覆盖原 bdata，
必须先确认新副本与设备外材料完整。MIBIB 容器不是整分区 raw 镜像，
其余块、旧副本和擦除范围须在现场协议中明确；双副本不能保证断电安全。
切表后重新进入恢复环境并刷新 SMEM/MTD 映射，读回 LICENSE 和新 bdata 后才进入 factory。
升级必须通过布局、容量和 FIT 检查，不能强制绕过；正常 sysupgrade 不负责迁移分区表。

回厂须协调恢复原表、刷新映射、恢复原地址身份与必要引导状态，再恢复原厂系统。
必须预先验证换表或换引导后仍能重入恢复环境；官方系统包不能重建本机身份。
具体阶段和停止条件见配套准备包的 PROCEDURE.md、RECOVERY.md 与 CHECKLIST.md。

## 首启与运行

LAN 作为 DHCP 客户端，管理地址从上游租约查找；LAN DHCP/RA/NDP 服务关闭。
`lan1..lan4` 与 `sfp` 在 LAN 桥内，`wan`/WAN6 不自动启动；无线、代理和 Tailscale 首启禁用。
HTTPS 证书在设备本地生成，不预置账号、密码、订阅或私钥。跨驱动迁移须重新核对接口配置。

RC01 使用 EMC2301 风扇控制器及 `emc2305` 驱动，按设备寻找 hwmon，不固定序号。
温控保留非零转速下限和故障满速保护；先验收起转、转速与温度响应，再进行负载测试。
代理先用单客户端验证 DNS、IPv4/IPv6、回程及实际接管，再对照 PPE/无线加速；
Nikki 与 dae 每次只启用一个透明代理。后续安装内核模块使用同一构建的包与来源。


## 离线对象级验证

`tests/rc01/wireless_replay.py <固定backports归档> <新生成目录>` 按当前维护补丁重放；
已集成923的树不要再传入第三个外部分支补丁参数。对象验证入口为
`tests/rc01/wireless_arm64_objects.sh <prepared Linux> <隔离工具链> <可写backports副本> <ARM64对象目录>`。
该脚本按正式mac80211 Build/Prepare删除vendored headers，不能在共享只读输入上运行。
早期DFS对象脚本遗漏这一步所需的MHI桥接已移除；它不是生产构建额外要求。

完整离线回归还使用 RC01_DNSMASQ_SOURCE、RC01_FIREWALL4_SOURCE、RC01_PACKAGES_FEED，
及本树feeds/packages、feeds/nikki中的固定Git对象；常规kernel/wireless、BUSYBOX、UCODE、LuCI变量仍按上文。
这些离线输入必须对应固定来源，不以缺输入skip代替最终回归。对象编译和源码回归不生成可验收镜像；
完整目标配置、模块链接、镜像及设备仍须另行验收。
