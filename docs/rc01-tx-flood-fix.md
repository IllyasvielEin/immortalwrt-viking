# PPE bridge TX 错误复制修复

2026-11-14，修复基于 `2172718aa1181da5da5ed54c0c259d0eb9ebe3f0`；当前构建与交付状态见[统一进度](rc01-progress.md)，实机验收仍待完成。
该提交的 `target/linux/qualcommbe` 与运行固件对应的 `1d62f4edd5` 源码一致。

## 缺陷与最小修复

Linux bridge 的 `offload_fwd_mark` 表示 TX forwarding offload，FDB 命中的已知单播也会带上它。
PPE 的 `edma_tx_fill_desc()` 却把该标记一律解释为 flood，再按以太网目的地址选 UC/MC/BC bitmap。
当 UC bitmap 为 LAN1+LAN2 时，本机向任一端口的已知单播会变成两口复制。
MDB 选择的特定多播目标也可能被通用 MC flood bitmap 扩大。

权威修复为
[`0685-net-ppe-disable-unsafe-tx-forwarding-offload.patch`](../target/linux/qualcommbe/patches-6.18/0685-net-ppe-disable-unsafe-tx-forwarding-offload.patch)。
它把 `ppe_bridge_port_join()` 的 `switchdev_bridge_port_offload()` 最后一个能力参数统一设为 `false`，
保留 switchdev 注册、hwdom、硬件 bridge TX MAC、VSI、VLAN 对象重放及 FDB/MDB 等原有路径。
Linux 因而为软件 TX 保留逐出口复制，已知单播只选一个目的端口，unknown unicast 仍送达所有合格出口。
EDMA bitmap 分支保留，但 PPE bridge 不再请求这项优化。

不能仅删 EDMA bitmap 分支：bridge 已按 hwdom 抑制的其他副本无法恢复。
也不能仅让组播使用 bitmap：unknown unicast 同样需要多个出口，会发生漏发。
重新启用此能力须先实现可靠的实际转发目标语义，覆盖 FDB、unknown unicast、MDB 和各端口策略。

## 注册与 VLAN 范围

对当前完整展开的 PPE `*.c` 检索，只有 `ppe_bridge_port_join()` 一个
`switchdev_bridge_port_offload()` 调用，没有独立 `switchdev_bridge_port_replay()` 调用。
VLAN-unaware 与 VLAN-aware 加入共用该入口；后者先登记端口状态，再由 switchdev 注册触发 VLAN 对象重放。
VLAN 增删对象处理不另行宣告 TX offload。重新加入也经过相同入口。
现有 `SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING` 处理拒绝在已加入后切换模式，没有隐藏的模式切换重注册路径。
本修复统一传 `false`，VLAN-aware 原本就是 `false`。

## 有判别力的离线回归

[`test_bridge_tx_flood.py`](../tests/rc01/test_bridge_tx_flood.py) 与
[`bridge_tx_flood_harness.c`](../tests/rc01/bridge_tx_flood_harness.c)
从 `RC01_KERNEL_SOURCE` 提取并编译实际 C 函数，包括：

- PPE 的完整 join、flood bitmap 更新、EDMA 描述符填写，以及实际描述符宏和结构。
- bridge 的 capability 注册/注销、TX 标记与 hwdom 抑制、已知单播 `br_forward()`、
  `br_flood()`、`br_multicast_flood()` 及实际 clone/forward helper。

测试按描述符 word4 的固定字段解码最终物理出口，检查每口副本数，不以源码包含 `false` 作为行为通过条件。
覆盖向 LAN1/LAN2 的本机与软件转发已知单播；三个合格出口的 unknown unicast 与 flood-off；
广播、多播、本机发送的 flood-off 例外；MDB 成员、路由器端口、重叠目标去重、blocked 成员和 multicast-to-unicast；
VLAN-aware 的各口 tag/准入夹具；hairpin、非 forwarding、isolated、IGMP，以及 RX 已由硬件转发时的同 hwdom 重复抑制。
还验证其它设备打开全局 TX offload static key 时，PPE 端口仍然按自己的能力处理。

负例恢复旧注册参数后，两个已知单播方向均得到 `word4=0x30060000`，即 type 3、bitmap `0x6`，被出口断言拒绝。
两个局部 EDMA 错误修法均在三个出口 unknown-unicast 场景漏发，被相同出口断言拒绝。
旧源码直接编译的独立负例与固定源码正例也保存在本次生成目录。

这些是宿主语义测试：FDB 查找完成后的目的端口由夹具指定；内存分配、netfilter、VLAN 查表/改 tag、
switchdev 通知及对象重放外围由桩实现。没有启动真实 Linux bridge/netns，也没有执行 PPE 寄存器、
硬件 VLAN/FDB/MDB 转发或 PHY 数据面。不能将其表述为实机功能或吞吐通过。

## 本次输入与验证

所有新生成物位于仓库 `build_dir/rc01-tx-flood-fix-20261114/`。
`linux/` 是从当前 `build_dir/rc01-pcs-fix-20261113/inputs/src/linux-6.18.60/` 复制的任务副本，
随后仅应用本次权威补丁；原输入没有修改。
另从该输入目录现存且哈希核对的 Linux 归档重新展开，重放当前仓库全部 generic/platform 补丁，
所得 `replay/linux-6.18.60/` 的 PPE 与 bridge 目录和任务副本逐文件一致。
`source-binding.json` 记录 HEAD、基线、补丁、测试、关键展开源码及归档摘要。
这些生成输入与本机验证记录不是提交产物，也不修改旧 `result.json` 的绑定。

定向回归 10 项通过；完整回归 640 项零失败、零错误、零跳过。
完整回归复用该批内核以外的 BusyBox、backports、ucode、LuCI、dnsmasq、firewall4 输入。
可从仓库执行以下定向检查：

```sh
. build_dir/rc01-tx-flood-fix-20261114/env.sh
UV_CACHE_DIR=/tmp/rc01-uv-cache uv run --no-project --python python3 \
  python -B -m unittest discover -s tests/rc01 -p test_bridge_tx_flood.py -v
```

本机 x86-64 `COMPILE_TEST` 的 `ppe_bridge.o`、`br_forward.o`、`br_switchdev.o` 已编译。
PPE bridge 既存 `ppe_bridge_foreign_dev_check()` const 形参警告需通过
`KCFLAGS=-Wno-error=discarded-qualifiers` 保留为 warning，未改该函数或全局告警策略。
完整 `edma_tx.o` 因既存 ARM 专用 `dsb(st)` 无法用 x86 工具链编译，当前没有可用 ARM64 交叉工具链；
实际 EDMA descriptor 函数仍由上述宿主 C 回归编译执行。没有 ARM64 目标编译或模块链接结果。

## 风险与后续验收

关闭这项优化会增加软件 flood 的逐口复制/发送成本；普通硬件 bridge 注册和转发能力仍保留。
host 测试不能证明硬件控制面和运行配置没有其它问题，也不能证明本缺陷是吞吐受限的唯一原因。
后续应在另行安排的固件构建和实机窗口核对两个已知单播方向、unknown unicast、广播/MDB、VLAN/hairpin，
并对比无关出口 MAC 增量、队列积压及目标吞吐。本次没有设备访问、负载、配置修改、CI 或刷写。
