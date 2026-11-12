# RC01 Viking 上游追踪

固定日期：2026-11-13。本地源码 `6ab2fe55f46756bca369c8dcde34351ba1531b98`；Viking `owrt` HEAD 为 `0b433e656fd6886aa2f32f923c8d2d8dfc94835e`。这是已读取的固定快照，不表示之后没有更新。当前交付/CI 见 [统一进度](rc01-progress.md)，实际来源范围见 [来源 JSON](../scripts/rc01-platform-sources.json)。

## 历史距离与选择性移植

GitHub compare 的 base 为 `VIKINGYFY:owrt`、head 为 `rc01/custom`：ahead 76、behind 330，共同祖先 `e5b0e82156513e81b6b712e4498ab15fd3a28571`（11 月 2 日）。330 的时间段祖先计数为：

- 307：`e5b0e82156513e81b6b712e4498ab15fd3a28571..7b3b0f0fce341d6a92a0ad9b1b3d37e3fe384f8b`。
- 18：`7b3b0f0fce341d6a92a0ad9b1b3d37e3fe384f8b..dd8de87169f1c1d091a81eb237341b2de09bd5bf`。
- 5：`dd8de87169f1c1d091a81eb237341b2de09bd5bf..0b433e656fd6886aa2f32f923c8d2d8dfc94835e`。

这些数字包含 merge 等历史，不等于全量审核数量，也不代表缺少 330 项功能。来源 JSON 的 previous_selected_commits/selected_commits 合计登记 32 个 unique upstream commit，全部不是本地 HEAD 的祖先，但已有选择性移植。`integration=selected-commits`、`full_merge=false`；选中一个提交不表示该提交所有语义均已搬入。JSON 的 reviewed_head 保留其原有范围，本次文档追踪到新 HEAD 不修改该定义或扩大审核承诺。

已完成 UP01–UP06 开发级交付，分别见 [PPE block/drain](rc01-modules/upstream-01-ppe-block-drain.md)、[bridge scope](rc01-modules/upstream-02-bridge-scope.md)、[PPE resources](rc01-modules/upstream-03-ppe-resources.md)、[route/neighbour](rc01-modules/upstream-04-route-neighbour.md)、[firewall restart](rc01-modules/upstream-05-firewall-restart.md)、[tunnel RTNL](rc01-modules/upstream-06-tunnel-rtnl.md)。本地 769 flow-route patch 与上述最新上游逐字节相同；此结论只覆盖该补丁，不推广为整个上游已合入。

## 新增五个提交的适用性

| 提交 | 内容 | 当前 RC01 结论 |
| --- | --- | --- |
| `104c3d7738` | README | 文档更新，不作为新增运行能力 |
| `99aec6efe1` | merge | 历史合并节点，不按节点数推断功能缺失 |
| `a066473cf38e3b3f91b523065c6f14f0dbf6742e` | WSI PCI 端点检查 | 本地 prepared ath12k HW1 提前转入 invalid_group 单设备组路径，绕过 get_wsi_info，当前收益低；本地并未因此拥有上游端点检查 |
| `02cfff9116feb30be608b116f8710c0efb9381e3` | AX NSS 按物理内存预算 | 不适用当前 RC01 ath12k/PPE 路线 |
| `0b433e656fd6886aa2f32f923c8d2d8dfc94835e` | ath11k 监管带宽/无可用信道处理 | 不适用当前 RC01 ath12k/PPE 路线 |

以上是限定调用路径与平台适用性结论，不是整组运行或实机验证；本次没有移植这五个提交。

## 保留候选、待办与验收

原匿名开发资料 `src/viking/docs/reviews/viking-other-upstream-summary-2026-11-12.md` 的研究固定本地 `ccb3270cdc1040abcbf960f92e5490c90ded7b6e`、上游 `dd8de87169f1c1d091a81eb237341b2de09bd5bf`。下面选择性保留公开开发结论，供本树独立阅读；原报告和模块历史不回写成当前验收。本次没有实施这些候选。

| 候选/问题 | 现状与下一步 | 完成标准 |
| --- | --- | --- |
| hostapd 中心频率 | 优先评估小范围 20 MHz 旧中心清理、6 GHz 40 MHz 偏移；不要求整包升级 | 固定来源并复核本地 C 接口，覆盖宽转窄、偏移、reload/失败回退；开发回归、目标构建及实机切信道分开记录 |
| radio 默认带宽 | 未实施；复核逐 radio 频率能力及 defaults_done，保留缺子 radio 时等待和自动最高 160 MHz 边界 | 说明新初始化与已初始化 AP 的影响；覆盖缺失/受限能力输入，不能无意引入自动 320 |
| 6 GHz 能力声明 | 未实施；模式与最大宽度需一致，声明修正不等于硬件支持 | 校验能力组合及非 6 GHz 限制；自动 EHT320 另需真实能力与完整默认值/C helper 闭环（主信道 >221 拒绝、末重叠中心 223→191） |
| ath12k 全组重组 | 高成本，保留当前选集；新分组会碰到本地 914 借用计数、920 生命周期及旧 317 适配 | 如另行选择实施，先证明本地修复保持，覆盖生命周期和内存边界，再做模块构建/回归；文件名变化不作为新增收益 |
| Rust 构建支持与 feed metadata errors | Rust 支持在旧 330 范围中；CI 有部分 Rust feed metadata errors，相关性和必要性待核实 | 记录具体错误、feed SHA、选包/依赖路径及所需 Rust 版本，复现并判断是否影响本次目标；未证实前不声称上游 Rust 缺失导致 CI 失败 |
| WSI 端点检查 | 当前 HW1 路径收益低，保留观察 | 若硬件/驱动路径变化，重新核对 invalid_group/get_wsi_info 可达性及缺端点场景；不将提前绕过写成已具备检查 |

其余旧候选包括补丁分组整理、按需 PPE/EDMA 文本诊断、未使用 host-only low-memory override 清理、SCS 重组和 hostapd 整包升级，均非已实施新增功能。历史证据只证明 EDMA/DS 16 个限定展开文件相同、SCS 的 scs.c 相同，不能推广为整个 PPE/SCS 等价。诊断若进入普通轮询须有容量/时间边界；AX/ath11k/NSS、IPQ5332 AHB 专有修改不并入当前 RC01 路线。

已撤回“上游放宽本地固件精确门槛”的早期判断：当时两端均为固件版本精确比较、build ID 子串匹配，未因此修改无线代码。不要把旧推断重新列为已证实缺陷。

后续更新先固定两端 SHA、核对来源定义和实际补丁/调用链，再按收益与适用性选择范围。研究清单不自动授权移植；硬件、目标构建及完整 CI 仍各自验收。辅助原始清单曾位于 `/tmp/viking-upstream-330.txt`、`/tmp/viking-upstream-first-parent.txt`；固定范围、关键结论和待办已保存在本页，不依赖临时文件长期存在。
