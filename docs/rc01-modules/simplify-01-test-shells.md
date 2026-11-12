# 简化 S04：SFP/RGB 测试实际使用指定 BusyBox

基线 `aad9ddd253580945a9da765d4b24345449563e7e`；分支 `dev/simplify-01-test-shells`。只改测试接线，产品实现未改。
未构建固件，未连接设备，也未在实机验证。等待 Astra 独立验收，本分支不自行合入。

## 问题

`tests/rc01/test_sfp_led.py` 和 `test_rgb.py` 按 PATH 查找 `busybox`，忽略 `BUSYBOX`，
所以 `scripts/rc01-verify-inputs.sh` 导出的固定 BusyBox 没有被执行。本机 PATH 中没有 busybox（`/bin/sh` 是 dash），
这两个套件此前实际只跑了 dash。RGB init 夹具固定执行 `sh`，SFP init 夹具也一样。0 skip 不能说明 ash 已覆盖。

## 改动（仅 `tests/rc01/test_sfp_led.py`、`tests/rc01/test_rgb.py`）

- 两个文件各自采用 test_netled/test_fan_guard 的约定：优先用 `BUSYBOX`，未设置时才用 PATH 中的 `busybox`。
  显式路径不是可执行文件时，导入就抛 `RuntimeError`，不会退回 PATH。
- 命令行改由 `SHELL_COMMANDS` 给出（`dash` → `[dash]`，`busybox` → `[$BUSYBOX, "sh"]`），不再用 `shutil.which(shell)`。
  两种 shell 都没有时仍回退到 `sh`。没有新建公共测试框架。
- SFP：服务生命周期、缺失 LED 退出和 `-n` 语法检查都使用所选路径。缺失 LED 退出改为逐个 shell 运行（subTest）。
  init 夹具在每个 shell 下运行，并断言各 shell 的输出一致。
- RGB：init 夹具（禁用、启用、缺失 LED、service_stopped）在每个 shell 下用新的 fixture 运行（subTest），
  `-n` 语法检查也用所选路径。RGB daemon 本身是 ucode，不经过 shell；本项**不**说明 daemon 在 ash 下运行。
- 保持不变：SFP `rc01_sfp_step`/restore 的单步测试仍只用首选 shell（dash），这是原有设计；
  RGB stderr 的 `ResourceWarning` 不阻断，未改；其他四项建议也未改。

## 实际执行的 shell 子项

| 套件 | 方法数 | ash（BusyBox）执行的子项 | 调用次数 |
| --- | ---: | --- | --- |
| test_sfp_led | 21 | 服务生命周期 2 次启动、缺失 LED 退出、init 夹具 10 次、语法检查 2 个文件 | 13 次 `sh -c`、2 次 `sh -n` |
| test_rgb | 39 | init 夹具：禁用 2 次、启用、缺失 LED、stopped 2 次；语法检查 | 6 次 `sh -c`、1 次 `sh -n` |

选中的二进制是 `rc01-verify-inputs/src/busybox-1.38.0/busybox`（ash；`CONFIG_FEATURE_PREFER_APPLETS` 未设置，
`FEATURE_SH_STANDALONE` 未设置），通过临时包装器调用：包装器记录参数后 `exec` 这个真实二进制。

## 验证

在专用临时目录中，用 uv 管理的 CPython 3.14 运行 `python -B -m unittest tests.rc01.test_sfp_led tests.rc01.test_rgb -v`，
环境为 `env -i`，PATH 为 `<禁用桩>:<node>:/usr/bin:/bin`（其中没有 busybox）。
UCODE、RC01_KERNEL_SOURCE、RC01_LUCI_VALIDATION_SOURCE 均指向上述公开输入。

- 预检：PATH 只含 `echo` 桩和指向 BusyBox 的 `timeout` 链接时，`busybox sh -c 'timeout 1 echo applet'` 输出 `stub`，
  说明 applet 不会绕过 PATH。另把 `logger`、`uci`、`ubus`、`ip`、`ping`、`jsonfilter` 做成记录调用后失败的禁用桩，
  放在 PATH 最前；所有运行中都没有被调用（被测脚本中 logger、uci 是 shell 函数桩，sysfs 用私有 `RC01_SYSFS`）。
- PATH 无 busybox，`BUSYBOX` 指向包装器：60 个方法 OK，0 skip。包装器记录到 19 次 `sh -c`、3 次 `sh -n`，与上表一致。
- 反证：`BUSYBOX` 指向一个总是 `exit 3` 的程序时，结果为 FAILED（16 个失败）。
  所有 busybox 子项（SFP 生命周期、缺失 LED、init 夹具、两个语法检查，以及 RGB 四个 init 子测试）都失败，
  说明结论依赖 ash 真实执行。
- 显式错误路径：`BUSYBOX` 指向不存在的路径或不可执行的文件时，退出码 1，报错 `RuntimeError: BUSYBOX=... is not an executable file`，没有回退。
- dash 回归：不设置 `BUSYBOX`，PATH 中也没有 busybox：60 个方法 OK，0 skip，没有任何 BusyBox 调用。
- `py_compile` 与 `git diff --check` 通过。未运行完整的 566 项回归，留给最终集成。

## 剩余边界

- 0 skip 仍不能说明 ash 已覆盖：只有设置 `BUSYBOX`（或 PATH 中有 busybox）时，busybox 子项才会存在。
  集成和 CI 应使用 `rc01-verify-inputs.sh` 生成的 `env.sh`。本项没有添加“必须有 busybox”的门槛。
- 被测脚本调用的 `cat`、`rm`、`sleep` 等外部命令仍来自宿主 PATH（applet 未优先），与目标 BusyBox applet 的差异未覆盖。
- 宿主 ash 测试不等于目标镜像或实机通过。
