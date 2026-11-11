# 模块 05：独立 RGB 灯条 — 开发交接

状态：开发完成，离线验证通过；**未构建镜像、未实机验证**。待 Astra 验收。

- 基线：`0aefaee18551938f58e44e8627d3cdcb2e1a42f9`（模块 01—04 已验收；0aefaee 仅更正 SFP 测试夹具）
- 分支：`dev/module-05-rgb`
- 目标提交：见本文末尾与 `git log`（本文随该提交一起提交，SHA 无法写入自身，由交接报告给出）

## 公开来源与许可

| 项 | 内容 |
|---|---|
| 参考仓库 | <https://github.com/zh8416/immortalwrt>（fork 自 VIKINGYFY/immortalwrt） |
| 固定提交 | `7c3057979a9e1767fed5e12401ae7d1948223f16`（该提交本身只删除 `sbin/tempinfo`，RGB 文件内容取该提交树） |
| 包 | `package/be10000-fan-rgb`，`PKG_LICENSE:=GPL-2.0-or-later`，`PKG_MAINTAINER:=zh8416` |
| 吸收的 RGB 文件 | `files/usr/libexec/be10000-rgb.uc`（效果集、配色映射、选项名、日程语义）、`files/etc/config/be10000_rgb`（选项与默认值参考）、`files/www/.../system/be10000-rgb.js`（界面结构）、`ipq9570-be10000.dts` 中 `leds-rgb`/`&pwm`/`pwm_pins`（引脚、通道、周期） |
| 未带入 | `be10000-fan.uc`、`be10000-fan`/`be10000-fan-prepare` init、`fancontrol` 配置与 LuCI、`sbin/faninfo`、uci-defaults 首启脚本（它会 disable 其他服务）、`usr/sbin/be10000-rgb` CLI、ucitrack、对 `rc init` 的 ACL 写权限、`coreutils-timeout`/`rpcd-mod-file`/`luci-base` 等核心包依赖 |

新文件均为 `GPL-2.0-or-later`（与参考包一致），文件头与 Makefile 注明来源仓库、提交和作者。仓库层面 GitHub 许可显示 NOASSERTION，包级许可以参考 Makefile 声明为准。

## 板级设备树（`target/linux/qualcommbe/dts/ipq9570-be10000.dts`）

- 新增 `rgb-leds`（`pwm-leds`）：红 `&pwm 3`、绿 `&pwm 2`、蓝 `&pwm 1`，周期 51000 ns，极性 normal，`max-brightness = <255>`，`default-state = "off"`。命名用 `color` + `function = "ambient"`，内核名为 `red:ambient`/`green:ambient`/`blue:ambient`（与本 DTS 已有 `function = "network"/"sfp"` 风格一致）。
- `&pwm` 启用，`pinctrl-0 = <&pwm_pins>`；`&tlmm` 新增 `pwm_pins: pwm-state`（gpio31/32/33，function `pwm`，8 mA，bias-disable）。
- 参考 DTS 用 `label` + `default-state = "keep"`；这里改为 `off`，避免未验证灯效在启动时直接点亮。

核对结果（当前源码与最终 DTB）：

- 上游 `pinctrl-ipq9574.c`（gregkh/linux `v6.18.60`）中 GPIO31/32/33 均有 `pwm` 复用（`PINGROUP(31, pcie3_clk, pwm, …)` 等）。
- 这三脚在通用 dtsi 中只出现在 `/omit-if-no-ref/ pcie3_default`；RC01 的 pcie3 用 `pcie3_reset_default`（GPIO23），最终 DTB 中 GPIO31—33 除 `pwm-state` 外无任何 `pins`/`*-gpios` 引用。
- PWM 节点来自 `0402-arm64-dts-qcom-ipq9574-add-pwm-node.patch`（100 MHz，`#pwm-cells = <3>`，默认 disabled）；驱动 `802-...ipq6018-pwm` 为 4 通道、仅 normal 极性；`CONFIG_PWM_IPQ=y`、`CONFIG_LEDS_PWM=y` 内建，无需额外 kmod。
- 按 `830-pwm-ipq-fix-period-calculation.patch` 的算法推演：51000 ns @100 MHz 精确得到 `pre_div=0, pwm_div=5099`，0—255 每级亮度对应不同高电平时间（测试中建模）。
- 风扇仍在 EMC2301（I2C 0x2f）上，与 SoC PWM 无关；系统、network、SFP、网口灯节点未改。

**公开参考不等于电气实测**：通道与颜色对应、极性、亮度线性、是否需要其它供电/使能均未在 RC01 上测量。

## 软件包

### `package/emortal/rc01-rgb`

| 安装路径 | 作用 |
|---|---|
| `/etc/config/rc01_rgb`（conffile） | 单一 `config rgb 'main'`；默认 `enabled '0'`、`mode 'static'` |
| `/etc/init.d/rc01-rgb` | procd 服务，START=97（在 `/etc/init.d/led` 96 之后），STOP=10 |
| `/usr/libexec/rc01-rgb` | ucode 守护循环（无 uloop，`sleep()` + `signal()`） |
| `/usr/share/ucode/rc01/rgb.uc` | 配置规整、日程、帧计算、数据源读取、LED 写入 |

依赖：`@TARGET_qualcommbe_ipq95xx +ucode +ucode-mod-fs +ucode-mod-uci`。已加入 `xiaomi_be10000` 的 `DEVICE_PACKAGES`、`defconfig/qualcommbe-rc01.config` 与 `scripts/validate-rc01-config.sh` 必选项。

### `package/emortal/luci-app-rc01-rgb`

- 视图 `system/rc01-rgb`（菜单：系统 → RGB 灯光），中文翻译 `po/zh_Hans/rc01-rgb.po`（生成 `luci-i18n-rc01-rgb-zh-cn`）。
- ACL `luci-app-rc01-rgb`：写仅 `uci: rc01_rgb`；读 `uci: rc01_rgb`、`ubus rc list`、文件 `/var/run/rc01-rgb.status`。不授予 `rc init`、`file exec/write` 或任何其他配置写权限；现有 `luci-app-rc01` 只读状态 RPC 未改动。
- 保存并应用后由 procd 配置触发重启服务，无需页面调用 init。

## 行为定义

### 选项

`enabled`、`mode`（static/breathe/duotone/rainbow/temperature/cpu/traffic）、`color`、`second_color`（`#RRGGBB`）、`brightness`/`minimum_brightness`/`night_brightness`（0—255）、`speed`/`sensitivity`（1—10）、`traffic_interface`、`schedule_enabled`/`on_time`/`off_time`、`night_enabled`/`night_start`/`night_end`（`HH:MM`）。

未设或空值取包默认值；非法值逐项回退默认并记入状态 `ignored_options` 和 syslog，不会整体拒绝启动。LuCI 端同样校验格式与范围。

### 日程 / 夜间优先级

1. `enabled=0` 或 `system.@system[-1].leds_off=1`：不启动实例；灯条保持关闭（DTS 默认 off，停止时清零）。
2. `schedule_enabled=1` 且当前不在 `[on_time, off_time)`：灭（`period: schedule-off`）。定时优先于夜间。
3. 在亮灯时段内且 `night_enabled=1` 且处于 `[night_start, night_end)`：最高亮度取 `min(brightness, night_brightness)`，夜间只降不升（`period: night`）。
4. 否则使用 `brightness`。

时段可跨午夜；起止相同表示全天。使用路由器本地时间与 `/etc/TZ`；NTP 同步前的时钟偏差会影响日程（未额外处理）。

### 模式与数据回退

- static：主色 × 亮度。breathe：主色在 `minimum_brightness`（不超过当前上限）与上限间三角波，speed 5 为 10 s 周期。duotone：主色↔第二色。rainbow：六段色轮。动画相位按单调时钟计算。
- temperature：读取与 RC01 状态页/风扇曲线相同的 `nss-top-thermal` 区域，有效范围 1—130 °C，配色沿用参考（≤35 绿 → 55 橙 → ≥75 红，sensitivity 偏移 ±2 °C/级）。
- cpu：`/proc/stat` 总行差分（需两次采样）。traffic：`network.<接口>.device` 解析出的设备（无则把值当设备名，如 `pppoe-wan`），rx+tx 字节差分，满量程 12.5 MB/s ÷ sensitivity。
- 数据缺失、非法、首个采样或计数回绕：显示**主颜色**（按当前亮度），状态 `fallback: true`、`detail` 为 `source unavailable` 或 `sampling`。不显示为 0 或伪造数值。

### 资源与频率

动画 10 Hz（100 ms），其余（静态、数据模式、定时关闭）1 Hz；只写变化的通道，静态模式稳定后不再写 sysfs。状态文件 `/var/run/rc01-rgb.status`（原子替换）只在模式/时段/数据描述/写失败变化时更新，动画帧不写入。配置仅在启动时读取。

### 生命周期与 LED 所有权

- 启动：三颗 LED 任一缺失则 init 记日志不启动；守护进程自身也检查，缺失即退出 1 且不写任何 LED。启动后把三颗 RGB LED 的 trigger 设为 `none`，此后服务独占这三颗；不读写任何其他 LED、hwmon 或风扇节点。
- 配置变更：`procd_set_param file /etc/config/rc01_rgb` 使配置文件变化时重启实例；`procd_add_reload_trigger rc01_rgb system` 处理启用/禁用与 `leds_off`。禁用时 reload 不再声明实例，procd 停止旧实例。
- 停止：SIGTERM/SIGINT/SIGHUP → 退出循环 → 三色写 0、删除状态文件、退出 0。循环中出现未预期异常同样先关灯再退出 1（procd respawn 3600/5/5）。`service_stopped` 再兜底清零（只写三颗 RGB）。
- 运行中 LED 写失败：记一次日志、状态 `led_write_failed: true`，失败通道不缓存、下一帧重试；恢复后状态回到 false。
- 若用户在 `/etc/config/system` 为 `*:ambient` 另配 trigger：RGB 启用时以本服务为准（启动时清 trigger）；RGB 禁用时不触碰。

## 验证（实际执行结果）

环境：WSL2 Linux；ucode 使用公开宿主 `/tmp/rc01-acceleration-astra/ucode`（与树内 `package/utils/ucode` 同一提交 `dfc64d41fd720163ac21d9feb9cb224d1a80fd75`）；临时文件均在会话 scratchpad 和测试自建临时目录，未触及宿主 LED/PWM/syslog。

1. DT 输入：稀疏克隆 `gregkh/linux` `v6.18.60`（`f45ed76d6791cf72ff1265ee93511175bbbc2f18`）的 `arch/arm64/boot/dts/qcom`、`include/dt-bindings`、`include/uapi/linux`、`scripts/dtc/include-prefixes`，按 generic backport/pending/hack 与 qualcommbe 补丁顺序 `git apply --include` 仅应用 DT 相关部分：29 个补丁全部成功。
2. DTB：基线与新 DTS 均可用 cpp+dtc 编译；新 DTB 与基线相比 **无新增 dtc 警告**。`scripts/validate-rc01-platform.py` 的 `compile_dtb()` 编出的 DTB 通过 `verify_thermal_dtb`、`verify_ppe_dtb` 与扩展后的 `verify_platform_dtb`（新增 RGB 检查）；基线 DTB 在新检查下按预期失败（pwm disabled）。
3. RGB 测试：
   ```sh
   UCODE=/tmp/rc01-acceleration-astra/ucode RC01_KERNEL_SOURCE=<上述稀疏内核树> \
     uv run --offline --no-project python -B -m unittest -v tests.rc01.test_rgb
   ```
   结果：`Ran 37 tests … OK`，无跳过。覆盖：默认值与 shipped 配置一致且默认关闭；非法输入逐项回退；定时（含跨午夜、全天）、夜间只降不升、定时优先；静态缩放/亮度 0；呼吸上下限与 10 Hz、速度；双色/彩虹；温度（只认 nss-top-thermal、超范围/乱码/缺失回退）；CPU 两次采样与源缺失；流量速率、接口解析、计数回绕、设备缺失；只写变化通道与写失败重试；守护进程实跑（三种信号均关灯并删状态文件、只清 RGB trigger、其他 LED/风扇 PWM 夹具字节不变、静态不重复写、动画运行、重启换配置、定时关闭、数据缺失回退、非法选项记录、LED 缺失退出不写、写失败单次日志并恢复）；init（默认/leds_off 不启动、单实例与 `file` 参数、缺灯不启动、`service_stopped` 只清 RGB、dash/sh 语法）；包文件/依赖/来源许可、无风扇或其他灯引用；镜像选包；ACL 仅写 `rc01_rgb`、菜单、视图选项/模式与服务一致、翻译全覆盖、视图 JS 语法（node）；DTS 源接线；PWM 周期建模；最终 DTB 接线及 5 种错误接线（pwm 禁用、红色错通道、默认点亮、gpio32 被其他 pin state 复用、gpio31 被 GPIO 引用）均被检查器拒绝。
4. 变异检查（临时改代码后恢复）：去掉信号处理、让夜间/定时优先级失效、数据缺失回退改为黑色，均有对应测试失败。
5. 全量回归：`UCODE=/tmp/rc01-acceleration-astra/ucode uv run --offline --no-project python -B -m unittest discover -s tests/rc01` → `Ran 427 tests`，`failures=1, skipped=61`。唯一失败为已知基线 `test_router…test_first_generated_rc01_ap_is_disabled_before_any_service_reload`（无线首启 disabled，不属本模块）；跳过 = 基线 59 + 本模块 2 个需 `RC01_KERNEL_SOURCE` 的 DTB 测试（第 3 项已用稀疏内核树实际运行通过）。
6. `shellcheck -s sh -e SC2034,SC3043 package/emortal/rc01-rgb/files/rc01-rgb.init`：通过。
7. `make -C package/emortal/rc01-rgb TOPDIR="$PWD" -n compile`：配方可解析（本树无 host 工具/工具链/.config，仅解析，未产出包）。

曾尝试在 `scripts/prepare-rc01.sh` 增加 rc01-rgb 配方解析，但 `test_go_bootstrap` 固定了 prepare 的调用序列，已撤回，不改该流程。

## 未验证 / 待验

- **未构建**：未运行 `prepare-rc01.sh`/`make`，未产出 `rc01-rgb`、`luci-app-rc01-rgb`、`luci-i18n-rc01-rgb-zh-cn` APK，未验证 apk 安装/依赖解析、luci.mk 生成的 po 编译与最终 SquashFS 内容；未运行 `verify-rc01-images.py` 于真实镜像（其中已包含新的 RGB DTB 检查）。
- **DTB**：基于 v6.18.60 加 DT 相关补丁的稀疏树编译，不是本次完整内核构建目录；需在实际构建后用 `validate-rc01-platform.py` 再查。
- **目标运行时**：busybox/procd/rpcd/uci 实际行为（reload 触发、`file` 参数重启、SIGTERM 时序、`rc list`、`/etc/TZ`）未在 OpenWrt 上运行；LuCI 页面未在浏览器中渲染。
- **硬件**：GPIO31—33 的通道/颜色对应、极性、51000 ns 下的可见闪烁与亮度线性、关断是否完全熄灭、是否有其他使能/供电条件，均需实机逐色核对（建议先 `echo N > /sys/class/leds/<c>:ambient/brightness` 单色测试，再启用服务）。服务异常不改风扇调速、不争用系统/network/网口/SFP 灯，需实机复核。
- 未处理：NTP 同步前的日程偏差；`traffic_interface` 指向的 network 配置变更不会自动重启服务（RGB 配置变更或服务重启后生效）。
