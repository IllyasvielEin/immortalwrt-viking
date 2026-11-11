# 模块 06：可配置出口连通状态灯（交接）

本文只记录开发事实与离线验证结果，不代表镜像构建或实机验收通过。

初版 `787a1a3` 已经 Astra 初审，页面 R1 已由主线程局部修复，待新的独立复验；修复提交见本分支 git log 和外层交接索引。原始开发验证保留在下文，修复验证另列。

## 基线、范围与提交

| 项目 | 值 |
|---|---|
| worktree | `/home/illya/.t3/worktrees/immortalwrt/dev-module-06-connectiv-3be17756452b8313` |
| 分支 | `dev/module-06-connectivity-led` |
| 基线 | `0aefaee18551938f58e44e8627d3cdcb2e1a42f9`（开工时 status 干净） |
| 功能提交 | `009b514`（rc01: configurable egress connectivity LED） |
| 交接文档提交 | 在功能提交之后单独提交（本文件） |

本树未合入 RGB（主线 `a9ac7c6`），也未读取 RGB 工作树。若两者合并，`platform.uc`、`rc01.po`、ACL/菜单 JSON 和 `test_status_ui.py` 可能出现文本冲突，需要人工合并。本模块只增加 `connectivity` 字段和 `rc01.connectivity` 方法，不改变已有字段。

依据：外层只读资料 `docs/FEATURES.md` 第 4 节、`CONTRACTS.md`（LAN DHCP 旁路默认行为）、`COMMUNITY.md`（外部实现固定目标、直接控制白/橙灯，本实现改为可配置，并补充 unknown/stale 与退出恢复）。未读取原 rc01 资料、原代码或任何设备资料。

## 核对结论（源码/依赖）

- **LED 节点**：`ipq9570-be10000.dts`（ES 版继承）中 `network-white`（GPIO42）、`network-amber`（GPIO43）为 gpio-leds，`function = "network"`、无 label，`default-state = "off"`，按内核 LED 命名规则对应 `white:network`、`amber:network`。`board.d/01_leds` 未给 network 灯配置策略，`/etc/init.d/led` 默认不管理这两颗灯。系统灯（GPIO40/41，boot/running 别名）、SFP 灯、网口 PHY 灯以及 RGB 均不在本模块范围内。
- **触发器**：`target/linux/generic/config-6.18` 中 `LEDS_TRIGGER_DEFAULT_ON=y`、`LEDS_TRIGGER_TIMER=y`，qualcommbe 未覆盖。
- **BusyBox**：版本 1.38.0，源码包 sha256 `34f9ea6f…53bb2` 与 `package/utils/busybox/Makefile` 一致，下载到临时目录后打上 OpenWrt 全部补丁。RC01 defconfig 未覆盖 BusyBox 选项，因此默认 `PING`、`PING6`、`FEATURE_FANCY_PING`、`TIMEOUT`、`IP`、`FEATURE_IP_ROUTE`、`LOGGER` 均为 y。阅读源码得到：
  - `ping`：`-c 1 -W N` 无应答时 `exit(1)`，有应答时为 0，发送错误（xsendto 失败）也是 1；`-I` 优先按接口名 `SO_BINDTODEVICE`，接口不存在时会尝试解析为地址。
  - `timeout [-s SIG] SECS PROG`：被监视的父进程直接 exec PROG，由孙进程负责 kill，因此 `-s KILL` 超时后 shell 看到的退出码是 137；exec 失败为 127。
  - BusyBox `ip route get` 支持 `oif`，输出形如 `dst via … dev … src …`；非 unicast 类型以类型字作开头（`local`、`unreachable` 等）。
- **ip**：RC01 defconfig 选中 `ip-full`；ip-full 与 BusyBox `ip` 都支持 `route get … oif`，因此未新增强制依赖。`ubus`、`jsonfilter`、`logger` 均来自 base-files/procd/busybox 的依赖链。
- **默认网络**：`89-rc01-bypass` 把 lan 设为 DHCP 客户端，wan/wan6 不自动启动，默认路由在 lan（br-lan）上。因此默认采用 `auto`，不假设 wan 一定存在。

## 设计

### 文件

| 文件 | 作用 |
|---|---|
| `package/emortal/rc01-platform/files/rc01-netled` → `/usr/libexec/rc01-netled` | 探测与 LED 守护（POSIX sh），`check`/`run` 两个入口 |
| `…/rc01-netled.init` → `/etc/init.d/rc01-netled` | procd 服务，START=97 |
| `…/rc01_netled.config` → `/etc/config/rc01_netled`（conffiles） | 默认配置，`enabled '0'` |
| `…/platform.uc` | 只读读取 `/var/run/rc01-netled/state.json`，新增 `connectivity` |
| `luci-app-rc01/…/rpcd/ucode/rc01.uc` | 新增只读方法 `rc01.connectivity`，`status` 不变 |
| `luci-app-rc01/…/view/rc01/netled.js`、菜单、ACL、po | 「服务 → 出口连通状态灯」设置页及状态展示 |

`rc01-platform` 的 PKG_RELEASE 改为 8，`luci-app-rc01` 改为 2；后者的标题不再写“只读”。

### UCI（`rc01_netled.main`）

| 选项 | 默认 | 范围/规则 |
|---|---|---|
| `enabled` | 0 | 0/1 |
| `ipv4`、`ipv6` | 1、1 | 0/1，至少启用一个 |
| `ipv4_target`、`ipv6_target`（list） | `223.5.5.5`、`119.29.29.29` / `2400:3200::1`、`2402:4e00::` | 每族 1–3 个单播地址 |
| `ipv4_interface`、`ipv6_interface` | `auto` | `auto` 或 UCI 接口名 `[A-Za-z0-9_]+` |
| `interval` | 30 | 10–3600 秒，纯整数 |
| `timeout` | 2 | 1–5 秒 |
| `fail_count`、`recover_count` | 3、2 | 1–10 |

默认目标为阿里 DNS 和 DNSPod 的公共任播解析地址，配置文件注释和页面上都写明了来源，可直接替换。测试中只使用 RFC5737 / `2001:db8::/32` 文档地址。

地址校验：
- IPv4 必须是点分四段、不带前导零，排除 0/8、127/8 和 224/3。
- IPv6 只接受纯十六进制写法，最多一处 `::`，拒绝嵌入 IPv4、zone、`::`、`::1`、fe80::/10 和 ff00::/8。

所有值以 `key=value` 形式作为独立 argv 传递，只在双引号中展开，没有任何 `eval`。init 先调用 `rc01-netled check`；校验失败时写日志说明“配置无效”，不启动服务。

### 探测与状态

每轮对每个启用的协议族执行：
1. 若指定了接口：`ubus -t 3 call network.interface.<if> status`，用 `jsonfilter` 读取 `up` 和 `l3_device`。
2. 对每个目标：`ip -4|-6 route get <t> [oif <dev>]`，再执行 `ping -4|-6 -q -c 1 -W <timeout> [-I <dev>] <t>`。

每条命令都在 `timeout -s KILL` 下运行（ubus 5 s、ip 3 s、ping timeout+2 s），以后台方式加 `wait`，保证停止信号立即生效。任一目标应答即结束本轮，探测串行执行，不并发。最坏情况下一轮耗时有上界，该上界写入报告的 `valid_until`。

单轮结果分为以下几类：
- `ok`：有应答。
- `fail`：路由存在但没有应答。
- `nopath`：无路由、路由类型为 unreachable/local 等、`dev lo`、没有 src，或 IPv6 只有 link-local 源地址。
- `unavail`：指定接口不存在、未启动或没有设备。
- `error`：工具缺失、被超时杀掉、输出无法解析、ubus 查询失败。

多个目标的结果按优先级合并：ok > fail > error > nopath。

各协议族的状态：

| 状态 | 进入条件 |
|---|---|
| `unknown` | 新进程初始状态；在 unknown 下发生的错误仍保持 unknown |
| `up` | 首次 ok 即进入；从 down 类状态恢复需连续 `recover_count` 轮 ok |
| `down` / `no-path` / `egress-unavailable` | 连续 `fail_count` 轮对应的坏结果 |
| `stale` | 已有结论后，连续 `fail_count` 轮 error；error 不重置失败计数 |
| `disabled` | 该协议族未启用 |

总状态：
- `up`：所有启用的协议族都是 up。
- `degraded`：部分为 up。
- `down`：没有 up，且至少一个确定为坏状态。
- 其余情况为 `unknown`。

仅 IPv4 或仅 IPv6 时，未启用的协议族不参与计算。

### LED 与控制权

| 总状态 | 白 | 琥珀 |
|---|---|---|
| up | `default-on` | `none` |
| degraded | `timer` 1000/1000 ms | `none` |
| down | `none` | `default-on` |
| unknown/stale | `none` | `timer` 1000/1000 ms |
| 服务停止 | `none`（熄灭，板级默认） | `none` |

- **启动接管**：只从 `none`+亮度 0，或本服务自己用的 `default-on`/`timer`（例如被 SIGKILL 后遗留）接管；其他触发器视为他人正在使用，保持原样。`system` 中有 `led` 段的 `sysfs` 指向某灯，或设置了 `leds_off=1` 时，init 不把该灯交给服务（报告中为 unmanaged），探测照常运行。
- **运行中**：每次写入前核对灯是否仍是本服务上次写入的触发器（none 还要求亮度为 0）；不一致则让出，只写一次日志，本进程内不再抢回。
- **写入失败**：报告为 `write-failed`，日志只写一次，下一轮重试。
- **停止**（TERM/INT）：杀掉当前探测子进程；仍归本服务控制的灯恢复为 `none`；删除状态文件。

### reload 机制

init 把全部配置（包括哪些灯可用）放进 procd 的 command argv，并注册 `procd_add_reload_trigger rc01_netled system`。配置变化后命令行随之变化，procd 重启实例，新进程从 unknown 开始。配置未变时 procd 不重启，这正是预期行为。`enabled=0` 时不登记实例，procd 会停止旧实例。

### 状态读取（只读）

满足以下全部条件时，`rc01.connectivity` / `status.connectivity` 才返回报告：
- UCI 已启用；
- 存在 `rc01-netled` 进程，且其 pid 与报告中的 pid 一致；
- 当前 uptime 不超过报告的 `valid_until`；
- JSON 结构合法，读取上限为 8 KiB。

否则只给出 `disabled`、`not-running`、`starting`、`stale`、`malformed`、`truncated` 或 `unreadable`，`report` 为 null，不显示旧结果。`rc01-netled` 派生的同名子 shell 不计为多个实例。

### LuCI 与 ACL

- 新 ACL 组 `luci-app-rc01-netled`：读权限为 ubus `rc01.connectivity` 和 uci `rc01_netled`，写权限只有 uci `rc01_netled`。原 `luci-app-rc01` 组仍只有 `rc01.status` 读权限，没有新增任何写接口。
- 页面明确说明：检测的只是 ICMP 可达性，不代表 DNS、网页或代理正常；ICMP 被过滤时可能显示不可达而其他业务正常；不动系统、RGB、网口和 SFP 灯。页面同时列出灯态含义和各状态的解释，刷新失败时清除旧状态。
- 整数选项使用 `and(uinteger,range(…))`，另加“无前导零”校验，因为 LuCI 的 uinteger 接受 `030`，而后端拒绝（shell 算术会按八进制处理）。目标地址在 `ip4addr/ip6addr("nomask")` 之外还加了与后端相同的单播规则。

## 验证

### 宿主工具

| 工具 | 版本/来源 | 用途 |
|---|---|---|
| dash | 0.5.12-12ubuntu3 | 运行守护进程测试 |
| BusyBox | 1.38.0 + OpenWrt 补丁，upstream defconfig 自建，位于会话临时目录 | ash 及 timeout/sleep/mv/rm/mkdir/cat applet |
| ucode | 公开宿主 `/tmp/rc01-acceleration-astra/ucode`（源码 dfc64d41） | 状态读取测试 |
| Node | v24.22.2 | LuCI 视图测试 |
| validation.js | immortalwrt/luci `df794c7f69361641195a4b336634d1b1c918099c`，通过 `gh api` 取得，sha256 `4b68d550…9652e`，放在临时目录 | 页面与后端校验一致性测试 |
| shellcheck | 0.11.0 | 静态检查 |
| timeout | 宿主为 uutils coreutils 0.10.0 | dash 变体中使用 |

### 命令

```sh
BUSYBOX=<临时目录>/busybox UCODE=/tmp/rc01-acceleration-astra/ucode \
LUCI_VALIDATION_JS=<临时目录>/validation.js \
uv run --offline --no-project python -B -m unittest -v \
  tests.rc01.test_netled tests.rc01.test_netled_status tests.rc01.test_status_ui \
  tests.rc01.test_status tests.rc01.test_sfp_led
# 结果：Ran 257 tests, OK，无跳过

# 同样的环境变量下全套运行一次：
uv run --offline --no-project python -B -m unittest discover -s tests/rc01
# 结果：Ran 481，failures=1，skipped=57
# 唯一失败为已知基线 test_router…test_first_generated_rc01_ap_is_disabled_before_any_service_reload（无线首启 disabled），非本模块
# 设置 BUSYBOX/UCODE 后跳过数为 57（已知基线未设置时为 59）
```

### 覆盖内容（真实入口 + stub）

测试中守护进程以 `RC01_NETLED_LIBRARY=1` 加载真实文件，执行真实的 `rc01_netled_main check|run` 和每轮流程，仅替换写 sysfs 的函数，模拟内核 trigger 文件语义。`ip`、`ping`、`ubus`、`jsonfilter`、`logger` 均为固定 stub。PATH 只含 stub 和少数链接进来的工具（timeout/sleep/mv/rm/mkdir/cat/env/python3），因此缺失的 stub 就是真正缺失。init 与一致性测试的 PATH 中另放了守卫 stub：一旦调用即记录并失败，测试断言没有任何调用。Validation、Probing、Leds 三类测试在 dash 和 BusyBox ash 下各跑一遍，Lifecycle 两种 shell 都覆盖。

- **校验**：各种合法/非法 IPv4/IPv6、数量上限、接口名、数值范围与前导零、未知键；注入 `$(touch …)` 不会被执行；`run` 收到非法参数时退出码为 2，且不写 LED。
- **探测**：
  - IPv4 auto 在旁路默认路由 br-lan 下正常；仅 IPv6；
  - 一族无路由时总状态为 degraded（白灯闪烁），无路由时不发 ping；
  - unreachable/local/`dev lo`/无 src/仅 link-local src 归为 no-path；
  - 指定接口时 route 带 oif、ping 带 `-I`；接口缺失、down、无设备时为 egress-unavailable；经指定接口无路由时为 no-path；
  - BusyBox 输出格式能正确解析，异常的设备名或类型字不会破坏 JSON。
- **防抖**：单个目标失败而另一目标成功时保持 up；首个目标应答后不再探测第二个；失败确认与恢复的状态序列和 LED 写入序列；error 中性、累计后转 stale 再恢复；unknown 下的 error 保持 unknown；error 不重置失败计数。
- **有界性**：ping 挂起时在 timeout+2 秒内被 KILL，ip 挂起时 3 秒被 KILL，均记为 error；工具缺失（127）记为 error；每轮命令数量上限正确；`valid_until` 计算正确。
- **LED**：
  - 用户改用其他触发器或改亮度后让出、只写一次日志、不抢回，停止时只恢复仍归本服务的灯；
  - 启动时灯已被占用则不接管；能回收遗留的本服务触发器；unmanaged/missing 状态；
  - 写入失败上报、只写一次日志并重试；内核缺少某触发器时按写入失败处理；
  - 真实写函数对只读文件失败时不会终止 dash/ash；timer 周期正确设置。
- **生命周期**（真实进程 + SIGTERM，dash/BusyBox）：运行后达到 up；停止时恢复两灯为 none 并删除状态文件；换配置后新进程从 unknown 开始，不显示旧的成功；探测挂起期间收到 TERM 可在 2 秒内退出（procd 默认 5 秒后发 KILL）；用户接管后停止，不覆盖用户设置。
- **init**：使用真实的 `functions.sh`、`uci.sh`，`/sbin/uci` 换成 export stub，在 BusyBox ash 下运行（`export -n` 需要 ash/bash）。覆盖：默认关闭；启用后参数完整；ES 板型同样支持；配置改变则命令改变；`system` 中的 led 段或 `leds_off` 会排除对应的灯；非法配置不启动并写日志；其他板型不启动。
- **status/RPC/ACL/LuCI**：
  - 真实 `platform.uc` 读取状态文件：ok；disabled、not-running、starting、malformed、stale、pid 不符、超长时都不返回 report；
  - RPC 只有 status/connectivity 两个只读方法；ACL 写权限只有 uci `rc01_netled`；
  - LuCI 视图在 Node stub 下运行：选项、默认值与配置文件一致；整数 datatype 正确；状态与灯态文案齐全；轮询失败时清空旧结果；
  - 用真实 validation.js 对比页面与 `rc01-netled check`，所有样例结果一致，并确认既有接受也有拒绝；
  - 每个页面字符串都有 zh_Hans 翻译，po 无重复 msgid、无解析遗漏。宿主没有 msgfmt，未做 msgfmt 检查。
- **包**：Makefile 安装路径与 conffiles 正确，引用文件都存在，START=97，默认关闭；LED 名称只涉及两颗 network 灯，与 DTS 的 GPIO42/43 对应；`01_leds` 未增加 network 策略；dash/ash `-n` 与 shellcheck 均通过（忽略的规则见测试）。

## 测试中误调用宿主 ping（透明记录）

**原因**：`test_netled.py` 早期版本的 stub PATH 后面还拼接了宿主 PATH。子测试 “missing ping → probe-failed” 删除 ping stub 后，命令落到了宿主 `ping`。同一测试中 “missing ip” 子测试删除 ip stub 后，落到了宿主 `ip`。

**已修复**：守护进程测试的 PATH 只含 stub 和指定工具；init 与一致性测试另加守卫 stub。修复后确认不存在残留的 `ping` 或 `timeout` 进程（仅按这两个进程名精确查询）。

**根据本任务已有证据核对**：

| 项目 | 情况 |
|---|---|
| 目标 | 只有 `203.0.113.1`（RFC5737 TEST-NET-3 文档地址）。没有 IPv6 调用，也没有其他目标 |
| 调用次数 | 修复前完整运行过该测试 2 次，每次该子测试 2 轮。推断宿主 `ping -4 -q -c 1 -W 1 203.0.113.1` 共约 4 次，每次在 `timeout -s KILL 3` 之下，`-c 1` 即每次最多 1 个 echo request |
| 时长 | 第一次运行全部 41 个测试耗时 10.3 s，第二次运行的耗时输出已被过滤，未记录；单次调用约 1 s（`-W 1`），上限 3 s |
| 是否有响应 | 两次运行中该子测试都得到 `fail/no-reply`，即宿主 ping 退出码为 1，没有收到应答 |
| 是否实际发出 | 推断已在宿主发送；报文是否离开 WSL 宿主、经过哪段网络，**未知** |
| 宿主 `ip` | `ip -4 route get 203.0.113.1` 同样约 4 次。这是本机 netlink 只读查询，不发包 |
| 现场设备 | 测试没有连接设备、SSH 或刷写，也没有向设备地址发包。若宿主默认路由经过现场路由器，这些 echo 可能作为普通转发流量经过它，**未知**，未做进一步核实 |

为核实上述情况没有再做任何实际探测，也没有查看无关进程或日志。

## 初审 R1 修复验证（主线程）

- 启用服务时，enabled/IPv4/IPv6 三个开关共同校验至少一个协议族启用；关闭服务仍允许保存。
- 目标列表在服务及对应协议族均启用时才激活，并设置必填；空列表不能再被真实 form.parse 当成可删除选项保存。隐藏时保留已有目标配置，禁用服务或单个协议族不会清空原列表。后端既有校验保留。
- 新增测试使用 locked LuCI `df794c7f69361641195a4b336634d1b1c918099c` 的真实 `form.js`（可由 `LUCI_FORM_JS` 提供）和 `validation.js` 执行生产页面的保存/依赖/校验方法；DOM、映射与UCI存储是夹具，不是浏览器端到端。覆盖12组空目标、协议族组合、数量上限、正常目标、单协议族和关闭服务场景；合法保存结果再经隔离PATH调用真实后端check。
- 设置公开host ucode、`LUCI_VALIDATION_JS` 和 `LUCI_FORM_JS` 后，运行 `test_netled_status`、`test_status_ui`、`test_status`、`test_sfp_led`：**175通过、0跳过**。旧页面导出到新临时目录时，新增保存用例产生9个失败断言，0执行错误、0跳过；修复后通过。`git diff --check`、新增Node harness语法通过。
- 本轮只改页面、中文翻译、LuCI包release（2→3）、相关测试与本交接；daemon/init/ACL/探测和灯控制逻辑未改，没有实际网络探测。此前独立257项守护/BusyBox验证证据保留，不计作本轮重跑。
- 初审的非阻断权限边界保留：受限账号缺少已有 `luci-base-network-status` 读组时，接口建议列表可能为空；auto和手填逻辑接口仍可用。未扩展network写权限，实际rpcd session仍待目标验证。

## 已知缺口（待验）

- **镜像构建**：未执行。`make package/…/compile`、包内容、`/etc/config` conffile 和 init enable 行为，以及 luci.mk 的 po 编译，均待构建验证。
- **目标环境实际行为**：
  - 守护进程只在宿主 dash 和自建 BusyBox ash 上运行过，目标镜像中的 procd 行为未执行过；
  - procd 对“命令不同则重启、命令相同不重启、无实例则停止”的实际行为未运行，`term_timeout` 时序也未实测；
  - `ubus -t 3 call network.interface.X status` 的退出码（不存在时为 4）与 `jsonfilter -e '@.up'` 的输出（`true`/`false`）按已知语义编写，测试使用的是 stub，需在目标上核对；
  - ip-full 与 BusyBox `ip route get` 的实际输出（特别是 IPv6 无路由、策略路由、`oif` 与 PPPoE 设备）待核对；
  - BusyBox ping 的 `-6 -I <dev>` 需要实机确认（上游源码注释称 IPv6 + 地址形式未测试；本实现用的是设备名）。
- **LED**：最终 DTB 中的 sysfs 名称、`default-on`/`timer` 实际可用性、白/琥珀两色的视觉区分及闪烁效果、与 `/etc/init.d/led` reload 的实际交互、写失败的真实错误码，均需实机核对。
- **网络行为**：现场 ICMP 被过滤、仅 IPv4、仅 IPv6、旁路、指定 wan/wan6 等场景的真实结果，需要在许可的现场条件下另行验收。
- **LuCI**：浏览器中的真实渲染未执行，只在 Node 中用 stub 运行，`form.js` 未加载。DynamicList 的条目校验和 `network.getNetworks()` 的权限需在真机 LuCI 上确认。
