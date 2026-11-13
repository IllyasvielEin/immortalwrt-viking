# RC01 千兆接收故障

记录时间：2026-11-13，现场固件 `6ab2fe55f4` / Linux 6.18.60。

## 结论

已定位到PCS模式状态与Common Clock Framework（CCF）缓存更新顺序错误。QXGMII硬件配置完成后，驱动先调用 `clk_set_rate()`，随后才提交 `qpcs->interface`；时钟provider的 `recalc_rate()` 却依赖这个字段。因此CCF在重算时仍读到旧模式，将PCS输出父时钟缓存为125 MHz，而该模式应为312.5 MHz。

NSSCC为125 MHz端口目标提供÷2.5和÷1两种配置，按缓存父频率选择最接近的结果。错误的125 MHz缓存使它选中÷1；对应QXGMII模式的312.5 MHz输出则需要÷2.5。2.5G目标的÷4配置是单一候选，解释了2.5G仍可收发、但框架把其端口频率报告为31.25 MHz的现象。

源码缺陷、现场缓存/分频值和同端口速率对照相互吻合。源码修复已实现并通过下述本地验证，设备尚未升级；最终因果验收仍需修复固件通过同组实机对照。这里没有测量外部物理时钟波形。

## 已完成的实机对照

保持GS7对端、网线、RC01 lan2和自协商不变，仅临时关闭 `2500baseT/Full` 广告能力。设备自身脚本采样并恢复，另有独立35秒恢复进程；脱离SSH的执行能力已先用无网络变更探针验证。

- 基线2.5G：正常收发，rx_crc_errors为0。
- 协商1G后：正常接收计数停在7784；累计新增18个CRC类错误；限定探测期间新增16个错误，正常接收增量为0，ping失败。
- 恢复2.5G：通信恢复，原有广告速率、Pause、自协商及master/slave状态均与基线一致；最终3次ping全部收到回复。
- boot_id未变。未重启、刷写或保存网络配置。主脚本确认恢复后取消独立恢复操作，守护进程已结束。

因此故障也可在GS7对端复现，不能继续仅归因于win2的Realtek网卡。此前同线同网卡在GS7千兆正常，win2直连RC01 LAN1和LAN3均失败。

## 时钟与寄存器证据

- 所有速率阶段，`7a00000.ethernet-pcs::rx_clk/tx_clk` 的CCF缓存均为125 MHz。
- 2.5G阶段NSS port2 RX/TX报告31.25 MHz；1G阶段报告125 MHz；恢复后回到31.25 MHz。
- 通过regmap debugfs仅读取已由源码定位的NSSCC CFG寄存器：LAN1/3最后保留的RX/TX值分别为 `0x201/0x301`，对应UNIPHY0输入、÷1；恢复后的LAN2为 `0x207/0x307`，对应同一输入、÷4。
- LAN1/3读取时已拔线，故不把它们的寄存器快照冒充本轮LAN2千兆阶段的直接寄存器读数。
- netdev的rx_errors/rx_dropped共享MAC MIB错误汇总，不能把两者同增当作两份独立证据。rx_crc_errors还可能汇总fragment；本次依据是时钟链、分频选择和受控速率复现的组合。

## 引入位置与调用链

引入提交：`1e1d04288ea3d35449c9ff9da6b893e32d75c9c9`，2026-11-05，提交说明包含“Commit PCS cached state only after success”。该改动将 `qpcs->interface = interface` 从父时钟更新前移动至函数成功末尾。它早于本轮匿名开发整合，也不由本次分区迁移产生。

- [PCS补丁](../target/linux/qualcommbe/patches-6.18/0313-net-pcs-ipq9574-modes-and-bringup.patch)：`ipq_pcs_config_mode()` 的状态提交顺序，以及依赖该状态的 `ipq_pcs_clk_rate_get()` / RX/TX `recalc_rate()`。
- 原生内核 `drivers/clk/clk.c`：`clk_change_rate()` 调用provider重算，并将结果保存到 `core->rate`；该provider没有设置无缓存标志。
- 原生内核 `drivers/clk/qcom/clk-rcg2.c`：`__clk_rcg2_select_conf()` 读取缓存父频率，选择分频候选。
- 原生内核 `drivers/clk/qcom/nsscc-ipq9574.c`：125 MHz目标有÷2.5/÷1候选，78.125 MHz目标使用固定÷4。

内核调用链已在现有prepared源码中核对；生成目录不是独立clone的必需入口。

## 已实现修复与同类审查

将“成功完成的接口模式”与“已经配置的PCS输出时钟状态”分开管理。校准完成后先发布独立的 `clock_rate`，再通知CCF重算RX/TX缓存；完整模式依旧在全部步骤成功后提交。后续步骤失败时保留实际已配置的输出频率，不伪造硬件回滚。

对PCS模式配置、USXGMII/QXGMII共享初始化及MII时钟启停的审查还确认并修复：

- 开始重配置时使旧模式及所有通道初始化标记失效，避免失败后请求旧模式却跳过实际重配。
- 共享XPCS初始化失败时使模式缓存失效，重试重新执行未完成步骤；已完成共享设置后的通道操作仍保留各自重试路径。
- 六处复位操作检查并传播错误，失败时不提交成功模式。
- RX/TX时钟以一对资源维护所有权；TX启用失败撤销RX引用，重复启用/禁用和配置重试保持引用平衡。

限定审查也覆盖QCA8084 PHY/SerDes、NSSCC分频选择及PPE速率设置。未发现第二处已证实的同类“晚提交接口字段导致CCF旧缓存”缺陷；QCA8084 SerDes速率回调仍有未检查的 `clk_set_rate()` / `clk_prepare_enable()` 返回值，PHY的IPG设置也未传播错误。这些是待做独立故障注入的错误处理缺口，不能据此声称它们已在本机触发；本次未改变其速率切换策略。

## 验证结果与剩余验收

- 新增10项宿主回归，提取并编译实际PCS函数，模拟CCF缓存语义及寄存器、复位和时钟失败；覆盖21个QXGMII与22个USXGMII故障位置及失败重试。
- 原始驱动在六组场景中均被拒绝；另有四个缺陷回注用例，验证晚发布父频率、旧模式缓存、共享初始化缓存和RX引用泄漏确实会被检测。
- 重新准备当前补丁对应的Linux/backports、固定LuCI、BusyBox、ucode、firewall4和dnsmasq输入后，完整630项回归通过，42.295秒，零失败、零错误、零跳过。
- 修改后的完整驱动使用Linux 6.18.60真实头文件完成x86-64 COMPILE_TEST对象编译。ARM64目标及最终镜像由远端完整CI验证；宿主模型不覆盖模拟电路、真实MDIO时序或完整CCF并发。

远端构建后须进行同GS7/同口/同线的2.5G→1G→2.5G对照，核对父时钟缓存、千兆分频、CRC增量和数据收发，并复验win2直连及冷启动。现有完整CI不能替代此次缺失的实机千兆验收。

当前源码已含QCA8084 QXGMII关闭EEE补丁；运行时 `ethtool --show-eee` 不受支持。不要把重复添加该补丁当成本次修复。

原始设备身份、日志、测试脚本与校验清单保留于工作区private材料；本文仅保存脱敏结论。
