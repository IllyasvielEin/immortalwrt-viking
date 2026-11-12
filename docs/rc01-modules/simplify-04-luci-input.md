# 简洁性第二批第 4 项（S05）：统一 LuCI validation.js 环境变量

基线 `6493370c79b92096dc599995069aa4baa5357031`（含已合入的 S04/S03）；分支 `dev/simplify-02-process-title`。
只改测试接线、准备脚本、CI 与源码内构建说明，产品代码和包内容不变，没有改 `PKG_RELEASE`。

## 改动

- `tests/rc01/test_rgb.py`：RGB 页面整数校验改为读取 `LUCI_VALIDATION_JS`。未设置时仍回退到
  `feeds/luci/modules/luci-base/htdocs/luci-static/resources/validation.js`；跳过提示同步改名。
- `scripts/rc01-verify-inputs.sh`：生成的 `env.sh` 不再导出 `RC01_LUCI_VALIDATION_SOURCE`。
  每次重建的机制没有改。
- `.github/workflows/build-rc01.yml`：构建后回归不再传 `RC01_LUCI_VALIDATION_SOURCE`，保留
  `LUCI_VALIDATION_JS` 和 `LUCI_FORM_JS`（后者指向 form.js，用途不同，所以保留）。
- `docs/rc01-build.md`：零跳过回归只要求这两个变量，并注明 RGB 的 feed 回退。

没有加双变量兼容层，旧变量现在不会被读取。`docs/rc01-modules/05-rgb.md` 和 `simplify-01-test-shells.md`
里的旧名是历史交接记录，没有改写。`test_netled_status` 本来就只读 `LUCI_VALIDATION_JS`/`LUCI_FORM_JS`，也没有 feed 回退，所以没改。

## 验证（离线，公开输入）

输入来自主线 `build_dir/rc01-verify-inputs/env.sh`，包括 ucode、BusyBox 1.38.0、LuCI `df794c7f`
和 Linux 6.18.60/backports 7.2。先 source 这个文件，再 `unset RC01_LUCI_VALIDATION_SOURCE`，以确认测试只靠规范变量就能拿到输入。
Python 用 `uv run --offline --no-project`，并给它一个专用临时缓存目录。

| 场景 | 命令/条件 | 结果 |
|---|---|---|
| 只设规范变量 | `unittest tests.rc01.test_rgb tests.rc01.test_netled_status -v`，工作树没有 `feeds/` | 48 项通过，0 跳过；RGB `test_page_integer_validation_agrees_with_real_backend` 和 netled 的两项 Parity 都实际执行 |
| feed 回退 | 去掉三个 LuCI 变量，在本工作树临时建 `feeds/luci` 链接指向公开 LuCI 源（git 忽略该目录，测后已删除），用零跳过门槛跑 `test_rgb` | 39 项通过，0 跳过，门槛 rc=0 |
| 缺输入且没有回退 | 不设变量，也没有 `feeds/` | `test_rgb` 跳过 1 项，门槛 rc=1（预期失败） |
| 只设旧别名 | 只设 `RC01_LUCI_VALIDATION_SOURCE`，没有 `feeds/` | 同样跳过 1 项，门槛 rc=1，说明旧名已不被读取 |
| netled 缺输入 | 不设变量 | 跳过 2 项，门槛 rc=1（预期失败，行为不变） |

- netled Parity 会调用真实的 `rc01-netled check`。运行前先做了 preflight：PATH 里只有 dash，另有一组
  ping/ping6/ip/ubus/jsonfilter/logger/timeout 桩，桩被调用会记录并失败。设
  `RC01_NETLED_LIBRARY=1` 跑 `check` 得到 rc=0，所有桩都没有被调用。测试自带的 `isolated_bin` 有同样的守卫，测后断言没有被调用的记录。
- `env.sh` 接线：用 awk 从脚本里取出原 heredoc 模板，在临时目录里用示例 `W/KV/BV/BBV` 展开并 source。
  结果只导出 `LUCI_VALIDATION_JS` 和 `LUCI_FORM_JS`，没有旧变量。
- `sh -n scripts/rc01-verify-inputs.sh`、`py_compile tests/rc01/test_rgb.py`、`git diff --check` 都通过。
  工作流这一步的续行检查过：删掉一行后，前后两行仍以 `\` 衔接到 `python3`。宿主没装 PyYAML，所以没有做 YAML 解析。

## 未验证

- 没有重跑全部 566 项，也没有执行 CI 工作流、`rc01-verify-inputs.sh` 全流程或目标构建。
- 主线 `build_dir/rc01-verify-inputs/env.sh` 仍有旧别名。下次运行准备脚本时会重新生成，到时旧别名就会消失。本次没有直接改它。
- 没有连接设备，也没有做实机检查。
