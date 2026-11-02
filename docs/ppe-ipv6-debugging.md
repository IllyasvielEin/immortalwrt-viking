# PPE 卡顿修复与测试环境

适用本轮外部反馈：AX PPE、PPPoE、IPv4/IPv6 TCP/UDP 间歇卡顿与 ADD FAIL。2026-11-02 已在 RE-CS-02 与 CR1000A 的隔离 WAN 直连环境建立 PPPoE、PAP 认证、IPv6-PD 和双栈回显流量。硬件拒绝真实存在，尚未确认它与外部反馈卡顿的同一五元组关系。

本轮以 `e5b0e821565` 为基线；反馈版本声称 `cf0998e`。2026-11-02 核对 JB `ppe-nss` 仍为 `06531c585ded`。候选修复将驱动 `HASH_MODE0/1` 从 XOR/XOR（1/1）改为 SDK 寄存器默认的 CRC10/XOR（0/1），同时保留 IPv6 TCP/UDP 卸载。

RE-CS-02 上每组 60 秒、同端口 TCP/UDP 并发：16 TCP + 16 UDP 的 IPv4/IPv6 控制组在两种模式下均无新增硬件 ADD FAIL；32 TCP + 32 UDP、端口步长 1024 的 IPv6 组从 583 次降为 0；64 TCP + 64 UDP 的 IPv6 组从 1263 次降为 30。后者 30 次全部来自两条 IPv6/TCP 定向五元组重复重试。该结果证明特定流集的加表行为改善；回显超时没有同步下降，不能据此宣称原场景卡顿已修复。

CR1000A 的同组回归另有 `ingress_vlan_domain` 拒绝，部分方向仍走软件路径。它用于覆盖模式切换后的可达性和已安装方向，不能替代完整双向硬件验收。两台实验寄存器最终都恢复到原来的 1/1；新默认值需由新固件启动初始化生效。

**接线：两台路由器、电脑两张测试网卡、三根网线**

```mermaid
flowchart LR
    PC1[电脑测试网卡 1<br/>客户端与 A 管理] --- ALAN[A：DUT 的 LAN]
    ALAN --- A[A：被测路由器<br/>PPPoE 客户端 / PPE 开启]
    A ---|A WAN 直连 B WAN<br/>PPPoE / IPv6| B[B：接入端<br/>PPPoE AC / DHCPv6-PD / 流量服务端]
    B --- BLAN[B 的 LAN 管理]
    BLAN --- PC2[电脑测试网卡 2<br/>只管理 B]
```

电脑保留现有上网接口。MAC `8c:3d:16:62:7a:e1` 不参与测试。两张可用有线网卡 MAC 为 `8c:8c:aa:45:52:b3` 和 `98:fc:84:e6:a4:33`，接口名、接线和对应设备须现场复核。串口 COM1/COM2 保留。

- A 优先使用原反馈 RE-CS-07；若先用现有同平台设备，只作为 IPQ60xx 实现验证。之后交换 A/B 角色，覆盖 IPQ807x，不能将同 SoC 结果当成原机型验收。
- A、B 的 WAN 都从正常上联网拔下，再相互连接。测试服务只绑定隔离 WAN，不接入家用 LAN。
- 电脑的第二张网卡仅管理 B，**流量服务进程运行在 B**。不要把服务地址设为电脑另一张网卡的地址，否则同一主机/WSL 可能本地送达，根本没有经过 DUT。
- 有线基线通过后，在 A 的同一 LAN 用户口接外部 AP，客户端改走该 AP 的 Wi-Fi，覆盖原附件中的外部 AP 桥接场景。最后再单独检查 A 自带 Wi-Fi/NSS。

**地址与服务配置目标**

| 项目 | A：被测拨号端 | B：接入及服务端 |
|---|---|---|
| LAN 管理 | 192.168.1.1/24 | 192.168.2.1/24 |
| PPPoE IPv4 | 100.64.61.2 | 100.64.61.1 |
| PPPoE 凭据 | 用户 `ppe-test`；每次生成随机非空密码 | 单独的测试 PAP/CHAP 认证；不复用真实上网凭据 |
| MTU/MRU | 1492 | 1492 |
| DHCPv6-PD | 请求 /56，向 LAN 分配 /64 | 委派 `fd42:618:1102:100::/56` 并安装指向 PPP 会话的回程路由 |
| LAN IPv6 | `fd42:618:1102:100::/64`；测试客户端例如 `::2` | 不使用 A 的委派前缀作本地 LAN 前缀 |
| IPv6 测试服务 | 访问右侧服务地址 | 独立服务地址 `fd42:618:1102:ff00::1/128` |
| 流量服务 | 电脑经 A LAN 发起 | B 上运行 iperf3、UDP 回显/序号探针 |

ULA 用于受控复现与驱动回归。若故障依赖运营商 GUA、PD 更新、代理或真实业务，它不能替代原反馈现场的验收。

B 需要 `rp-pppoe-server`、PPP/PPPoE、完整 DHCPv6-PD 服务、iperf3 和 tcpdump。配置 PPP IPv6CP 只能建立 IPv6 链路参数，**不会自行完成 DHCPv6-PD**；要同时确认 DHCPv6 服务和委派前缀的回程路由。具体动态 PPP 设备绑定按 B 当时的软件版本处理，不预设其一定叫 `ppp0`。

A 的 WAN 使用 PPPoE、IPv6 自动方式，LAN `ip6assign=64`；LAN 前缀类别不写死 `wan6`。保留 TCP 和 UDP 的正常 flow-add 规则，IPv6、PPE 和默认 QoS 开启；起始阶段不配置额外整形、代理或 VLAN。B 为 A 的 LAN IPv6 提供明确回程路由，测试服务绑定在 B 而非 A 的本机地址。

本轮 AC 使用 `/48` 父池并设置最小委派长度 `/56`，实际委派 `fd42:618:1102:100::/56`。客户端 PPP 参数使用 `pppd_options='mtu 1492 mru 1492'`，物理 WAN 保持 1500，需读取最终 PPP 设备 MTU 确认；直接把物理 `wan.mtu` 设置成 1492 会使本环境 PPP 最终变成 1484。

接线后按以下顺序验收，未通过前不开始驱动结论对比：

1. 核对 NIC MAC、LAN MAC、board_name、串口与端口角色，保存两机配置及原始状态。
2. 验证 PPPoE 发现、认证和会话建立，记录 session ID、实际 PPP 设备和物理 WAN。
3. 验证 IPv4、IPv6CP、DHCPv6 IA_PD、LAN RA/SLAAC 及客户端地址，再验证双方路由/邻居/回程。
4. 从 A 的 LAN 客户端访问 B 的 IPv4/IPv6 服务；同时检查 A 的物理端口和 B 的接收端计数，排除本地主机短路或代理转发。
5. 分别核对两个方向的 PPE entry 与硬件命中增量。连接级 `HW_OFFLOAD` 不能替代逐方向验证。

**如何抓包，避免再次把不可见当成丢包**

仓库脚本 [ppe-field-capture.sh](../scripts/ppe-field-capture.sh) 在 A 执行，四个接口参数须来自实际设备状态：

```sh
sh /tmp/ppe-field-capture.sh \
  fd42:618:1102:100::2 lan1 pppoe-wan wan 120
```

参数依次为客户端 IPv6、LAN 用户口、逻辑 PPP 设备、真实物理 WAN、时长。它不应用网络配置，三个接口分别不加 BPF 抓包，保留 PPPoE、VLAN、IPv4/IPv6 和 ICMPv6/ND；每侧 2 个 2 MB 轮转文件，总抓包上限约 12 MB 加少量文件尾开销。目录位于 `/tmp/ppe-field-*`，要求至少 64 MiB 空余。

- 启动和结束保存完整 nft ruleset、TC/qdisc、IPv6 路由/邻居、bridge VLAN/FDB、端口计数和 PPS/CPU 相关状态。缺失的可选命令会明确记录在 `command-status.txt`，不会冒充采集成功。
- 每约 2 秒保存 PPE 计数器与失败环，前后配对 wallclock、uptime 和 boot_id。采样可能仍漏掉高频失败的中间记录，应核对序号缺口。
- 前台采集按 Ctrl-C 会停止自身 tcpdump 子进程，再保存结束状态和校验清单。后台启动时 shell 可能忽略 SIGINT，应向脚本 PID 发送 `kill -TERM PID`，等待 `completion.txt` 和 SHA 清单生成并确认子进程已退出。不要 `kill -9`；轮转仅保留最近报文，高吞吐时必须及时停止或由电脑持续拉取。
- 周期采集不扫描整张硬件流表；需要时另做一次锁持有时间受控的资源/同键检查，避免把大量 MMIO 读开销引入正在观测的停顿。

**B 的真实 WAN 接收抓包是关键对照**，它能观测 A 的硬件转发到达对端的结果。B 上与 A 同步运行以下命令，故障出现后 Ctrl-C，保存 tcpdump 输出的内核丢包统计：

```sh
umask 077
mkdir -p /tmp/ppe-peer-capture
tcpdump -p -nn -Z root -i wan -s 0 -U -C 2 -W 2 \
  -w /tmp/ppe-peer-capture/peer-wan.pcap
```

这里的 `wan` 也须核对为 B 连接 A 的物理口。B 的 PPPoE 会话终止于本机服务，适合作为对端观测；若 B 的实现仍使该 tap 不可见，再使用镜像口或外部抓包。跨设备关联以五元组、PPP session ID 和探针序号为准，不靠两台墙钟的微小差值计算 RTT。

在电脑上解码新失败：

```sh
python3 scripts/ppe-field-decode.py \
  CAPTURE_DIR/after/offload_failures.txt \
  --before CAPTURE_DIR/before/offload_failures.txt > new-failures.json
```

先比较 `before/time-before.txt`、`after/time-before.txt` 的 boot_id，必须是同一次启动。`selection.records_no_longer_retained` 表示序号增量中未保留的条数；结合中间样本才能补齐，不能用最后 32 条代表全部尝试。也可不加 `--before` 解码历史快照，但输出会明确标为 retained history。

**测试顺序与可观测判据**

| 阶段 | 流量/条件 | 主要判断 |
|---|---|---|
| 1：基础 | IPv4/IPv6 × TCP/UDP × 上/下行；先 1–2 Mbit/s、600 秒 | 实际流经 A；双向可达；分方向硬件命中与新增失败 |
| 2：连接生命周期 | 1/16/64 并发、固定及随机端口、短连接/长连接、12–30 秒空闲后恢复 | 重试、资源回收和同一五元组的完整事件链；不按失败次数当独立流数 |
| 3：协议复用 | 同一地址/端口对，先 UDP 后 TCP、反向顺序、两协议并存；独立绑定探针端口 | 分别记录两种 L4 协议；是否产生同键 ADD FAIL、错误命中或残留项 |
| 4：小包与排队 | 64–256 字节低速双向序号 UDP，叠加 TCP；负载逐级增加 | 客户端单调时钟 RTT/丢包/乱序；定位 44–64 ms 等时间差落在哪一段 |
| 5：现场条件 | PPPoE over VLAN、外部 AP、自带 Wi-Fi/NSS、QoS/整形分别单变量加入 | 哪个具体条件是触发器；每次保留正常与异常样本 |
| 6：长稳 | 原业务与混合连接负载 8–24 小时；记录 PD/邻居变化和断线重拨 | 是否小时级累积；保留事件前后的捕获，不能只看短流通过 |

小包延迟使用带序号和时间戳的回显探针，在同一客户端用单调时钟算 RTT；iperf3 汇总只能提供吞吐/UDP 丢包辅助证据。需要精确同端口 TCP/UDP 复用时使用独立绑定的探针，不把 iperf3 的 TCP 控制连接误当成 UDP 数据流。

基线完成且已捕获异常后，才按单变量对照当前策略与修复版本；必要的软件/硬件路径对照只在隔离环境短时执行并恢复。复现阶段不编译固件。B 的 CPU、软中断和服务负载也要记录，避免把接入端过载算作 A 的问题。

判断链路至少区分：

- A LAN 出现、B 物理 WAN 收到：不能认定 A 丢包；继续检查 PPP 解封、B 服务与回复。
- A LAN 出现、B 未收到：先核对双方采集覆盖和丢包统计，再沿 A ingress/TC/flowtable/路由/硬件出口定位。
- B 收到请求并回复、A 客户端未收到：调查回程 PD/邻居、PPPoE 入站、PPE 下行与 LAN/AP。
- 新增 ADD FAIL 与卡顿五元组/序号同时出现：进入同键硬件读回、host/flow 占用、下一跳和退役状态定位。若仍可正常转发，继续分辨只是未加速还是出现实际停顿。
- 同流 RTT/出站时间差增加但没有新增 ADD FAIL：优先关联 TC、CPU/软中断、实际存在的代理/队列路径；不将另一条历史 ADD FAIL 强行关联。

**修复点的选择与回归门槛**

本轮已按“停用软硬件流卸载 → 确认 live_entries=0 → 受控模块仅写哈希模式位并读回 → 恢复卸载 → 同流集对照”验证 0/1 组合。写寄存器的临时工具只用于实验，没有加入生产固件；驱动默认值在启动时一次初始化。SDK 的联合 host/flow 操作同样只等待 flow 结果，不支持此前“遗漏消费 host 结果队列”的猜测。

剩余重点是相同键重复/冲突、host 资源生命周期，以及部分方向加表成功后的软件路径。不要把 ADD FAIL 次数当独立连接数，也不要在仍有硬件流时在线更改哈希模式。候选修复未证明外部 AP、PPPoE VLAN、GUA、PD 切换、原机型或小时级稳定性已通过。

一旦确定机制，针对最小范围修改 IPv4/IPv6 公共路径，保存 A/B 源码与同环境异常/健康五元组；完成静态/构建检查后，在两平台验证该触发场景及必要相邻路径。以延迟分布、丢包、逐方向硬件计数和资源回收作为修复判据，不以 ENOENT 不增长、网页打开或单个卸载标志作为单独验收标准。

结束后恢复两机原网络/认证/防火墙/QoS、临时前缀/路由和工具，停止本轮采集与服务进程，再恢复 WAN 接线。管理与上网接口恢复结果单独记录。

参考实现：[Linux flowtable](https://www.kernel.org/doc/html/latest/networking/nf_flowtable.html)、[odhcpd](https://github.com/openwrt/odhcpd)、本项目 `feeds/packages/net/rp-pppoe` 的版本 4.0 和 `package/network/services/ppp`。外部资料快照及本轮校验记录保存于本项目 `backup_work/ppe-fix-prep-20261102/`；原始故障包与上一轮再分析保持原样。
