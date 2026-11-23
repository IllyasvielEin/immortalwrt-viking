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

## 构建与软件来源

工作流 `.github/workflows/build-rc01.yml` 使用手动 `workflow_dispatch`，输入为 `go_bootstrap` 与 `cold_cache`。
目标是只构建上述布局，工作流不执行设备刷写。

软件来源由仓库固定，构建时不解析更新的 HEAD 或 Release：

- `config/rc01/feeds.conf` 的每一行都是 `src-git <名称> <URL>^<40 位提交>`，固定 packages、luci、routing、nikki、daeuniverse（后者仅供 honk）。
  修改某个 feed 的版本就是修改这一行的提交 ID，并经审核提交。
- 对固定 feed 的修改只来自 `config/rc01/patches/` 下的标准 `.patch`：
  `golang/` 修改 packages 的 `lang/golang/*.mk`（编译器与主机 Go 使用本地工具链），
  `packages/` 将 Tailscale、Mihomo 升到审核过的版本（Mihomo 补丁同时让构建时间取 `SOURCE_DATE_EPOCH`），
  `honk/` 修改 daeuniverse feed 的 `honk/Makefile`（见下文 honk）。
  `packages/manifest.json` 记录每个包的 feed、路径、补丁、版本、源码提交与哈希以及补丁后配方的摘要，
  供产物元数据核对。
- `scripts/prepare-rc01.sh` 在 `./scripts/feeds update -a` 之后，确认每个 feed 位于固定提交，
  再对每个补丁依次执行 `git -C feeds/<feed> apply --check <补丁>` 与 `git apply`；
  已应用的补丁（反向检查通过）跳过，任何补丁不能干净应用都立即失败。新增受控补丁时
  在该脚本的 `rc01_patches` 列表中加一行。
- 之后脚本安装所选软件包根、合并 `defconfig/qualcommbe-rc01.config`、选择 Go bootstrap、
  执行 `make defconfig`、`scripts/validate-rc01-config.sh` 与 rc01-platform 配方预检。
  `SOURCE_DATE_EPOCH` 沿用 OpenWrt 的 `scripts/get_source_date_epoch.sh`（仓库 HEAD 提交时间）。
- 软件包中不含 dae/daed/daede 的任何形式；`validate-rc01-config.sh` 会拒绝它们及 `vmlinux-btf`、`vmlinux-btf-builtin`。

产物的 `rc01-build-metadata.txt` 记录仓库提交与树、各 feed 的提交、受控补丁文件的 SHA-256、
`SOURCE_DATE_EPOCH`、Go bootstrap 等；`rc01-source-metadata.py` 在构建后核验平台来源、
补丁后的配方与 feed 提交并生成 `rc01-source-metadata.json`。CI 负责选择 bootstrap、缓存和发布前检查。

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

更新软件来源：先在新的 feed 提交上重新生成对应补丁（上游已含等价修改时删除补丁），
修改 `feeds.conf` 的提交 ID、补丁、`manifest.json` 和 `rc01_patches` 列表，再运行
`sh scripts/prepare-rc01.sh` 确认全部补丁通过 `git apply --check`；Go 编译器版本由固定 feed 的配方决定，
实际编译器须与之一致（`scripts/validate-rc01-go-version.py`），不会随应用版本自动升级。
准备脚本重建 feeds 链接、`feeds.conf`、`.config` 和 RC01 的 `files` 覆盖文件；
源配置放在 `defconfig/qualcommbe-rc01.config` 与 `config/rc01/`。

基础软件包含 Nikki/Mihomo、WireGuard、Tailscale Community、中文 LuCI、CA 证书、tcpdump、
htop 和 zram；首启均不接管流量。核心与 LuCI 随固件重编，避免在线覆盖 ROM 核心占用 overlay。

Actions 的 `go_bootstrap=external` 校验官方 Go 1.24.13 bootstrap 后从固定 feed 的源码构建工具链；
`source` 使用源码链。`cold_cache=true` 跳过缓存恢复与保存。缓存、并行及串行重试统计
随构建记录；软件版本与来源以产物的 `rc01-build-metadata.txt` 和 feed 提交为准。

## honk（实验，运行时未验证）

固件默认编入 daeuniverse 的 eBPF 透明代理 honk 及其 LuCI 页面（`luci-app-honk`、`luci-lib-daeuniverse` 与中文包
`luci-i18n-daeuniverse-zh-cn`），没有构建开关；首启默认停用，与 Nikki 二选一。RC01 上的运行时行为尚未验证，必须在新镜像上实测。

- 来源：官方 feed `https://github.com/daeuniverse/openwrt-packages.git`，在 `feeds.conf` 固定为
  `e104a4930c5fc4f625f3355257626495685424f5`。honk 源码是 `daeuniverse/honk` 标签 `v0.1.0-beta.6`
  （提交 `64848881b7304e250e7a25422f4f1d86a5975eca`）的 codeload tarball，SHA-256
  `cfddedeafa349d0bdfaebd792d6f15c9f31de46a051e5a0692749a74f69d8e6d`，解包目录 `honk-0.1.0-beta.6/`。
- 补丁：`config/rc01/patches/honk/100-rc01-honk-source.patch` 只修改该 feed 的 `honk/Makefile`：
  用标准 `PKG_SOURCE_URL`/`PKG_HASH` 下载源码，取代依赖 `./src` 检出的自定义 `Build/Prepare`；
  版本、标签与哈希写死，去掉 `HONK_VERSION`/`HONK_GIT_TAG` 外部覆盖；去掉对 `vmlinux-btf` 的依赖，
  BTF 来自内核自带的 `/sys/kernel/btf/vmlinux`（`honk.init` 同时接受该路径）；`PKG_RELEASE` 2→3，
  使 apk 版本不同于官方 feed 的 `0.1.0_beta6-r2`。该补丁与其他补丁一样，在
  `prepare-rc01.sh` 中检查并应用，上游 feed 变化会立即暴露。feed 中的 `dae`、`luci-app-dae`、
  `vmlinux-btf`、`vmlinux-btf-builtin` 不会被安装，配置校验也拒绝它们。
- 构建：`prepare-rc01.sh` 无条件安装 honk 相关包（原 `RC01_WITH_HONK` 开关已移除，设置它会报错），
  `defconfig/qualcommbe-rc01.config` 直接包含其选项（BTF、cgroup BPF、`kmod-sched-bpf`、`kmod-veth`、
  nft 队列与 nfnetlink 队列、geo 数据等；不开启 XDP 套接字、BPF stream parser、kprobes 与主机 BPF 工具链），
  `validate-rc01-config.sh` 要求这些符号都存在。构建后
  `scripts/validate-rc01-honk-kernel.sh` 核对编译出的内核配置（BPF/CGROUP_BPF/NET_CLS_ACT/NET_XGRESS/
  DEBUG_INFO_BTF/NET_NS/NF_TABLES_INET 为 `y`，VETH/NF_TABLES/NFT_CT/NFT_QUEUE/NETFILTER_NETLINK_QUEUE 等为 `y` 或 `m`）
  和 `vmlinux` 的 `.BTF` 段。
- 与 Nikki 二选一：`89-rc01-bypass` 在存在 `/etc/config/honk` 时把 `honk.config.enabled` 设为 `0`；
  honk 包自带的 `90-honk-autostart` 在其后运行，只在该选项为空时才写入，所以全新镜像保持不自启。
  使用 `sysupgrade` 保留设置时，新镜像的首启脚本会再次执行，honk 同样回到停用，需手动重新启用。
  启用 honk 前先停用 Nikki，反之亦然；Nikki 预检会把运行或启用中的 honk 报告为冲突。
- CI 的 Rust 依赖：honk 的 `build.sh` 用 cargo 构建 `honk-core --features ebpf,native-ui`，需要 Rust 稳定版
  `1.99.1`（含 `aarch64-unknown-linux-musl` 目标）、eBPF 用 `nightly-2026-10-20`（`rust-src`、`llvm-tools`）
  和经 SHA-256 校验的 `bpf-linker 0.11.0`，以及 `cmake`、`libclang-dev`、`clang`、`llvm`、`pkg-config`、`zstd`。
  这些版本集中在工作流顶层 `env`（`RC01_HONK_*`），安装步骤在下载源码之后、编译之前，并对照下载的 honk 源码中的
  `rust-toolchain.toml` 与 `.github/ci/pins.env`，不一致即失败。Cargo 注册表按 honk 标签缓存。
  `build.sh` 还会在编译时下载其固定的 doona 界面压缩包并校验 SHA-256，所以编译需要联网。
- 待实机验证：`honk` 在 RC01 内核上的 eBPF 加载、TC/veth/`daens` 命名空间、NFQUEUE、cgroup 遥测，与 PPE 硬件加速及
  无线共存、LuCI 页面、启动/升级后的停用状态、与 Nikki 切换，以及 `honk-core` 的依赖库是否只含 libc、libgcc_s 与 libstdc++。
  CI 通过只证明构建与静态检查。

## CI 验收范围

CI 静态检查候选 MBN 与 MIBIB 的大小、摘要、格式和布局一致性，
核对最终 DTB、环境映射、FIT 引用与 hash、ARM64 解压内存范围，以及 UBI CRC、卷 ID、
factory/sysupgrade 载荷一致性、坏块预算和至少 32 MiB data 卷 LEB 空间。
最终 SquashFS 中的布局契约、升级预检和状态采集脚本须与本次源码一致；
升级前检查全部 26 个分区的名称、位置和几何，以及身份区 ECC 和坏块状况。

通过的构建交付 factory、sysupgrade、校验和、配置、feeds 与补丁记录、package manifest 及同批 APK/公钥，
不包含签名私钥。未完成验收的 artifact 不能作为合格刷写材料。
镜像包命名为 `rc01-identity-preserved-dhcp-complete-{run_number}`，
包含同批布局契约、MIBIB 和静态验证记录；静态物料 artifact 同样不包含 U-Boot 二进制。
`.github/workflows/verify-rc01.yml` 可用 `source_run_id` 对通过原生检查的当前布局产物重新验收，
并核对提交、布局与物料身份。

从同一次 Actions run 和 attempt 下载已验收镜像及 `rc01-symbols-{run_id}-{run_attempt}`，
比对两包中的 `rc01-images.sha256`，并在镜像目录执行 `sha256sum -c rc01-images.sha256`。
解析现场栈中的 `函数+偏移 [模块]` 时，设置 `CROSS_COMPILE` 为匹配的 AArch64 工具链前缀，
在符号包目录运行内核源码中的 `scripts/faddr2line`，例如
`"$kernel_dir/scripts/faddr2line" qcom-ppe.ko qcom_ppe_ds_start+0x4`。内核内建函数使用 `vmlinux`。
基础配置（含 honk 与 Nikki）固定 `CONFIG_KERNEL_DEBUG_INFO=y` 且不启用 `KERNEL_DEBUG_INFO_REDUCED`
（qualcommbe 默认会启用后者），`validate-rc01-config.sh` 要求前者并拒绝后者，符号包因此始终带完整 DWARF。符号包步骤不依赖 honk 专属选项（如 BTF）。

**临时 SLUB 诊断（kmalloc-2k）**：为定位 kmalloc-2k freelist 被覆写的 double free / UAF，基础配置构建
`CONFIG_KERNEL_SLUB_DEBUG=y`（不启用 `KERNEL_SLUB_DEBUG_ON`，避免全局开销），正式版
`ipq9570-be10000.dts` 的 bootargs 追加 `slub_debug=FZPU,kmalloc-2k`，仅对该 cache 开启。
`validate-rc01-config.sh`、`validate-rc01-platform.py`（最终内核 `.config`）与 `verify-rc01-images.py`（编译后 DTB）
同步校验。抓到调用栈后 `git revert` 该提交即可同时撤销配置、bootargs、校验与测试；启动后可在
`/sys/kernel/slab/kmalloc-2k/` 与 dmesg 中确认标志已生效。

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
首次生成的无线 radio 与默认 AP 均禁用（`wifi-scripts` 的 `mac80211.uc`，仅 `xiaomi,be10000`）；自动带宽上限固定为
2.4 GHz 20、低 5 GHz 160、高 5 GHz（默认信道 ≥100）80、6 GHz 160 MHz，不写 `auto_htmode`，运行时不升到 320 MHz。
HTTPS 证书在设备本地生成，不预置账号、密码、订阅或私钥。跨驱动迁移须重新核对接口配置。

RC01 使用 EMC2301 风扇控制器及 `emc2305` 驱动，按设备寻找 hwmon，不固定序号。
温控保留非零转速下限和故障满速保护；先验收起转、转速与温度响应，再进行负载测试。
代理先用单客户端验证 DNS、IPv4/IPv6、回程及实际接管，再对照 PPE/无线加速；
Nikki 是默认受支持的透明代理；honk 为实验性、首启停用，且与 Nikki 二选一。其他代理（dae、daed、honk）
与 Nikki 同时运行或启用会被预检判为冲突（已安装但停止并禁用的 honk 不算冲突）。后续安装内核模块使用同一构建的包与来源。


## 离线对象级验证

`tests/rc01/wireless_replay.py <固定backports归档> <新生成目录>` 按当前维护补丁重放；
已集成923的树不要再传入第三个外部分支补丁参数。对象验证入口为
`tests/rc01/wireless_arm64_objects.sh <prepared Linux> <隔离工具链> <可写backports副本> <ARM64对象目录>`。
该脚本按正式mac80211 Build/Prepare删除vendored headers，不能在共享只读输入上运行。
早期DFS对象脚本遗漏这一步所需的MHI桥接已移除；它不是生产构建额外要求。

完整离线回归还使用 RC01_DNSMASQ_SOURCE、RC01_FIREWALL4_SOURCE，
及本树 feeds/luci、feeds/nikki 中的固定 Git 对象；常规 kernel/wireless、BUSYBOX、UCODE、LuCI 变量仍按上文。
这些离线输入必须对应固定来源，不以缺输入skip代替最终回归。对象编译和源码回归不生成可验收镜像；
完整目标配置、模块链接、镜像及设备仍须另行验收。
