# RC01 自用构建

本定制基于 VIKINGYFY/immortalwrt `owrt` 的
`e5b0e82156513e81b6b712e4498ab15fd3a28571`，仅针对已经核实的
小米万兆 **RC01 / BE10000 / IPQ9570**。保留上游作者、补丁来源及许可。
无线固件仍包含原项目使用的二进制文件，公开构建源码不等于所有组件均开源。

## 适用的设备状态

- Du v2 社区 U-Boot，机型 `8050301`，需要兼容 `config@al02-c4`。
- 已合并为 **120 MiB rootfs** 的 MIBIB；不适用于原厂双 60 MiB 布局。
- 512 KiB APPSBLENV 分区中的 **64 KiB、非冗余环境格式**，擦除块128 KiB，
  环境工具的坏块搜索范围为两个擦除块。分区编号按名称解析，不固定为 mtd17。
- UBI0 中 kernel/rootfs 的卷编号为0/1，rootfs_data 为2。
- 已核实的 EMC2301 风扇控制器，I²C 地址 `0x2f`。

原厂备份与当前实机都通过64 KiB环境CRC校验；256 KiB校验均不通过。
硬件与维护资料保留在工作区的 `router/RC01.md`，私密备份不进入此仓库。
这些条件不能推广到 BE10000 Pro 或其他社区引导器。

## 构建形式

沿用 AX6000 仓库的“源代码 fork + 单设备 defconfig + 手动 Actions + 缓存 +
配置和产物校验 + 构建元数据”形式。工作流是
`.github/workflows/build-rc01.yml`，只接受 `workflow_dispatch`，没有定时构建、
自动合并上游、发布 Release、上传路由器或刷机步骤。将工作流提交到自己的
fork 默认分支后，可以在 Actions 中手动启动。

`config/rc01/feeds.conf` 固定四个 feed 的提交；Nikki 固定v1.26.2，源码构建
Mihomo 1.19.32。准备脚本仅安装所选软件及依赖，移除会造成Kconfig循环的alpha核心
定义，并记录一处使用 `SOURCE_DATE_EPOCH` 的Mihomo构建时间修改。

普通软件已对照 AX6000 默认分支 `25.12` 与最近成功构建
`6eeefe96793e0ab0181d6b9d97ed6b9b63e84126`：Nikki/Mihomo、WireGuard、
Tailscale Community、中文LuCI、CA证书、完整tcpdump、htop与zram选入默认镜像。
WG没有预置隧道；Tailscale首启禁用服务，未写入账号、密钥或路由通告。
MTK的无线配置、HNAT、WARP和eQoS软件不随通用软件迁移。

本地构建使用项目正常的 OpenWrt 主机依赖，之后执行：

```sh
sh scripts/prepare-rc01.sh
python3 -B -m unittest discover -s tests/rc01 -v
make -j4 download
make -j4
python3 -B scripts/verify-rc01-images.py bin/targets/qualcommbe/ipq95xx
```

需要与AX6000的旧daed做对照时，可在Actions手动选择 `include_daed`，
或在本地把第一条命令换成 `RC01_WITH_DAED=true sh scripts/prepare-rc01.sh`。
该选项合并 `config/rc01/daed.config`，启用主机LLVM、完整BTF、cgroup/BPF事件、
stream parser、veth及相关TC/XDP模块，并选入daed及其LuCI和地理数据库。
本地需要可用的clang/LLVM；Actions已安装。它仍保留Nikki，首启daed的
`enabled=0`，不自动接管流量。测试产物以 `daed-test` 标注。

准备脚本会重建生成的 feeds 包链接、`feeds.conf`、`.config` 和 `files` 中的RC01
覆盖文件；不要在这些生成位置保存个人配置。版本源文件分别位于
`defconfig/qualcommbe-rc01.config` 和 `config/rc01/`。跟进上游、升级feeds或Nikki时，
更新锁定提交并重新运行检查，不能只给旧镜像混装新内核模块。

Actions 使用Ubuntu24.04、4个编译任务和独立的下载/ccache缓存。产物包含
factory.ubi、sysupgrade.bin、完整配置、diffconfig、feeds锁、manifest、校验和、
提交及构建时间记录、Nikki配方差异和本次构建的APK包及公钥；不上传签名私钥。
本固件没有启用未经核对的公共APK源，后续安装应使用同一构建的包归档或经过验证的源。
daed测试构建额外校验实际内核配置和 `vmlinux` 的BTF段；只勾选LuCI软件包不算通过。
两种构建都遵守同一个32 MiB data卷LEB预算，超限时失败，不自动削减预留空间。

## 启动和存储检查

C4、C6是两个完整FIT配置节点，引用同一份kernel/DTB，不额外复制载荷。
环境工具为RC01启用严格CRC模式，异常时拒绝用工具默认环境执行 `fw_setenv`。
初始化和升级路径都不会为适配镜像而自动改写环境或MIBIB。

CI检查FIT引用与hash、ARM64 Image头及解压内存边界、RC01设备树与温控映射、
UBI EC/VID/卷表CRC、kernel/rootfs卷编号，以及factory和sysupgrade载荷一致性。
容量检查保留整片256 MiB NAND的坏块预算、UBI开销和至少32 MiB的data卷LEB空间。
后者不等于UBIFS最终可用容量。系统内升级也重新检查布局和FIT配置。

Mihomo位于SquashFS，overlay供配置、规则和数据库使用。在线覆盖ROM内核心会在
overlay另存一份大文件；默认通过重编固件升级核心，需要独立更新时另行规划外置存储。

## 首次启动的网络和代理

- LAN为 `192.168.50.3/24`，网关/DNS为GS7 `192.168.50.1`。
- `lan1..lan4` 和 `sfp` 在LAN桥内；10G电口 `wan` 保留且不自动启动，WAN6同样关闭。
- DHCPv4/v6、RA、NDP关闭，不对整个LAN强加SNAT；ICMP redirect关闭。
- Router Tool保留，自动路由预设关闭，平台PPE默认设置仍初始化一次。后续手动修改
  offload不会被每次重载覆盖。LuCI保存路径同样遵守手动管理选项。
- 三路无线在首次生成配置时即关闭，没有SSID密码、订阅或代理账号预置。
- Nikki和Mihomo默认选入构建，但未配置代理接管。NAS、UPnP不选入；daed仅为可选测试构建。

不能保留QWRT的eth接口配置跨驱动升级。单客户端代理验证应同时检查DNS、IPv6和
同网段回程；关闭RC01自己的RA不会阻止GS7的RA抵达同一二层网络的客户端。
保留原生PPE能力，用新建连接做offload开关对照。空闲计数为0或不对称回程下未命中，
不能单独判驱动故障。ath12k DS仅在所支持AP/授权peer路径启用，MLO回退不等于无线失败。

## 高通与RC01的软件取舍

| 用途 | 选取与边界 |
| --- | --- |
| 有线与无线硬件 | 保留原生 `kmod-qcom-ppe`、Aquantia PHY、QCA8084、SFP与ath12k/QCN9274、板级BDF和ART校准；不混装旧NSS/ECM、SSDK或MTK驱动 |
| 无线检查 | 完整 `iw-full`、`hostapd-utils`、MAC80211/ath12k debugfs与ath12k温度支持；不预先开启三路射频、MLO、漫游或无线回程 |
| 链路与转发检查 | `ethtool-full`、`tc-full`、`ip-bridge`、`conntrack`、完整tcpdump和 `iperf3`；iperf3仅按需运行，路由器作为端点的测速不等于转发吞吐 |
| CPU与内存 | 保留cpufreq，加LuCI管理，使用内核现有schedutil与合法OPP；不复制QWRT的ondemand、固定频率、超频或强制IRQ/RPS参数；保留zram |
| 设备枚举 | PCI/USB工具便于核对三块PCIe无线卡及USB设备；不等于驱动或固件已经工作 |
| 风扇 | `rc01-platform`、EMC2301驱动及内核thermal；不叠加第二个PWM控制服务，不运行I²C扫描或sensors-detect |
| USB存储 | USB3、USB storage/UAS、ext4、block-mount与e2fsprogs；供后续外置规则/核心数据使用，不预置自动挂载、格式化、extroot或文件共享 |
| QoS | 原生驱动已有TBF端口整形支持；当前 `qca-ppe-qos` 的依赖限定IPQ60xx/IPQ807x，且其LuCI筛选DSA端口，不直接选入RC01的switchdev端口；SQM/qosify另行验证 |
| IRQ与监控 | 不默认启用irqbalance、nlbwmon或collectd；先以驱动RSS/IRQ与PPE计数做基线，硬件转发流量不能靠软件抓包或统计覆盖率推断 |

10G电口的AQR113C固件由现有设备树从原分区nvmem读取；不附加其他板子的PHY固件，
也不在启动时刷写PHY。SFP+ EEPROM/DDM能否通过 `ethtool -m` 读取取决于模块与驱动。
RGB、NFC节点与外观功能不因安装通用插件就获得完整适配，留待逐一确认控制器和接线。
PPE转发加速也不代表Mihomo、dae、WireGuard或Tailscale的加密由PPE处理。

## 首启状态与代理预检

自用镜像包含 `luci-app-rc01`，入口为 **状态 → RC01 硬件状态**。每5秒读取一次
温区、EMC风扇、网口、分区与UBI卷、overlay可用空间、PPE活跃流、无线禁用配置和
核心进程RSS；刷新失败后清除旧读数。中文语言总开关及相关翻译包由构建校验强制确认。
缺少可选daed时界面显示“本次构建未选入”，不会把它当成必需硬件故障。

安装本固件后，可使用以下只读命令；输出直接写到终端，不生成报告文件：

```sh
rc01-check status
rc01-check check hardware
rc01-check check nikki
rc01-check check daed
```

`status` 输出JSON快照；`check` 输出所选预检项，发现错误时退出码为1，参数错误为2。
警告不会被当成已验证的兼容性结论。代理预检检查核心可执行性、双核心或启用状态冲突、
手动网络管理及offload基线；daed还要求运行内核的BTF文件。
不会启动/停止代理、加载或卸载驱动、改防火墙、改风扇、发送测速流量或刷写分区。

RPC仅开放 `rc01.status` 的读取权限，没有执行命令、UCI写入或启停服务的接口。
模块仅取所需UCI启用标志，不输出订阅或代理配置；不读取环境区内容、代理配置正文、
进程命令行或日志。环境映射匹配不能证明CRC有效，
CRC检查仍由已有升级前检查执行。无线禁用配置不能替代实际射频状态检查；PPE流数为0、
端口未插线或速率不可读也不会被误报为硬件损坏。硬件故障、内核BPF加载和流量验收仍需
在后续独立实机测试中确认。

## daed核查与试验顺序

2026-11-02核查：AX6000锁定feed和本仓库锁定feed均提供daed **1.27.0**。
追踪其git子模块得到 `wing=6bb6a310ef3d98bea691fd5955cc703306835546`，
`dae-core=7e67e31e241a6d2cc5f2b5ff228b4fb5faf6d24a`，对应 **dae 1.1.0rc1**。
它不是当前独立dae的版本。官方daed与dae-wing仓库已归档，daed最后发布v2.1.1；
dae仓库仍在维护。本次没有擅自追踪main或把package版本号改成最新版。

RC01的Linux6.18.55满足该核心的最低5.17门槛，但原最小配置缺少完整BTF及BPF事件等
前提；不能只据内核版本认定可用。社区存在daed1.27.0内存持续增长的报告，涉及ARM64
及x86用户，不能视为本机已复现。另有新版daed1.28/dae main在6.18 IPQ6010上的
网络命名空间故障报告，版本与硬件均不同，不能直接套在此构建。

试验按以下顺序，涉及实机操作时单独执行：

1. 首次完整构建先通过编译内核/BTF、镜像和容量检查；不要将“配置通过”称为“构建通过”。
2. 保持Nikki/daed都未接管，核对基础网络与恢复入口。每次只启动一个透明代理，切换前停掉
   旧服务并核对TC/BPF、nft规则、DNS监听和策略路由；安装在同一镜像不等于同时运行。
3. 选一个测试客户端、保留其他客户端原网关。以软件/硬件flow offload关闭为代理基线，
   验证旁路由LAN接口与本机出站接口绑定，不能将被禁用的 `network.wan` 当作实际上联。
   检查TCP、UDP、DNS、IPv4/IPv6、直连与代理出口、MTU及同网段回程。
4. 核对 `/sys/kernel/btf/vmlinux`、TC/BPF附着、veth/命名空间、实际报文与泄漏情况。
   挂载成功或BPF已附着不等于接管成功；有线与无线需分别测。
5. 保持新连接测试方法一致，再对照软件offload、PPE与无线DS。未证明代理流量正确排除前
   不承诺全加速共存，也不为测代理而卸载PPE驱动或破坏LAN硬件网桥。
6. 通过前述项目后做24–48小时内存、连接反复建立、DNS缓存及冷启动检查。
   RSS持续增长、接管漏流量、DNS失联、内核异常或管理不可达时停止daed试验并恢复Nikki基线；
   zram不能当作内存泄漏的修复。

若长期使用eBPF代理优先级较高，再评估维护中的独立dae及 `luci-app-dae`，或对daed新版本
做单独移植。两者的UI、核心、配置和数据库不能仅改一个版本号替换；本阶段不将其冒充为
已验收的RC01增强软件。在检查的官方仓库、issues与Viking资料中，未找到这套RC01/6.18.55/
PPE/daed组合的直接实机验收记录，未覆盖私有群聊与未索引帖子。

核查来源：

- [AX6000实际配置](https://github.com/IllyasvielEin/immortalwrt-mt798x/blob/6eeefe96793e0ab0181d6b9d97ed6b9b63e84126/defconfig/mt7986-ax6000.config)
- [daed官方仓库及归档状态](https://github.com/daeuniverse/daed)、[dae-wing](https://github.com/daeuniverse/dae-wing)、[dae核心与内核要求](https://github.com/daeuniverse/dae/blob/main/docs/en/README.md)
- [daed1.27内存增长报告#998](https://github.com/daeuniverse/dae/issues/998)
- [新版dae在6.18/IPQ6010的命名空间报告#1136](https://github.com/daeuniverse/dae/issues/1136)
- [单接口旁路接管报告#1032](https://github.com/daeuniverse/dae/issues/1032)
- [本基线的qca-ppe-qos适用目标](https://github.com/VIKINGYFY/immortalwrt/blob/e5b0e82156513e81b6b712e4498ab15fd3a28571/package/network/config/qca-ppe-qos/Makefile)

## 风扇与实机验收

使用现有主线EMC驱动，按设备身份寻找 `emc2305` hwmon，不固定I²C编号或hwmon序号。
内核thermal使用SoC原有nss-top传感器，每秒采样：基础state3（PWM77/255）、
50°C state5、60°C state7、70°C state10，回落迟滞3°C。原有critical与CPU温控保留。

独立守护服务开机先请求全速3秒，再提供非零PWM下限。温度过高或连续三次
转速/传感器/PWM异常会锁定全速；停止服务也请求全速。温控曲线由内核负责，
守护服务退出后不会把风扇主动关停。I²C或硬件故障时软件无法保证风扇实际转动。

**这套曲线尚未在新驱动上标定，首版镜像尚未完成实机验收。**
完成可恢复路径准备后，先验证风扇起转、转速、PWM响应、传感器及故障处理，
再做负载；风扇应转不转或温度持续失控时停止负载。随后验证每个端口、
代理与加速共存、无线/AP/DS、冷启动和长期运行。编译通过不替代这些检查。
试启动和刷机另行执行，不是CI构建的后续自动步骤。

新系统的升级检查会拒绝不带双配置节点的旧QWRT sysupgrade镜像。回退应准备维护主档
中已验证的U-Boot网线恢复入口及0726 factory材料，再在QWRT内升级到1007；
不要用强制参数绕过不明检查错误。新系统到QWRT的完整回退流程仍需后续演练。
