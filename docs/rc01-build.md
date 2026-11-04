# RC01 自用构建

本定制基于 VIKINGYFY/immortalwrt `owrt` 的
`e5b0e82156513e81b6b712e4498ab15fd3a28571`，仅针对已经核实的
小米万兆 **RC01 / BE10000 / IPQ9570**。保留上游作者、补丁来源及许可。
无线固件仍包含原项目使用的二进制文件，公开构建源码不等于所有组件均开源。

本轮从 `rc01/custom` 的 `64802ce26c` 继续，真实 ancestry 基线仍为上述 `e5b0e821`。
Viking 审查到 `ec73307670005a1f00cbeb039dc90709fa2a871e`，按以下完整提交选择整合，
没有 full merge，也没有把此前审查点 `a8d4a44c` 当作已合并基线。
此前已摘取的四项继续保留；构建元数据同时记录原始来源与本次选择列表，保留作者、签署和许可。

| 上游提交 | 跟进内容 | 验收边界 |
| --- | --- | --- |
| `4e7173571c` | IPQ9574 使用三个 PPE DS 节点，IPQ5332 仍为两个 | ring3 / queue48–55 的源码和 IRQ 资源已核对，RC01 实际注册与流量待验 |
| `47ce1cd5df` | AP 重建后补齐 inet/bridge flowtable hooks | 十种离线事件重放通过，AP 重建与代理共存仍待实测 |
| `d58d050780` | 按 WSI 拓扑稳定 MLO radio 排序 | 升级后先核对无线索引、频段和物理卡，不能直接套旧配置 |
| `5dbfbfe462` | 按固件实际 PHY 数收紧 WMI endpoint | 保留已有单卡一个 radio 的限制，不增加射频数量或能力 |
| `f120d87c92` | ES/calibration 标识、ipq-wifi r7 | qca-wireless 固定 `5363395811fb3c72da0e62304a26d995c0f9c675`，不是新机 ART 数据 |
| `1e95b974f6` | C4 默认 FIT、ART MAC、三卡独立注册 | C6 完整别名同载荷，保留五参数64KiB环境映射与120MiB布局 |
| `6149690bf7` | Aquantia GPIO61低有效100/100ms复位、USXGMII时钟 | 时钟先于XPCS释放，ethphyfw `0x28/0x60002` 不变；RC01冷启动待验 |
| `490d9e9cb2` | 平台CMA128MiB、BE10000 `cma=320M`、动态AFC、mac80211 r35 | 移除3×64MiB WLAN及18MiB MLO carveout，AFC上限8192字节；连续分配收益待实测 |
| `490a5b3887` | HW1非MLO独立组 | 禁止HW1单芯片/跨卡MLO，保留HW2及AHB合法MLO路径 |
| `7e8c4cef99` | 清理临时无线诊断 | NAND 0412身份/ECC诊断继续保留 |
| `ec73307670` | 受限高5GHz合法默认 | 高频卡用auto/EHT80，保持首启禁用 |
| `90f54dddcf` | 独立加入909 AP TPC重算 | 仅6GHz TPC路径，不宣传RC01 2.4/5GHz功率收益 |

暂缓 `6061a61a` DFS 去重：只比较已生效规则，仍需验证“当前 A、待应用 B、再次请求 A”
的异步队列情形，911不加入。Askey 专用 slab 开关与 qualcommax 的 AX/SVID 修改未摘取。
CMA320MiB是本次候选分配策略，不把第三路DS与此前CMA紧张的因果关系当作已证实。

内核固定 **6.18.60**，官方tar SHA256为
`3c2f44ef6acc021a7ad444d9a2e6ec1ef488ff09368793d0004a6080f69d621e`；不切7.3，
mac80211仍用backports7.2，BPF headers大版本不变。6.18.56的
`a47406c769f72c070386985488f1c4d057df86b2` 已包含在其中；prepared树检查
`inet_sock_destruct()` 和 `tcp_v4_reqsk_destructor()` 两处 `kfree_rcu`，对应CVE-2026-98432。
本次移除五个已进入stable的重复回移（438、782-02、787、948、950），并刷新
flowtable706-03、ARP151、LoongArch304、bridge675-02、fq_codel660上下文，保留stable新修复。
PCS0313仅将interface缓存提交推迟到模式配置成功后，不称为已复现故障的修复。

## 适用的设备状态

- Du v2 社区 U-Boot，机型 `8050301`，需要兼容 `config@al02-c4`。
- 已合并为 **120 MiB rootfs** 的 MIBIB；不适用于原厂双 60 MiB 布局。
- 512 KiB APPSBLENV 分区中的 **64 KiB、非冗余环境格式**，擦除块128 KiB，
  环境工具的坏块搜索范围为两个擦除块。分区编号按名称解析，不固定为 mtd17。
- UBI0 中 kernel/rootfs 的卷编号为0/1，rootfs_data 为2。
- 已核实的 EMC2301 风扇控制器，I²C 地址 `0x2f`。

原厂备份与 QWRT 阶段实机均通过64 KiB环境CRC校验；256 KiB校验均不通过。
当前原生系统仅复核工具映射，本轮未读取环境内容或重新检查CRC。
硬件与维护资料保留在工作区的 `router/RC01.md`，私密备份不进入此仓库。
这些条件不能推广到 BE10000 Pro 或其他社区引导器。

## 构建形式

沿用 AX6000 仓库的“源代码 fork + 单设备 defconfig + 手动 Actions + 缓存 +
配置和产物校验 + 构建元数据”形式。工作流是
`.github/workflows/build-rc01.yml`，只接受 `workflow_dispatch`，没有定时构建、
自动合并上游、发布 Release、上传路由器或刷机步骤。将工作流提交到自己的
fork 默认分支后，可以在 Actions 中手动启动。

`config/rc01/feeds.conf` 固定五个feed；Nikki锁定
`78e1a751d55ed0762811f054ee699049473da7b8`，包版本2026.10.10-r1。
Mihomo源码固定1.19.33、Tailscale固定1.104.5，Go编译器1.27.3、bootstrap1.24.13。
`config/rc01/patches/packages/manifest.json` 记录核心提交、源包hash和本地配方补丁，
prepare按固定feed与配方hash幂等应用。仅安装所选软件及依赖，移除会造成Kconfig循环的
alpha核心定义，并使用 `SOURCE_DATE_EPOCH` 固定Mihomo构建时间。

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

在Actions选择 `include_dae`，或本地使用
`RC01_WITH_DAE=true sh scripts/prepare-rc01.sh`，构建 **dae + luci-app-daede** 实验版。
此选项替代此前的旧daed实验配置，合并 `config/rc01/dae.config`，启用主机LLVM、
完整内核BTF、cgroup/BPF事件、stream parser、veth及TC/XDP模块。
它仍保留Nikki，两者首启均不接管流量；产物以 `dae-test` 标注。
本地需要clang/LLVM，当前锁定的Go工具链已为1.27。源码更新脚本不执行上游一键安装器，
不启用公共二进制feed；核心和LuCI随固件重编升级，地理数据库可以单独更新。

准备脚本会重建生成的 feeds 包链接、`feeds.conf`、`.config` 和 `files` 中的RC01
覆盖文件；不要在这些生成位置保存个人配置。版本源文件分别位于
`defconfig/qualcommbe-rc01.config` 和 `config/rc01/`。跟进上游、升级feeds或Nikki时，
更新锁定提交并重新运行检查，不能只给旧镜像混装新内核模块。

后续正常构建统一使用长期分支 `rc01/custom`；GitHub缓存只允许当前分支和默认分支
共享，历史 sibling 分支的缓存不会因快进或相同key自动迁移。

Actions 的 `go_bootstrap` 默认 `external`：下载并核对官方 Go 1.24.13 linux-amd64
归档的固定SHA256，在独立空目录解压，再从锁定源码编译最终 Go 1.27.3。
这跳过原有多级bootstrap链，不替换固件中的代理程序或功能。
下载、校验或版本检查失败时在正式编译前停止；显式选择 `source` 才回到原有源码链，
不在已有构建产物上自动切换。packages feed中的两处实际Go调用均强制
`GOTOOLCHAIN=local`，保留模块校验。完整 `tmp/go-build` 缓存暂不启用，
因为共用目录含CGO结果，其外部C依赖闭包尚未完成审查。

`cold_cache=true` 跳过下载和ccache恢复/保存，使用全新的本地缓存；不设置
`GOCACHE=off`。先 lookup-only 确定期望key，再精确恢复并核对实际返回key；恢复异常会清除
对应不完整目录并禁止当轮保存，正常首次miss则可从全新目录建立缓存。
ccache采用 `compiler_check=content` 和新命名空间 `rc01-ccache-v2-content`，
保留3G上限、不放宽校验；旧mtime条目不算新模式的热缓存。
缓存key含run attempt，兼容前缀限定runner架构、目标和工具链配方指纹。

每轮独立记录并行编译和串行重试的退出码与耗时，在额外验收编译前汇总ccache
stats log、恢复matched-key、runner资源，以及构建实际解析的host/cross/BPF工具路径、
内容身份和版本。统计单独上传，普通编译或验收失败保留原失败状态；缓存上传失败
只记录警告。新增优化仍须由后续正常构建测量冷/热缓存净收益，不预承诺节省比例。

官方依据：[GitHub缓存范围](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache)、
[ccache参数](https://ccache.dev/manual/latest.html#_configuration_options)、
[Go源码安装](https://go.dev/doc/install/source)、
[Go缓存与CGO限制](https://go.dev/cmd/go/#hdr-Build_and_test_caching)。

Actions 使用Ubuntu24.04、4个编译任务和独立的下载/ccache缓存。产物包含
factory.ubi、sysupgrade.bin、完整配置、diffconfig、feeds锁、manifest、校验和、
提交及构建时间记录、Nikki配方差异和本次构建的APK包及公钥；不上传签名私钥。
本固件没有启用未经核对的公共APK源，后续安装应使用同一构建的包归档或经过验证的源。
dae测试构建额外校验实际内核配置和 `vmlinux` 的BTF段；只勾选LuCI软件包不算通过。
两种构建都遵守同一个32 MiB data卷LEB预算，超限时失败，不自动削减预留空间。

编译完成后，先保存 `rc01-unverified-images`（7天，未验收）和对应APK包，
再执行原生模块/内核检查及镜像验收。下载和ccache在正常失败后也保存，
避免只有整个任务成功才产生缓存。未验收镜像不能当成可刷写产物。

仅镜像验收脚本需要修正时，可手动运行 `.github/workflows/verify-rc01.yml`，
填写原构建的 `source_run_id`，下载原产物并重新检查，不执行编译。
该流程核对原始提交、校验和，并要求原任务的原生模块/实际内核检查已经通过；
通过后记录原构建与本次验收代码的两个提交。没有保存候选镜像的旧任务无法恢复。

## 启动和存储检查

升级预检从 `rc01-platform 1-r3` 修正 BusyBox awk 对正则中斜杠的解析：
字符类中的斜杠也需转义。旧43fc、DHCP complete和082981镜像里的脚本会在选择
sysupgrade kernel条目前报 `bad regex`，导致LuCI拒绝镜像。上传SHA正确也不能跳过
设备端检查；不勾选强制升级。当前运行系统使用自己的预检脚本，上传含修复的新镜像
不会自动修正旧脚本。源码回归直接执行BusyBox ash/awk/tar，覆盖正常归档和缺失、
嵌套、空设备名、重复kernel条目；设备脚本的修正与实际刷写需分别获授权。

C4为默认，C6为完整别名；恰好两个FIT配置节点，引用同一份kernel/DTB，不额外复制载荷。
环境工具为RC01启用严格CRC模式，异常时拒绝用工具默认环境执行 `fw_setenv`。
初始化和升级路径都不会为适配镜像而自动改写环境或MIBIB。

CI检查FIT引用与hash、ARM64 Image头及解压footprint与最终DTB所有固定reserved-memory及
FDT reserve-map逐一避让（包括 `0x49b00000/6MiB` tzapp，不只旧bootloader边界）、
CMA320MiB、四个旧carveout和host-ddr引用消失、ART0 WAN/ART6 LAN及WiFi+2/+3/+4、
board-id1/4/2与caldata三路径 `0x1000/0x33000/0x65000`、长度 `0x2d000`、末端 `0x92000`，
以及PHY GPIO61/ethphyfw和PCIe3 GPIO23、NAND8/512、RC01设备树与温控映射、
风扇推挽PWM参数，以及三个DS ring的九个命名IRQ、GIC编号与触发类型；PPE/EDMA及
父节点必须启用。DTB字段正确不等于DS已经实际注册或转发。另检查UBI EC/VID/卷表CRC、
kernel/rootfs卷编号，以及factory和sysupgrade载荷一致性。
SquashFS按superblock的bytes_used逐字节比较；sysupgrade仅允许标准1KiB对齐的
补零，factory剩余LEB空间仍必须是擦除态0xff，不忽略文件系统正文的末尾零字节。
容量检查保留整片256 MiB NAND的坏块预算、UBI开销和至少32 MiB的data卷LEB空间。
后者不等于UBIFS最终可用容量。系统内升级也重新检查布局和FIT配置。

Mihomo位于SquashFS，overlay供配置、规则和数据库使用。在线覆盖ROM内核心会在
overlay另存一份大文件；默认通过重编固件升级核心，需要独立更新时另行规划外置存储。

## 首次启动的网络和代理

- LAN作为DHCP客户端从GS7获取地址、网关和DNS，系统与客户端hostname均为 `RC01`，
  不固定 `.50.3`；在GS7租约中按hostname和实际MAC查找地址。设备获取自身管理地址，
  LAN DHCP服务器仍关闭。此前 `43fc40c991` 已在本机通过 Du v2 启动，获 `.50.219`；
  Viking sync 082981已于2026-11-04启动，风扇有实际风量、三路DS节点已注册；
  AP数据流、完整温控与代理共存仍待验收。现场随后设静态`.50.3/24`，固件首启仍保持DHCP。
- 首启启用HTTPS监听，由设备本地生成证书，保留HTTP且不强制跳转；不预置证书私钥。
  Tailscale主服务与辅助服务均禁用，持久设置 `service_enabled=0`，避免helper恢复默认启用。
- `lan1..lan4` 和 `sfp` 在LAN桥内；10G电口 `wan` 保留且不自动启动，WAN6同样关闭。
- DHCPv4/v6、RA、NDP关闭，不对整个LAN强加SNAT；IPv4 ICMP redirect发送/接收均关闭。
- DNS缓存8192、EDNS1232；删除继承的最低TTL/stale配置，恢复正常负缓存开关，关闭
  独立UDP DNS redirect，代理启用时由选定后端统一管理DNS路径。nonegcache=0不等于负缓存已验收。
- Router Tool保留，自动路由预设关闭，平台PPE默认设置仍初始化一次。后续手动修改
  offload不会被每次重载覆盖。LuCI保存路径同样遵守手动管理选项。
- 三路无线在首次生成配置时即关闭，没有SSID密码、订阅或代理账号预置。
- Nikki和Mihomo默认选入构建，但未配置代理接管。NAS、UPnP不选入；dae/daede仅为可选测试构建。

不能保留QWRT的eth接口配置跨驱动升级。单客户端代理验证应同时检查DNS、IPv6和
同网段回程；关闭RC01自己的RA不会阻止GS7的RA抵达同一二层网络的客户端。
保留原生PPE能力，用新建连接做offload开关对照。空闲计数为0或不对称回程下未命中，
不能单独判驱动故障。ath12k DS仅在所支持AP/授权peer路径启用，MLO回退不等于无线失败。

旧构建曾按WSI拓扑把RC01聚合排序为wifi2→wifi3→wifi1；本候选对QCN9274 HW1
使用三个独立非MLO组和独立wiphy，不再宣称旧聚合/MLO能力。HW2/AHB的合法MLO路径保留。
当前 `43fc` 配置使用 `phy=wl0`，radio0/1为5GHz、radio2为2.4GHz，全部禁用；已有检测脚本
不会迁移保留配置中的旧radio映射。升级后保持无线关闭，按实际新枚举核对频道/频段和物理卡，
再决定是否重建配置。不要仅凭radio0/1名称判定低/高5GHz，也不自动迁移QWRT无线配置。

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
缺少可选dae/daed时界面显示“本次构建未选入”，不会把它当成必需硬件故障。

安装本固件后，可使用以下只读命令；输出直接写到终端，不生成报告文件：

```sh
rc01-check status
rc01-check check hardware
rc01-check check nikki
rc01-check check dae
rc01-check check daed
```

`status` 输出JSON快照；`check` 输出所选预检项，发现错误时退出码为1，参数错误为2。
警告不会被当成已验证的兼容性结论。代理预检检查核心可执行性、双核心或启用状态冲突、
手动网络管理及offload基线；dae/daed还要求运行内核的BTF文件。
不会启动/停止代理、加载或卸载驱动、改防火墙、改风扇、发送测速流量或刷写分区。

RPC仅开放 `rc01.status` 的读取权限，没有执行命令、UCI写入或启停服务的接口。
模块仅取所需UCI启用标志，不输出订阅或代理配置；不读取环境区内容、代理配置正文、
进程命令行或日志。环境映射匹配不能证明CRC有效，
CRC检查仍由已有升级前检查执行。无线禁用配置不能替代实际射频状态检查；PPE流数为0、
端口未插线或速率不可读也不会被误报为硬件损坏。硬件故障、内核BPF加载和流量验收仍需
在后续独立实机测试中确认。

## dae/daede源码与试验顺序

2026-11-02接入 `kenzok8/openwrt-daede` 固定提交
`1eedcafd1cd4d8a89ac928be6608df76ffb38f78`：dae **2026.10.30-r2**、LuCI **1.15-r14**，
release增加用于区分本地修补。核心使用官方dae加olicesx性能分支整合源码，outbound与
quic-go也有固定性能分支，不能称为纯官方dae。源包完整SHA256在配方中，组件提交在
feed的 `ci/pins.env`；CI产物同时保存该锁和本地补丁。保留来源、原作者与许可。

本地补丁位于 `config/rc01/patches/daede/`，由准备脚本按固定HEAD幂等应用：
- dae由guard托管，TERM转发后最多等待20秒，回收核心后再清理保留名称daens/dae0；
  发现其他代理进程或不能核实状态时拒绝清理/启动，不删除共享BPF pin或其他TC规则。
  核心负责TC/BPF脱附，强制终止后仍需单独核对TC状态。
- 使用本次实际内核的BTF，不选另编影子内核的vmlinux-btf包。
- LuCI默认后端为dae，默认地理数据库更新源与镜像内v2fly数据一致，规则使用cn。
- 禁止LuCI在线升级核心/UI，防止脱离源码锁定并在overlay复制ROM大文件。

旧实验项使用daed1.27.0与归档dae-wing；本次不再选择它，也不同时安装旧luci-app-daed。
本次LuCI已包括中文资源，支持节点转换及表单配置；Clash YAML转换不等于迁移完整
Mihomo分流、分组和DNS配置。当前QWRT5.4不适用于此核心；试验平台为新固件的6.18。
配置或编译通过均不表示RC01/PPE/无线DS已实机兼容。

试验按以下顺序，涉及实机操作时单独执行：

1. 首次完整构建先通过编译内核/BTF、镜像和容量检查；不要将“配置通过”称为“构建通过”。
2. 保持Nikki/dae都未接管，核对基础网络与恢复入口。每次只启动一个透明代理，切换前停掉
   旧服务并核对TC/BPF、nft规则、DNS监听和策略路由；安装在同一镜像不等于同时运行。
3. 选一个测试客户端、保留其他客户端原网关。以软件/硬件flow offload关闭为代理基线，
   验证旁路由LAN接口与本机出站接口绑定，不能将被禁用的 `network.wan` 当作实际上联。
   检查TCP、UDP、DNS、IPv4/IPv6、直连与代理出口、MTU及同网段回程。
4. 核对 `/sys/kernel/btf/vmlinux`、TC/BPF附着、veth/命名空间、实际报文与泄漏情况。
   挂载成功或BPF已附着不等于接管成功；有线与无线需分别测。
5. 保持新连接测试方法一致，再对照软件offload、PPE与无线DS。未证明代理流量正确排除前
   不承诺全加速共存，也不为测代理而卸载PPE驱动或破坏LAN硬件网桥。
6. 通过前述项目后做24–48小时内存、连接反复建立、DNS缓存及冷启动检查。
   RSS持续增长、接管漏流量、DNS失联、内核异常或管理不可达时停止dae试验并恢复Nikki基线；
   zram不能当作内存泄漏的修复。

核查来源：
- [固定源码及组件锁](https://github.com/kenzok8/openwrt-daede/blob/1eedcafd1cd4d8a89ac928be6608df76ffb38f78/ci/pins.env)
- [发布版跨架构CI](https://github.com/kenzok8/openwrt-daede/actions/runs/41186972600)
- [重启/命名空间问题#13](https://github.com/kenzok8/openwrt-daede/issues/13)
- [规则与geosite不匹配#109](https://github.com/kenzok8/openwrt-daede/issues/109)
- [单接口旁路接管报告#1032](https://github.com/daeuniverse/dae/issues/1032)

## 风扇与实机验收

使用现有主线EMC驱动，按设备身份寻找 `emc2305` hwmon，不固定I²C编号或hwmon序号。
PWM使用正常极性、通道0的推挽输出，与QWRT0726/1007实际驱动行为对齐。
`26000` 为binding参数，Linux6.18当前驱动并未实际写入频率寄存器，不能宣称已配置26kHz。
内核thermal使用SoC原有nss-top传感器，每秒采样：基础state3（PWM77/255）、
50°C state5、60°C state7、70°C state10，回落迟滞3°C。原有critical与CPU温控保留。

独立守护服务开机先请求全速3秒，再提供非零PWM下限。温度过高或连续三次
转速/传感器/PWM异常会锁定全速；停止服务也请求全速。温控曲线由内核负责，
守护服务退出后不会把风扇主动关停。I²C或硬件故障时软件无法保证风扇实际转动。

**这套曲线尚未在新驱动上标定。`43fc` 虽已启动，但开漏配置下PWM255、RPM0且实物无风；
改为推挽后的起转、最低状态及故障处理仍须实测，不能提前记为修复成功。**
完成可恢复路径准备后，先验证风扇起转、转速、PWM响应、传感器及故障处理，
再做负载；风扇应转不转或温度持续失控时停止负载。随后验证每个端口、
代理与加速共存、无线/AP/DS、冷启动和长期运行。编译通过不替代这些检查。
试启动和刷机另行执行，不是CI构建的后续自动步骤。

新系统的升级检查会拒绝不带双配置节点的旧QWRT sysupgrade镜像。回退应准备维护主档
中已验证的U-Boot网线恢复入口及0726 factory材料，再在QWRT内升级到1007；
不要用强制参数绕过不明检查错误。新系统到QWRT的完整回退流程仍需后续演练。

## 2026-11-04：旁路由调优依据与实施边界

现场082981已由DHCP`.219`改为静态`.3/24`、网关/DNS`.1`，SSH、HTTP/HTTPS、
GS7双向连通和本机解析均通过。先私密备份、启动五分钟设备侧回退、验证临时新地址，
再持久切换并在验收后解除回退。当前静态可用不能解释旧b834当时失联；CIDR本身合法。
GS7仍将`.3`保留给旧MAC，未来存在重复分配风险，应由用户确认后修改或移除；本轮未改GS7。
现机不要重跑89，它会把现场静态地址改回DHCP；下版继续使用可发现的DHCP首启。

现机的`min_cache_ttl=3600`、`use_stale_cache=3600`、`nonegcache=1`、`dns_redirect=1`
来自基础默认配置，之前只检查RC01片段不足以排除它们。已按Astra xhigh复核移除/复位；
init未映射stale参数，因此只撤销潜伏配置，不能称此前过期应答已生效。重启dnsmasq后
检查生成配置及include、上游和`inet dnsmasq`表消失，不用全局清conntrack。
A/AAAA TTL83/82秒到82/81秒、TCP查询通过。带SOA的重复NXDOMAIN仍被转发，
现机负缓存未验收。Astra已确认009安全补丁把零长度blockdata_alloc变成NULL，
导致find_soa无法储存SOA；004子域判断正确。010采用正式上游
`10fbc39cf57220eb93f61ce282d7561750f8d29d`（DL6ER），在真实分配路径恢复零长度空块，
同时修复IPC零长度RR；保留009的失败排空等安全逻辑，原009逐字节不变。dnsmasq包从2.93-r9
提升为2.93-r10；不以neg_ttl或撤安全修复掩盖。

12项真实C夹具已在校验过的官方源码和全部14个顺序补丁上通过，补丁无fuzz/偏移；
ASan、UBSan和泄漏检测同样全部通过。旧wrapper-only010作为对照，仅两个zero-length
IPC夹具失败；不得把旧wrapper语义记为最终修复。测试可自动发现prepared源码，
编译前没有源码时明确skip，编译后才运行；不将其当作目标固件DNSSEC/ubus集成或现机验收。
本轮未提交、推送、出包或替换在线dnsmasq，下版完成构建后需重测实际负缓存。

下表列出可讨论的主要调优项；“保持”表示没有实测瓶颈支持立即调整，不是最优值认证。

| 项目 | 当前依据与建议 | 何时再调整、怎样验收 |
| --- | --- | --- |
| DNS容量/TTL | 保持8192/1232，遵循收到的上游TTL，不强延长；不默认过滤AAAA | 用`ubus call dnsmasq metrics`看提前淘汰与转发增量、进程RSS；代表性负载后再改容量。GS7收到的TTL不等于权威TTL证明 |
| DNS负缓存/劫持 | nonegcache=0、dns_redirect=0，直接`.3:53`服务继续；NXDOMAIN未通过 | 正/负查询、带SOA的NXDOMAIN、TCP与TTL；实际经RC01转发到其他DNS的新流量待单客户端测试 |
| zram容量/算法 | 保持858MiB逻辑容量、LZO、priority100；不预占等量物理内存，当前未swap | 未来规则加载、稳定代理和恢复阶段观察mm_stat峰值、实际压缩收益、pswpin/out增量及CPU延迟。当前仅lzo-rle/lzo可选，LZ4/zstd需额外内核支持与对照 |
| VM回收 | 保持swappiness60/page-cluster3/compaction_proactiveness20/min_free_kbytes16384 | swappiness是相对回收成本，不是百分比阈值；只在持续换入换出/回收延迟时单变量对比，不照抄100/133或每日drop_caches |
| CPU调频 | 保持schedutil，936–2208MHz | 只在负载下频率/单核瓶颈出现时对照临时高频，记录吞吐、尾延迟、温度和功耗，不固定满频作默认 |
| IRQ/RPS/RFS | packet_steering0，实读各LAN接收队列RPS/RFS为0；当前低负载 | 单核softirq持续饱和、多流/小包受限时，按实际平台steering实现对照IRQ与RPS；检查IPI/缓存开销，不假设全核掩码必优 |
| PPE/DS/offload | 三provider注册，流量0；软硬offload当前1，未改 | 代理首测先关闭offload，再按直连/代理、TCP/UDP、IPv4/6检查规则与硬件计数；注册不证明任意路径已卸载，MLO仍可CPU fallback |
| SQM/qdisc | 本轮不新增整形，也不扩大队列 | 确认RC01真正位于拥塞路径，才在饱和上下行比较延迟、吞吐、CPU和qdisc计数；flow offload可能绕过整形 |
| MTU/EEE | MTU1500；EEE未修改、当前具体状态未采集 | jumbo需全路径支持且有收益；协商/误码/唤醒问题才A/B测试EEE。检查PMTU/ICMP与IPv6，不按10G规格直接设9000 |
| conntrack/TCP | 样本84/65535，OOM0；不扩大conntrack/socket缓冲或切BBR | 接近表上限/明确重传或长RTT窗口受限时，结合内存与实际代理路径测量；单纯桥转发不由RC01本机TCP拥塞算法加速 |
| IPv6 | RC01不发RA/不提供DHCPv6，只有link-local；不擅自关全网IPv6 | GS7的RA仍能直接抵达客户端；启用代理时对IPv6出口/DNS/绕行另验，AAAA解析成功不等于IPv6路由可用 |
| 存储与更新 | root upper约40.5MiB总/38.3MiB空；核心在SquashFS，不合并70MiB | 规则/数据库原子更新需预算新旧文件和临时空间；在线覆盖核心会占一整份overlay。重分区/extroot单独设计与恢复验收 |
| 日志/时间 | 128KiB RAM环缓冲，无持久日志；NTP客户端已运行，时间一致 | 按排障需要短时扩日志或远端接收，避免长期写闪存；独立核实NTP同步，不能由进程存在就认定完成 |
| 服务/管理 | 无线、Nikki/dae、VPN保持关闭；监听22/80/443/53 | 用户自行设置root密码/公钥。自签HTTPS应核对证书，确认客户端可用后再讨论停HTTP；不替用户生成未知密码或先关唯一入口 |
| 散热 | PWM77、约1876–1900RPM、有风、fault0，空闲约38–39°C | 下一维护窗口验证冷启动、完整升降温/滞回和长期运行，保留故障满速保护，不用静音优先去降保护阈值 |
| 无线规划/MLO | 当前两个5GHz都为36/EHT160，2.4GHz为1/EHT20，全部禁用 | 启用前按实际radio/WSI核对各卡能力并规划频道；聚合wiphy频率列表不代表每张卡都支持。比较80/160MHz、DFS与MLO客户端组合，保留MLO peer的CPU fallback |
| CMA/无线保留区 | 现机历史默认64MiB；新候选CMA320MiB并撤四个旧carveout | zram不是连续DMA内存解法；新候选仍需按调用者峰值、失败与长期内存压力验证，不把所有-28归给CMA |
| 旁路回程 | LAN masq0，redirect0 | 同网段可能回程绕RC01；实际代理试验若需要对称，再针对测试客户端选择窄SNAT或改拓扑，不先把整LAN NAT |
| 备份与恢复 | 当前只做配置重载，boot ID不变，私密前后备份已设备外保存 | 网络定时回退不是固件自动回滚；当前MIBIB已改单槽布局，升级需匹配镜像/guard与已验证Du恢复，不假设保配置覆盖全部数据 |

zram算法来源补充：当前选择zram-swap而未显式设置`system.@system[0].zram_comp_algo`。
本地Kconfig默认LZO-RLE，旧zram.init却在未配置时写`lzo`，现机运行脚本同样如此。
因此保留LZO仅是现状基线，并非已证明它优于LZO-RLE/LZ4。现机已有LZO-RLE后端；
6.18也支持普通LZ4，只是本构建没启用`CONFIG_KERNEL_ZRAM_BACKEND_LZ4`。
后续候选可同时编入LZO/LZO-RLE与LZ4、显式选择算法并用相同负载对照，勿只装压缩库或强装不匹配kmod。
7.3的LZ4字典性能补丁只在使用字典时相关，不是启用普通LZ4的版本门槛。
本次仅核对来源，没有重启zram、切换算法或执行内存压力测试。

QPIC四线核查补充（2026-11-04）：现机已确认ESMT F50D2G41KA、256MiB、ECC8/512，
GPIO0～3均复用为qspi_data；这不能单独证明板上IO2/IO3连通。当前6.18.55执行路径硬编码x1，
现有generic/qualcommbe补丁未加入x4实现；live flash节点也缺spi-rx/tx-bus-width。
`nand-bus-width=8`不代替SPI位宽。升级基础内核不会自动为当前设备树启用四线。
[原厂产品规格](https://www.esmt.com.tw/en/product/item/F50D2G41KA-2V-Ind)已明确Single/Dual/Quad。
[原厂手册](https://www.esmt.com.tw/download/F50D2G41KA-2V-ind?open=1)为2023-05 Rev1.0、59页，
SHA256 `b881cc742499c84ce3b419efde70821ae34678c7901a2e21b538f6a156fa26d5`；
第8/26页列6Bh x4读取，第13/15页明确B0.bit0为HD（Hold Disable）、出厂0，而非QE。
7.3 ESMT表flags=0与此相容；新QPIC却由GET_FEATURE(B0).bit0更新quad_mode并据此门控x4，
因此不能把该位当通用QE、为启用四线直接置位或给本芯片盲加SPINAND_HAS_QE_BIT。
回移还需包含6Bh映射的后续7e3ffa9f，不能只摘cdb1dbba；正常编程路径的实际位宽也需单独核实。
后续先修正并审查该型号的模式判断，再以控制器实际模式证据与同区域x1/x4读取摘要、ECC增量及耗时验证。
缺少的收益证据是成功的实际x4传输、有效SCK和总I/O中串行阶段占比；不能承诺整机或闪存总性能四倍。
本轮未切换闪存模式、重新probe、刷机或变更在线配置；未保存手册文件。

完整性能对照应区分同桥LAN流量、经RC01的L3流量和RC01本机代理/VPN流量，
分别测单流/多流/双向/小包、空闲及负载下延迟、每核CPU/softirq、丢包、温度、内存。
每次只改一个变量，重复同一工作负载并保留可恢复基线；本轮不在用户离开时压测或启用无线/代理。

官方依据：

- [dnsmasq手册：缓存、TTL、负缓存、EDNS和统计](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)
- [Linux6.18 zram](https://docs.kernel.org/6.18/admin-guide/blockdev/zram.html)、[VM sysctl](https://docs.kernel.org/6.18/admin-guide/sysctl/vm.html)
- [IP sysctl](https://docs.kernel.org/6.18/networking/ip-sysctl.html)、[CPUFreq/schedutil](https://docs.kernel.org/admin-guide/pm/cpufreq.html)、[RPS/RFS与IRQ](https://docs.kernel.org/networking/scaling.html)
- [flowtable](https://docs.kernel.org/networking/nf_flowtable.html)、[TPROXY](https://docs.kernel.org/networking/tproxy.html)、[conntrack](https://docs.kernel.org/networking/nf_conntrack-sysctl.html)
- [OverlayFS](https://docs.kernel.org/filesystems/overlayfs.html)、[EMC2301/2305](https://docs.kernel.org/hwmon/emc2305.html)

社区提供风险和实验线索，不构成RC01验收：[zram算法讨论#14441](https://github.com/openwrt/openwrt/issues/14441)、
[PPE RFC#24178](https://github.com/openwrt/openwrt/pull/24178)、[flowtable与qdisc#26305](https://github.com/openwrt/openwrt/pull/26305)、
[MT7621上的steering/GRO/SQM组合#18901](https://github.com/openwrt/openwrt/issues/18901)。
若干OpenWrt Wiki请求仅返回Anubis验证，论坛搜索429，未冒充读到正文；机制判断以实际读到的Linux/dnsmasq文档、锁定源码和当前设备为据。

### 未来Nikki/Mihomo资源调优

本节是未来单客户端试验的建议，不启用代理或加入订阅。新候选Mihomo固定1.19.33；
Nikki锁定78e1a751d55ed0762811f054ee699049473da7b8，包版本2026.10.10-r1。
必须核对生成后的最终配置，LuCI合并选项优先于mixin文件，不能只看订阅原文。

- Geo数据先保持memconservative加载器；MMDB/DAT由规则实际需求决定，不并存下载无用的大型数据库。
  规则集只保留需要的集合；MRS只适合domain/ipcidr，不能直接替代classical。比较文件大小、启动RSS、载入时长及更新临时空间。
- DNS缓存容量与fake-ip地址池是两个参数；不因缩小198.18.0.1/16就假定内存等比例降低。
  先排除与局域网/VPN路由重叠，统一dnsmasq、Nikki与Mihomo的DNS监听、转发和劫持所有权。
- Nikki当前模板tun_stack=mixed，以最终生效值为基线。此前调研核对的Mihomo1.19.32源码自带默认为C.TunMips，
  两者不同，不把核心默认照搬到Nikki；不先行切system/gvisor/mips或改MTU，按TCP/UDP/IPv4/6流量比较。
- TCP concurrent暂不主动开启；只有多地址连接建立慢才对照，评估额外并发的连接数和资源成本。
  嗅探只在缺失域名影响分流时按协议/端口/跳过范围验证，不把parse-pure-ip/强制覆盖视为免费提速。
- 默认策略选择/fake-ip持久缓存涉及数据库写入；先测实际落点与增长，权衡重启后的映射连续性和写入。
  不把cache.db简单放进RAM或直接关fake-ip持久化而忽视仍持有旧映射的客户端。
- 由Nikki负责的接管规则不再叠加无条件auto-route/auto-redirect/strict-route或另一后端；
  RC01自身流量、选定客户端、同网段回程与IPv6需一起验收，DNS ipv6=false不是完整的IPv6路由方案。
- 核心内置SquashFS，约38MiB空闲overlay不够容纳约55MiB新核心；升级核心沿用重编固件流程。

参考：[官方Geo/通用配置](https://wiki.metacubex.one/config/general/)、[规则集](https://wiki.metacubex.one/config/rule-providers/)、
[DNS](https://wiki.metacubex.one/config/dns/)、[TUN](https://wiki.metacubex.one/config/inbound/tun/)、
[嗅探](https://wiki.metacubex.one/config/sniff/)、[此前调研版本的默认值源码](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/config/config.go)。


## 2026-11-05：新机候选的离线验收边界

官方hash验证过的6.18.60与backports7.2干净源包，按本仓库真实顺序分别应用
699个内核、310个无线补丁，全部通过且无fuzz（有正常行号offset）。无线使用RC01的
非NSS ath11k host组，未将仅AX适用的NSS补丁混入。整体prepare、默认非dae配置与
平台安装配方检查通过。真实DTS经CPP/dtc编译，校验上述内存、MAC、PHY、NAND、温控和DS资源。
prepared源码同时核对两处CVE析构修复、0411 ESMT优先匹配、0412及动态AFC/HW1非MLO路径。
`validate-rc01-platform.py` 和 `tests/rc01/test_platform.py` 在CI编译后用真实prepared树再跑。
构建输入另有 `rc01-source-metadata.json`，核对实际prepared核心配方hash与feed锁，记录
内核、mac80211、ipq-wifi、dnsmasq、Mihomo/Tailscale/Nikki/Go来源，产物保留验证JSON。

本阶段未全量交叉编译工具链或生成新固件；最终内核/模块链接、真实FIT/UBI容量和32MiB
rootfs_data预算交给父线程CI。旧机不部署；新机候选不携带旧机MAC/caldata、现场静态`.3/.1`、
节点或VPN账号，首启继续DHCP且无线/代理/VPN关闭。元数据标注 `physical_acceptance=not-performed`；
准备、源码和夹具通过均不代表新机冷启动、风扇曲线、PPE/DS、无线及流量已实机验收。
