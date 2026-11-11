# 模块 07：风扇真实保护原因／失败计数导出：开发交接

状态：开发完成，等待 Astra 独立验收。**没有构建镜像，没有实机验证。** 离线测试不能替代目标 procd、BusyBox 和真实 EMC2301 上的验证。

- 基线：`a9ac7c6e3841b795e0b292232b308d72dea98929`（模块 01—05 已集成）
- 分支：`dev/module-07-fan-reasons`（宿主绑定的独立 worktree）
- 提交：本文和实现在同一次本地提交中提交。文件无法写入自己所在提交的 SHA，SHA 见交接报告和 `git log`。
- 方案依据：`docs/plans/07-fan-reasons-astra.md`（外层只读）。没有读取、修改或引入模块 06 的树和接口。

## 采用方案

按 Astra 的最小方案实现，没有扩展：

- **guard 是唯一生产者。** 在 `rc01_fan_sample` 每个原有的失败分支内，只增加原因变量的赋值，读取顺序和返回值保持不变。主流程在原有的计数／锁存分支中记录首次锁存原因。每一步保护动作（PWM 写入和原日志）完成后，才调用 `rc01_fan_report`。
- **一次最多一个后台发布，不排队，不等待。** `rc01_fan_report` 在父进程中只用 builtin 读取 `/proc/uptime`。如果上一个发布子进程还在运行（`kill -0`），本次直接丢弃。否则 fork 一个 `( trap - INT TERM EXIT; rc01_fan_write … ) &`，父进程只记录 `$!`。子进程把内容写到同目录的 `<state>.<guard pid>`，再 `mv -f` 原子替换；失败时 `rm -f` 并静默退出。子进程不采样，不写 PWM。
- **`wait` 释放已结束的作业（实现中发现的问题）。** BusyBox ash 的非交互 shell 会把已结束、未被 `wait` 的后台作业一直留在作业表中（`makejob` 只复用 `waited` 的项）。实测每次发布约泄漏 44 B，按每 2 秒一次计算约 1.9 MB/天，`makejob` 的线性扫描也会越来越慢。现在在 `kill -0` 确认上一子进程已被回收后，对其 PID 执行 `wait`。在 1.38.0 源码中，`dowait()` 对已不是 `JOBRUNNING` 的作业直接走 `DOWAIT_NONBLOCK`，因此不会阻塞（见 `shell/ash.c` `dowait`/`waitcmd`），并有回归测试覆盖。
- **身份。** 启动时读取一次 `boot_id`，以及自身 `/proc/$$/stat` 的第 22 字段（starttime）。任何一项读取失败都只跳过发布，不影响保护。
- `failures`/`latched` 由 `local` 改为脚本全局变量，名称和取值不变。这样在 `rc01_fan_main` 返回后，EXIT trap 仍能报告终态（例如锁存后 PWM 写失败时的 `latched=1, failures=3`）。
- 消费端 `platform.uc` 新增 `fan.protection`。它用 `bounded()` 读取最多 4 KiB，捕获 JSON 解析错误，按固定键和枚举校验，再对照现有进程筛选结果（已过滤守护子进程）核对实例身份和新鲜度。LuCI 只做显示。RPC 方法和 ACL 没有改动。
- `rc01-platform` 的 `PKG_RELEASE` 从 7 改为 8，沿用此前模块的惯例。

未做（按方案和用户要求）：常驻 worker、锁、IPC、历史、超时框架；复制温度／RPM／PWM／趋势；init 中删除旧文件；修改发现规则、曲线、底速、阈值、sleep、trap 或返回码。

## 状态契约

文件：`/var/run/rc01-fan-guard.json`（运行期，`RC01_FAN_STATE` 仅供测试覆盖）。内容为单行 JSON：

| 字段 | 取值与含义 |
| --- | --- |
| `schema` | `1` |
| `boot_id`、`pid`、`start_ticks` | guard 的实例身份，只用于判断新旧，不输出到页面 |
| `updated_uptime` | 本条记录生成时父进程读到的 `/proc/uptime`（单调秒） |
| `sampled_uptime` | 最近一次 guard 采样完成并生成记录时的 uptime；尚未采样时为 `null`。退出或发现失败时不刷新 |
| `phase` | `starting`（首个 255 写入之后）、`monitoring`、`exited`（尽力而为，可能丢失） |
| `event` | `startup`、`sample`、`controller-unavailable-or-ambiguous`、`pwm-write-failed`、`signal-stop`、`loop-ended` |
| `sample_reason` | `not-sampled`、`ok`、`sensor-missing`、`sensor-invalid`、`rpm-invalid`、`pwm-invalid`、`pwm-out-of-range`、`fault`、`rpm-zero`，取第一个失败的检查 |
| `failures` | 原 `failures` 的值：只统计循环中的连续失败。启动失败时仍为 0 |
| `latched` | 原 `latched` 的值，是软件决定，不代表 255 已写入成功或风扇已满速 |
| `latch_reason` | `null`；或首次锁存的原因 `startup:<reason>`、`over-temperature`、`consecutive-failures:<reason>`。进程内不再覆盖 |

`fault` 表示 `fan1_fault` 可读，但内容不是字符串 `0`（包括读取失败和格式错误）。`fan1_fault` 不存在或不可读时，原 guard 本来就跳过该检查，所以不作为失败原因。

`fan.protection`（RPC 输出）：

- `status`：
  - `current`：记录与唯一运行实例的 boot_id、PID、starttime 一致，且 `0 ≤ now - updated_uptime ≤ 10`；
  - `stale`：值保留，但只能作为“上次记录”；
  - `exited`：记录本身标明 guard 已退出；
  - `unknown`：所有字段为 null。
- `detail`：
  - unknown 时：`missing`、`unreadable`、`truncated`、`malformed`、`identity-unavailable`；
  - stale 时：`previous-boot`、`no-guard-process`、`multiple-guard-processes`、`other-instance`、`age-unknown`（时间倒退或不可读）、`expired`。
- 其余字段：`age`、`sample_age`（guard 采样距今多久，不是 RPC 时间；跨启动时为 null）、`phase`、`event`、`sample_reason`、`failures`、`latched`、`latch_reason`。

10 秒只是显示新鲜度的阈值，不是新的保护阈值。

### 兼容边界

- 顶层 `schema` 仍为 1，只新增 `fan.protection`。`trend`、`guard_process` 和原有说明文字不变。
- 新进程首次发布之前，旧文件只会显示为 stale 或 exited。旧实例延迟写入的发布即使覆盖了新文件，也会因身份不一致被判为 `other-instance`。新实例的计数和锁存从 0 开始，不继承旧值。
- 导出有损：任意一次记录（包括终态）都可能被丢弃。消费者用身份和年龄来兜底。
- 页面分别显示：守护记录状态、守护采样距今、事件、采样结果、连续失败数和软件锁存（含首次原因）。非 current 的值前面加“上次记录”；unknown 时一律显示 Unknown，不显示 0 或 false。页面还说明：锁存不能证明 PWM 已写入或风扇已满速，检查通过也不能替代硬件验收。

## 改动文件

- `package/emortal/rc01-platform/files/rc01-fan-guard`：原因变量、身份、发布函数；主流程增加报告点
- `package/emortal/rc01-platform/files/platform.uc`：`guard_record`、`fan_protection`，以及 `fan.protection`
- `package/emortal/luci-app-rc01/htdocs/.../status/rc01.js`、`po/zh_Hans/rc01.po`：显示与翻译
- `package/emortal/rc01-platform/Makefile`：`PKG_RELEASE` 8
- `tests/rc01/test_fan_guard.py`（新）、`tests/rc01/fan_trace_preload.c`（新，仅用于测试的 LD_PRELOAD 追踪器）
- `tests/rc01/test_status.py`（FanStatus）、`tests/rc01/test_status_ui.py`

## 验证

### 工具

- BusyBox：从 `https://www.busybox.net/downloads/busybox-1.38.0.tar.bz2` 自行下载。sha256 为 `34f9ea6f…c53bb2`，与本树 `package/utils/busybox/Makefile` 的 `PKG_HASH` 一致。在会话临时目录中解包，按顺序应用本树 `package/utils/busybox/patches/*.patch`（16 个全部应用成功，含 `302-ash-clear-bb_got_signal…`）。配置为 `make defconfig`，关闭 `CONFIG_TC`（主机内核头不兼容），并按 OpenWrt 默认打开 `FEATURE_PREFER_APPLETS`、`FEATURE_SH_NOFORK`（`SH_STANDALONE` 与 OpenWrt 一样保持关闭，因此外部命令仍经 PATH 解析）。实际版本：`BusyBox v1.38.0`。
- ucode：`/tmp/rc01-acceleration-astra/ucode`（公开构建，只执行）。Node v24.22.2，cc 15.2.0，Python 由 uv 管理。

### 测试设计（`test_fan_guard.py`）

- 每个用例都使用全新的临时目录，包含 sysfs、proc（`boot_id`、`uptime`、`<pid>/stat`）、状态文件和 tick 脚本。PATH 只包含桩目录：`sleep`、`logger` 是记录桩，`cat`、`readlink`、`rm`、`mv` 是指向上述 BusyBox 的链接，`mv` 可替换为慢速或失败桩。删除桩后不会回落到宿主程序（有专门用例）。guard 在 BusyBox ash 下运行。
- `fan_trace_preload.c` 按真实发生顺序记录 sysfs 读取，以及每次 PWM 写入的值和写入进程（libc 内部的 stdio 写入无法拦截，所以在最后一个指向 pwm1 的描述符关闭或被替换时读取文件内容）。断言比对完整的保护 I/O、sleep 参数、logger 和退出码，不只比较最终 PWM。非 guard 进程的 PWM 写入会单独标记为 `pwm-by-other-process`。
- **基线对照**：`git show a9ac7c6…:rc01-fan-guard` 导出到新的临时目录，同一夹具分别运行基线和新 guard，逐项比较完整事件序列（含读取顺序）和返回码。共 16 个场景加 21 种首错夹具，全部一致。场景包括：正常；启动失败；连续失败 1／2 次后成功清零；第 3 次失败锁存，恢复后不解锁、不写 77；高温 74999／75000 边界；启动时 75000 仍先写 77；高温伴随无效 RPM 计为失败；锁存后继续失败；控制器缺失或不唯一；初始 255、启动 77、锁存 255 写入失败；循环中 TERM；启动等待中 INT；锁存后 TERM。
- 首错原因（21 个夹具，在 255 写入之后的启动等待期间注入）：无 NSS；温度为坏值、缺失、999、130001；温度 1000、130000；RPM 和 PWM 同时坏（报 rpm-invalid）；RPM 缺失；PWM 坏且 0 RPM（报 pwm-invalid）；PWM 76 且 fault=1（报 out-of-range）；PWM 256、255；fault=1 且 0 RPM（报 fault）；fault 为坏值；fault 缺失（ok）；fault 缺失且 0 RPM（报 rpm-zero）；fault 不可读（跳过，ok）；第二个有效 NSS（ok）；第二个无效 NSS（报 sensor-invalid）。
- 导出隔离：
  - 慢速 `mv` 阻塞在 FIFO 上：guard 轨迹与预期完全一致，并在发布仍挂起时正常结束；整个运行只启动了 1 次 `mv`，后续发布全部被丢弃。挂起期间正式文件不存在，消费者只会看到 missing，不会读到半个文件。放行后只留下完整的 `starting` 记录，没有残留临时文件。
  - 在发布子进程挂起时向它发送 TERM 和 INT：没有出现额外的 PWM 写入。
  - 状态目录不存在、`mv` 失败、无 boot_id、uptime 损坏：三个保护场景的轨迹和返回码都不变，也没有残留文件。
  - 发布 5000 轮后 shell 的 VmData 不增长（去掉 `wait` 后增长 120 kB，用例失败）。
- 变异核对（临时修改，已恢复）：让发布子进程写 PWM 时，50 处断言失败；改为同步发布时，guard 挂在慢速 `mv` 上导致超时。
- 生产者到消费者：把真实 guard 产生的快照交给真实 `platform.uc` 读取，结果分别为 current（`failures=3`、锁存原因）、过 11 秒后 expired、终态 exited。
- `test_status.py` FanStatus 覆盖：current；子进程不算第二个实例；expired、时间倒退、starttime 或 PID 不同、跨启动、exited；无进程、多进程；stat 或 boot_id 不可读时为 unknown；缺失、截断（超过 4 KiB）、不可读、坏 JSON 和 17 种非法字段都返回 unknown 且计数为 null。
- `test_status_ui.py` 覆盖：current 采样 ok 但已锁存时仍显示锁存及首次原因；年龄使用 guard 的 `sample_age`；stale 和 exited 显示“上次记录”；unknown 或缺字段时全部显示 Unknown；进程存在但没有记录时不显示 current；原有说明保留。原有 RPC/ACL 用例未改且通过。

### 命令与结果

```sh
BB=<会话临时目录>/bin/busybox
UCODE=/tmp/rc01-acceleration-astra/ucode BUSYBOX=$BB \
  uv run --offline --no-project python -B -m unittest \
  tests.rc01.test_fan_guard tests.rc01.test_runtime tests.rc01.test_status tests.rc01.test_status_ui \
  tests.rc01.test_sfp_led tests.rc01.test_rgb tests.rc01.test_platform
# Ran 253 tests, OK (skipped=10)，连续两次
```

10 个跳过都缺少构建输入（`RC01_KERNEL_SOURCE`/`RC01_WIRELESS_SOURCE` 预备树，以及 RGB 页面校验需要的 LuCI 源码），不属于本模块。没有设置 `BUSYBOX`，或者主机上找不到 busybox 和 cc 时，`FanGuardTrace` 会整体跳过。验收时请提供 BusyBox。其余项：`busybox sh -n` 与 `dash -n` 语法通过。shellcheck 只有 SC2100（把 `x=a-b` 误判为算术），以及有意为之的 SC1007、SC2015、SC2086、SC3043；基线 guard 本来也有 SC3043。没有 msgfmt，po 只用脚本核对：新增 41 条，无重复 msgid，页面所用字符串全部有翻译。没有运行全量 discover：已知无线首启 disabled 基线失败和缺少构建输入的跳过不属于本模块。

## 未验证项

- 目标 BusyBox（OpenWrt 构建产物）和 procd 下的实际行为：procd stop／respawn 时信号只发给主进程，发布子进程的生命周期，`/var/run` 在 START=11 时已可写。
- 真实 `/proc/<pid>/stat`、`boot_id` 和 rpcd 环境中的读取与新鲜度判断。
- 实际起转、转速、温度响应、I2C/PWM 写失败以及满速效果。离线 PWM 写入只是夹具文件写入。
- 长时间运行时的 CPU 开销：每个周期多一次 fork（以及子进程中的 `mv`）。不宣称零开销或硬实时。
- LuCI 页面真实浏览器渲染与中文显示：只做了 Node 渲染树断言。
