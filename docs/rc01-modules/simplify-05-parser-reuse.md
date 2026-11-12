# 简化 S02（排序第5项）：PPE 解析与同次 collect 的 NSS 温度复用

基线 `0d5f030c54b282da38f61dd7f2d0a62b165952af`（S04/S03/S01 合入后）；分支 `dev/simplify-01-test-shells`，在其上追加提交。
未构建固件，未连接设备，也未在实机验证。等待 Astra 独立验收，本分支不自行合入。不依赖另一 worktree 的 S05。

## 改动

- `package/emortal/rc01-platform/files/platform.uc`
  - `ppe_state`：对已读的 `flows.data` 只调用一次 `ppe_counts(trim(...))`，存为局部 `counts`，
    `active_flows` 和 `active_flows_source.status` 共用它。sysfs/debugfs 读取次数不变（本项不减少读取）；
    接口字段、大整数字符串、`not-collected` 语义均不变。
  - `assess`：温度不再对 `state.temperatures` 重新 filter，改为读同次 `collect` 已算好的
    `state.fan.temperature.celsius`。`nss_temperature()` 只在恰好一个 `nss-top-thermal`/`nss_top_thermal`
    传感器时保留其 celsius，否则为 null，所以与原来的 `length(sensors) == 1 ? sensors[0].celsius : null` 相同。
    阈值表达式原样保留：null、<1、≥75 为 error，≥60 为 warn。没有改为只看 `status == 'ok'`，
    因此 75..130 仍然是 error。
- `package/emortal/rc01-platform/Makefile`：`PKG_RELEASE` 10 → 11。

## 保留边界

- `assess` 的 fan 检查原样保留正向表达式（`fan != null && fan.rpm > 0 && fan.pwm != null && fan.pwm >= 77 &&
  fan.pwm <= 255 && (!fan.fault_available || fan.fault === 0)`），没有改用 `fan.health`。Astra 给出的反例
  `9223372036854775808` 经 `number()` 变为 NaN 时 `fan_health` 为 ok，而原表达式拒绝；下方对照表复现了这一点，并且新旧结果相同。
  这只说明两种判断不等价，不证明硬件故障；本项没有增加数值防御。
- NSS 复用只适用于生产 `collect` 产生、未被修改的 state（`snapshot` 和 `test_status` 都是这样调用的）。
  手工构造的旧 state 如果缺少 `fan.temperature` 会报错，这里没有加兼容回退。
- 未改动风扇 guard、曲线、保护原因、板型规则、wireless、网络、布局、ENV/FIT、身份。

## 测试（`tests/rc01/test_status.py`）

复用现有用例：`test_missing_or_hot_sensor_fails`（invalid、76 C）、`test_temperature_and_controller_unavailability_are_distinct`
（invalid/out-of-range/ambiguous/missing 状态）、PPE missing/legacy/malformed/large 用例，以及 fan health 用例。

现有用例没有覆盖 preflight 的 60/75 档位，因此在 `FanStatus` 中新增 1 个方法（只运行一次，不被其他子类继承）：
`test_preflight_temperature_thresholds_for_every_target`。它通过生产 `collect` 夹具构造温度，对 hardware/nikki/dae/daed
四个 target 检查 temperature 级别。覆盖 invalid、0、0.999、1、59.999、60、74.999、75、130、130.001、150、150.001 C，
以及 ambiguous（两个 NSS 名称）和 missing（无 thermal）。

## 验证（实际执行）

环境：`build_dir/rc01-verify-inputs/env.sh` 中的固定公开输入（host ucode 等），`uv run --offline --no-project`，
CPython 3.14.9，使用本轮专用的 uv cache。夹具都是每个测试新建的临时 root。

- `test_status`、`test_status_ui`、`test_netled_status`、`test_fan_guard`、`test_layout`、`test_runtime`：230 项 OK，0 skip。
- `test_platform`、`test_nand`：16 项 OK。
- 新用例和 `test_missing_or_hot_sensor_fails` 在旧 `platform.uc`（基线副本）上也通过，说明它们表达的是原有语义。
  临时副本变异反证：把 75 改成 `> 75` 时 1 个子用例失败；改成"只看 status==ok，否则放行"时 8 个子用例失败。
- 新旧对照与计数插桩（只对临时副本）：把 `fs` 换成计数包装模块，统计路径含 `/ppe` 的 fs 调用次数，并给 `ppe_counts` 加计数。
  每个夹具在新旧两份副本上各跑一次 collect，加上四个 target 的 assess（state 中排除了 trend 和 unix 时间）：

| 夹具 | state+preflight 相同 | ppe fs 调用 旧/新 | ppe_counts 旧/新 |
| --- | --- | ---: | ---: |
| healthy、legacy `15`、malformed `4097/4096`、empty、大整数 ipv4 | 是 | 5 / 5 | 2 / 1 |
| active_flows 缺失 | 是 | 4 / 4 | 0 / 0 |
| 超过 256 字节（truncated）、chmod 0（unreadable） | 是 | 5 / 5 | 0 / 0 |
| 温度 60、75、200（invalid）、ambiguous | 是 | 5 / 5 | 2 / 1 |
| rpm 或 pwm 为 `9223372036854775808` | 是；fan=error，health=ok | 5 / 5 | 2 / 1 |

  大整数 ipv4 夹具显示 `active_flows.ipv4` 为 NaN。这是原有的 `number()` 行为，新旧相同，本项不修改。
- `git diff --check` 通过。未运行完整的 566 项回归，也未做目标构建。

## 未验证

- 实际 PPE debugfs、thermal 驱动、目标 ucode 版本、镜像中 rc01-check/LuCI 的调用都没有在目标或实机上验证。
