# 上游 UP04 / FLOW-ROUTE：flowtable 路由与邻居失效（完整 769）

基线 `4eca9f9602a748d43660f5e4f85ec8ae90cc78f0`（UP01+UP02 已合入），分支 `dev/simplify-02-process-title`，单个本地提交。
未读取或合并 UP03。未构建目标内核、模块或固件，未连接设备，未运行 ip/nft/路由/邻居操作。等待 Astra 独立验收，本分支不自行合入。

## 来源选取

依据公开 inventory `build_dir/rc01-upstream-20261112/inventory`（固定提交 API 记录）：

- 769 由 VIKINGYFY/immortalwrt `67bb668abe0b17a98278829ee38312d480ebed5e` 新增（ViKing，2026-11-11）。该提交只有这一个文件，
  当时 blob 为 `87b9308766396b37b7d65e68ddc58a908eed79e0`。
- `2bb59802ceeb8bcba408a7b6694f0c4669e24eab`（"refresh patches"）只给 9 个 hunk 头补了函数上下文（7+/7−），补丁正文没有变化，
  得到 blob `45ac1cfed4d84783b06adef41f2a3f28258ef8bc`。之后经合并 `1887488d` 进入固定读取点 `dd8de87169f1c1d091a81eb237341b2de09bd5bf`。
- 本项采用固定读取点的文件：`target/linux/generic/hack-6.18/769-netfilter-flowtable-revalidate-route-and-neighbour.patch`，
  与 `rc01-nss-audit-20261112/download-manifest.json` 一致（blob `45ac1cfe…`，sha256 `920d2d5f…6c7e`，5076 字节）。
  原文逐字保留，作者和 Signed-off-by 不变。工作树内 `git hash-object` 结果同为 `45ac1cfe…`。
  文件名沿用上游。本地已有 `769-net-ip6-tunnel-publish-flow-offload-parameters.patch`，按名称排序它在前，与上游顺序相同。
- `scripts/rc01-platform-sources.json`：`selected_commits` 中原有的 67bb668a 保留，`followup_selection` 里 67bb668a 的 scope
  改为 769 三文件组，并写明两个 blob 和 2bb59802 刷新。`reviewed_head` 仍为历史值 `7b3b0f0f…`，`full_merge` 仍为 false。
  `local_patchset` 的哈希范围已包含 `target/linux/generic/*-6.18`，不需要另建来源记录。

### UP02 出处更正（纯记录）

UP02 曾把 firewall4 004 记在 67bb668a 名下。inventory 显示 004/005/006 由 `43d9a24aab2cebab2d9bdb47a194ea28872c9cb6` 新增
（004 blob `b81a38fb…`），67bb668a 只新增 769。主线程已独立确认，并授权本项只做出处更正：

- 004 补丁的 RC01 说明行改为 43d9a24a；补丁内容、Makefile、release 和依赖都没有改。
- 来源 JSON 的 `selected_commits` 追加 43d9a24a，原 004 的 followup 条目改挂 43d9a24a。
- CI build-info 的 `viking_selected_commits` 追加 43d9a24a。核对结果：它与 `previous_selected_commits + selected_commits`
  的集合一致（32 个，无重复）。`viking_followup_commits` 与 `previous_selected_commits` 一致。
- `docs/rc01-modules/upstream-02-bridge-scope.md` 加了更正说明。外层 docs/reviews 的勘误由主线程处理。

## 改动内容（769，三文件）

- `nf_flow_table.h`：`flow_offload_tuple` 新增 `route_cache`、`route_cookie`、`neigh_ha`、`neigh_valid`，位置在 `__hash` 之后，
  不进入查找键。`nf_flow_route.out` 新增 `h_neigh`。
- `nf_flow_table_core.c`：
  - DIRECT 原先立即 `dst_release()`；现在把 `nft_route_dst_fetch()` 转移来的引用存为 `route_cache`，没有额外 hold。
    IPv6 记录 `rt6_get_cookie()`。只有 dst 设备为 `ARPHRD_ETHER` 时才记录邻居 MAC：优先取 `h_neigh`；旧 xt_FLOWOFFLOAD
    的 route 是零初始化的，没有 `h_neigh`，这时回退到 `h_dest`。
  - `nft_flow_dst_release()` 对两个方向都释放 `route_cache`（NULL 安全）；NEIGH/XFRM 照旧释放 `dst_cache`。
  - 新增 `nf_flow_route_valid()`：
    - 无 `route_cache` 时：NEIGH/XFRM 用已有的 `dst_cache`/cookie 做 `dst_check`，其他情况（纯桥 kzalloc 对象）返回 true。
    - 有 `route_cache` 时：先 `dst_check`；若记录了邻居，就用 conntrack 反方向源地址（NAT 后的目的地址，与发现阶段一致）
      `dst_neigh_lookup`，在 `neigh->lock` 内判断 `NUD_VALID` 并比较 MAC，然后 `neigh_release`。
  - `nf_flow_dst_valid()` 先调用它，再走原有桥 FDB 检查。GC 原本就对两个方向调用 `nf_flow_dst_valid()`，失效后走已有的
    teardown 和硬件删除。
- `nf_flow_table_path.c`：DIRECT 路径把发现阶段的邻居地址 `ha` 存入 `h_neigh`。PPPoE 路径会把 `h_dest` 改写为会话对端，
  `h_neigh` 不受影响。

效果与边界：已加速的流不再长期沿用旧路由或旧网关 MAC，后台 GC 发现失效后撤流，后续包重新走正常路径学习。
这不是路由变化后逐包立即生效，也不提高带宽。REACHABLE/STALE/DELAY/PROBE 等仍属 `NUD_VALID` 的状态变化不会撤流。
纯旁路桥流仍只做原 FDB 检查，所以收益主要在路由/NAT 流。没有新增路由监听、重试框架或开关，也没有改动默认 LAN/WAN、
禁用服务、PHY/MLO、PPE ownership/readback 或桥 FDB 逻辑。

## 验证（实际执行）

1. 完整补丁链：在 `build_dir/up04-flow-route/full/` 从只读的 `rc01-verify-inputs/dl/linux-6.18.60.tar.xz` 重放（sha256 与
   `kernel-6.18` 固定值一致）。依次复制 generic files，应用 backport/pending/hack，再用 `scripts/patch-kernel.sh` 应用
   qualcommbe 补丁，`PATCH="patch --fuzz=0"`，全链 0 个 `.rej`。
   - 与旧共享准备树 `rc01-verify-inputs/src/linux-6.18.60`（ccb3270，无 0683/769）做 `diff -rq`：只有 `ppe_flowtable.c`（UP01 的 0683）
     和这三个文件不同。
   - 把旧树的三个文件复制出来单独应用 769：`--fuzz=0`，没有 offset，结果与全链产物逐字节相同。
   共享输入和 UP02 的 pristine 目录都没有修改。
2. 新测试 `test_routed_flows_hold_dst_and_revalidate_route_and_neighbour`（`tests/rc01/test_flowtable.py`）：
   从准备好的内核中提取真实的 `flow_offload_tuple`/`nf_flow_route`/`nft_forward_info` 定义，以及以下函数：
   `flow_offload_dst_cookie`、`nft_route_dst_fetch`、`flow_offload_fill_route`、`nft_flow_dst_release`、`flow_offload_route_init`、
   `flow_offload_route_release`、`nf_flow_route_valid`、`nf_flow_dst_valid`、`nft_dev_forward_path`，用宿主 cc
   （`-Wall -Wextra -Werror`）编译后执行。dst 引用、邻居表/锁、FDB 和路径发现的边界用桩实现。覆盖的场景：
   - PPPoE 场景下发现阶段 `h_neigh` 等于邻居 MAC，与改写后的 `h_dest` 分开保存。
   - IPv4 DIRECT 双向：引用转移、不额外 hold、双向释放后引用计数归零。
   - NAT 场景下邻居查找地址取反方向源地址。
   - STALE/DELAY/PROBE/PERMANENT 状态保持有效；FAILED/INCOMPLETE、MAC 变化、邻居缺失、dst 过期判定失效。
   - MAC 比较时持有邻居锁，邻居引用平衡。
   - IPv6 cookie 改变后失效。
   - 旧 xt 路径无 `h_neigh` 时回退到 `h_dest`。
   - PPP dst 不查邻居，但仍持有并释放 route。
   - NEIGH 方向不设 `route_cache`，继续用 `dst_cache` 检查。
   - 纯桥流只走 FDB；经桥的路由流先查路由/邻居再查 FDB。
   另有静态断言：`route_cache` 位于 `__hash` 之后。
   变异检查（在私有副本上改真实源码）共 13 种，全部导致测试失败：去掉 route 释放、额外 hold、`h_neigh` 取 `h_dest`、
   去掉 `NUD_VALID`、只认 REACHABLE、邻居地址方向错误、漏掉 `neigh_release`、GC 未接入、去掉旧 xt 回退、去掉 IPv6 cookie、
   对 PPP 查邻居、NEIGH 不做 `dst_check`、把 `route_cache` 移进键区。用旧准备树（无 769）运行时该测试同样失败。
3. 回归（uv `--offline --no-project`，`TMPDIR` 为本轮新建的 `/var/tmp/rc01-up04-flowroute`）：
   - 输入：`RC01_KERNEL_SOURCE` 指向本项全链树；`RC01_FIREWALL4_SOURCE` 用本项从固定 e6f600c 归档新解压、再以当前补丁
     fuzz0 应用的副本；其余输入来自共享 `env.sh`（只读）。
   - 范围：test_flowtable、test_platform（含 DTB）、test_ci_build、test_firewall4_bridge、test_software_sources、test_firmware_pairing。
   - 结果：共 68 项通过，0 失败，0 跳过。
   - `validate-rc01-platform.py <本项内核树> <共享 backports>`：rc=0。
   - 来源 JSON 可解析，CI selected 集合核对一致，`git diff --check` 无问题。
   未重跑 597 项全回归。

## 能执行与仅静态核对的边界

- 已执行的只有上面列出的函数边界，用的是宿主桩，单线程运行。以下内容只做了源码阅读，没有执行：
  - GC 调度：`nf_flow_offload_gc_step` 双向调用、teardown/HW 删除。
  - `flow_offload_free` 只对 ROUTE 类型释放。
  - xt_FLOWOFFLOAD 的 `route = {}` 零初始化。
  - `nft_flow_route` 中 dst 的获取与转移。
  - PPE 驱动没有使用新字段，查找键也没有变化。
- 结构体扩展影响所有使用 `nf_flow_table.h` 的模块，必须在同一次内核构建中重新编译（kmod-nf-flow、nft/xt flowoffload、
  qcom-ppe 等）。本项没有改包定义。

## 未完成与待验门槛

- 目标 Kbuild、模块和镜像都没有编译：宿主没有 aarch64 交叉编译器、clang 和 flex/bison/m4，按要求没有安装，也没有搭工具链。
  `dst_neigh_lookup`、`rt6_get_cookie`、`read_lock_bh` 等内核 API 的真实类型和语义没有经过编译器检查。
- 没有运行真实 GC、lockdep、并发撤流、硬件 PPE 删除、IPv4/IPv6 双栈 NAT、网关 MAC 或路由切换、PPPoE、桥流量的实机测试，
  也没有测量 GC 额外开销。
- 本项没有处理 PPE 槽位诊断（UP03）、firewall4 重启（UP05）、隧道 trylock（UP06）或无线门槛。
