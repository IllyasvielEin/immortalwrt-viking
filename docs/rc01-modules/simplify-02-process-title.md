# 简洁性第二批第 2 项（S03）：状态页进程表标题

基线 `aad9ddd253580945a9da765d4b24345449563e7e`；分支 `dev/simplify-02-process-title`。只改文案，未构建 APK/固件，未连接设备。

## 改动

- `package/emortal/luci-app-rc01/htdocs/luci-static/resources/view/status/rc01.js`：标题从 `Proxy processes` 改为
  `Related service processes`。表格仍不过滤，显示 `state.processes` 的全部内容（代理、VPN、fan guard、SFP、netled 等）。
- `po/zh_Hans/rc01.po`：msgid 同步改为新标题，msgstr 改为“相关服务进程”，旧 msgid 已删除。
- `Makefile`：`PKG_RELEASE` 从 4 改为 5（`PKG_PO_VERSION` 随之变化）。

未改：collector、RPC 字段与 ACL、表列及数据，也没有加进程名单、过滤或分类。其他界面、服务、温控、设备树和上游包都没有动。

## 验证（离线）

- 现有回归：`UCODE=<公开 host ucode> uv run --offline --no-project python -B -m unittest tests.rc01.test_status_ui
  tests.rc01.test_status tests.rc01.test_netled_status.Translations`，共 150 项，全部通过，0 跳过。
- 用 Node 解析 `status/rc01.js` 和 `rc01/netled.js`，语法正确。
- PO 检查：没有重复或无法解析的条目。status/netled 视图中的 `_()` 字符串都有译文。唯一被报出的那条长串
  来自检查脚本，它没有把 `\'` 转义还原；PO 里实际有这条译文。旧标题的 msgid 已不在 PO 中。宿主没有 msgfmt，所以没用它检查。
- 在临时目录用手工 state 渲染页面（不用 collector，也不读宿主 sysfs/proc）。样本进程为 mihomo、rc01-fan-guard、
  rc01-sfp-led、rc01-netled。结果：新标题存在，旧标题不存在，四个进程的名称和 PID 都在表中。
- `git diff --check` 通过。

## 未验证

- 没有实际打包 APK 或生成 i18n lmo。
- 没有在目标 LuCI 上查看页面，也没有做实机检查。
- 没有重跑全部 566 项回归。
