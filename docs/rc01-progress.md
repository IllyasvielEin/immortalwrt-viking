# RC01 统一进度

记录日期：2026-11-13。当前源码基线为 `6ab2fe55f46756bca369c8dcde34351ba1531b98`，本地分支 `rc01/anonymous-rebase-20261112`。远端默认分支 `rc01/custom` 已快进到该提交；[PR #1](https://github.com/IllyasvielEin/immortalwrt-viking/pull/1) 已自动 **MERGED**。

最新 [CI 43261551327](https://github.com/IllyasvielEin/immortalwrt-viking/actions/runs/43261551327) 的 head 为 `6ab2fe55f4`；**截至 2026-11-13T01:45:03+08:00，状态为 in_progress，正在保存编译缓存收尾**。主线程已刷新核对：`Verify native driver artifacts and runtime scripts`、`Verify static boot contracts and image budget` 及 `Upload verified images and build metadata` 均为 success；整轮最终 conclusion 当时仍为空，不将步骤通过写成整轮已完成。无实机验收、无刷机。

入口：[构建与验收指南](rc01-build.md)、[上游追踪与候选](rc01-upstream-status.md)、[模块历史](rc01-modules/)、[来源定义](../scripts/rc01-platform-sources.json)。

## 整合与已交付范围

匿名开发头 `b74e6cde6f8196639805d2f309d8b3d706be3740` 基于独立 snapshot `f4d05b5f675eff4278eaadaa6b613d90c0974460`，对应原仓历史 `d1a7fdc6c41c7a1121c5ffb09e8267fc17c66067`。使用 `rebase --rebase-merges --onto <原 d1> <snapshot f4>` 保留模块结构；79 个匿名 delta 路径的 blob/mode 一致，原仓网络默认值 fixture 与 vscode 配置保留。整合源为 `a7fc1b49238f392bfdd074749e0b1680098b256c`，记录提交为 `70d5ed632d3094bd82a06eda7fb22f5920e29159`。整合时全量离线回归 620 项、42.005 秒，零失败/错误/跳过。

01–08（加速只读诊断、风扇状态、信道提示、SFP、RGB、出口连通灯、保护原因、集成）、五项简洁性改进及 UP01–UP06 已完成历史开发验收并合入；另有 Tailscale 1.104.6、Mihomo 1.19.34 来源更新。此处承接开发结果，不把历史宿主验收升级为硬件结论。各批日志与方法数不相加。原匿名外层 `src/viking/docs/DELIVERY.md`、`docs/TASKS.md`、`sources/module-progress.json` 为当时交接资料，不是本树路径或当前 CI 状态入口；必要结论已摘要到本树。

[整合记录](rc01-modules/local-rebase-integration-2026-11-12.md) 保留当时未推送、PR draft/open 的历史语境；当前远端状态以上述快进和 MERGED 为准。原仓旧 `rc01/custom` 工作树中的用户未提交构建文档未动。

## CI 故障、修复与证据

### 43219369427：构建前回归失败

[运行](https://github.com/IllyasvielEin/immortalwrt-viking/actions/runs/43219369427)，head `70d5ed632d`。失败分类为 **27 errors（风扇运行超时）和 29 failures（27 cleanup 超时 + 1 发布次数不足 + 1 netled lint）**。

Ubuntu BusyBox 1.36.1 的 `ASH_SLEEP` 内建绕过 PATH 中的 sleep 桩，导致夹具 tick/退出逻辑失效；`PREFER_APPLETS` 和 `SH_STANDALONE` 当时均关闭，mv 桩没有被绕过。不能沿用“mv 也绕过”或“28 个风扇 failure 都是 cleanup”的旧说法。发布次数当时为 910，要求大于 2000；其独立失败未复现，不能把成因直接归到 sleep。另有匿名风扇 SHA 基线在干净 CI 不可达，以及 ShellCheck 0.9.0 报两处 SC2015（本机 0.11.0 未报）。

修复 `591bbb924641d2639248d84a326f0a751c554a00`：增加专用 BusyBox-only 准备并跨构建前/后两阶段传递同一绝对路径；用实际 PATH 行为探针检验 sleep/mv；风扇基线改为可达、同字节的上述 `d1a7fdc6c4`；netled 数量校验改显式 if。生产风扇未改，发布次数及内存断言未降低。基线 guard blob 为 `cedbbcd6c30affce4d184ed0738511568f0ea695`，SHA-256 为 `f35e54f392d142445b8b3e932d368c2ba8b562a2e4ba16068406863d0f2473e3`。

修复后本地完整输入：620 项、42.515 秒，零失败/错误/跳过；干净构建前输入：`Ran 597`、29.424 秒，`OK (skipped=289)`，并非 597 个实际执行成功再加 289。原失败运行有 290 skips，其中包含旧基线不可达；修复后构建前为 289，最终必须为 0。class-level skip 会影响 testsRun，不能单凭 597/620 差额判断用例丢失。原诊断方案的“尚待实现”已由后续提交及上述验证更新；发布次数原失败的具体成因仍不据此声称已证明。

### 43240944194：编译成功，最终原生回归失败

[运行](https://github.com/IllyasvielEin/immortalwrt-viking/actions/runs/43240944194)，head `591bbb9246`。编译成功，但 final native driver/runtime 回归 **620 项、71.259 秒，2 failures、0 errors、0 skips**，整轮未通过。

- 当次 LuCI `ad75cde88e694b511100576bfbe0a280e5efe6ef` 新调用需要夹具提供 `setInvalid` 与 `ui.tabs.updateTabs`。
- dtc 1.7.0 将 pins 打印为 `"gpio46\0gpio64"`，1.7.2 则打印为分开的字符串；原字符串故障注入未命中，造成负例失效。

`6ab2fe55f4` 仅改 `tests/rc01/netled-form-check.js` 和 `tests/rc01/test_rgb.py`，10 增 3 删，生产代码未改。本地用 CI 实际工具输入回归 **620 项、42.478 秒成功，零跳过**；旧工具定向 3 项成功。这是修复的本地证据，不是新 CI 已通过。

### 最新重跑与产物边界

43261551327 重放 43240944194 的软件来源锁，参数为 `software_updates=latest`、`include_dae=false`、`go_bootstrap=external`、`cold_cache=false`。状态截止时间见页首。

上一轮 43240944194 的保留期限（到期或删除后须重新确认可用性）：

| artifact | ID | 到期 UTC |
| --- | --- | --- |
| 镜像（未验收） | 13962308633 | 2026-11-19T16:31:35Z |
| packages | 13962313519 | 2026-11-26T16:31:42Z |
| 来源 lock | 13954264750 | 2027-02-10T14:48:00Z |

现有 [verify-rc01.yml](../.github/workflows/verify-rc01.yml) 要求源运行 native step success，上一轮不满足，不能将其镜像标为 verified。完整 build_dir 未归档，因此现有正式 CI 仍需重编。上一轮编译 92m43s，宿主 BusyBox 约 20s；首次两个缓存未命中，ccache 命中率 10.61%。轮末约 1.25GB dl / 255MB ccache 成功保存，主线程已通过 API 确认存在；这不保证未来运行必然命中。

关键诊断已持久保存在本页。辅助原始证据：本树 `build_dir/rc01-ci-fix-20261112/`（full/clean/negative 日志）、`build_dir/rc01-ci-fixtures-20261113/full-fixed.log`；临时 `/tmp/viking-ci-astra-plan.md`、`/tmp/viking-ci-43219369427.log`、`/tmp/viking-ci-43240944194-logs.zip`、`/tmp/viking-ci-followup-native.log`。生成日志不提交，临时文件消失不改变本页所记阶段与结论。

## 待办与完成标准

1. 读取最新 CI 的最终结论，更新观察时间、head、步骤和产物身份；只有最终 native 零失败/错误/跳过且全部既有验收步骤通过，才记录完整 CI 成功。若失败，先按实际工具/输入与失败边界诊断。
2. CI 成功后核对镜像、校验和、manifest、配置、来源锁及同批 APK；沿用构建指南的产物门槛，不把上一轮未验收镜像当成合格交付。
3. 按 [上游待办](rc01-upstream-status.md) 核实候选适用性和 Rust metadata 错误关联；本次不自动移植。
4. 实机验收仍缺启动、布局迁移/恢复、ECC/坏块、ENV、升级/回厂、风扇保护、RGB、SFP、无线及代理共存等结果；按独立授权和既有安全条件推进，不以宿主或 CI 结果代替。
