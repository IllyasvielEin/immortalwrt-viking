# 上游 UP06 / PPE-A：隧道建流不阻塞等待 RTNL（0682）

基线 `6905f64e8892e00356b5bf9d71c452bc4edebf24`（UP01–UP04 已合入），分支 `dev/simplify-02-process-title`，单个本地提交。
不依赖 UP05。未构建目标内核或固件，未连接设备，未执行真实 RTNL、网络或硬件操作。等待 Astra 独立验收，本分支不自行合入。

## 适用性核对

结论：这是当前源选集里能进入的已有代码，可以按授权做最小修复。没有启用任何隧道，也没有改 Kconfig 或默认功能。

- 隧道分支来自本地已有的 `0514-03-net-ethernet-qualcomm-ppe-offload-map-e-fmrs.patch`。`ppe_ft_flow_replace()` 中，
  非 LWT 的 IPIP6/MAP-E 流在读取 ip6tnl 参数时调用 `rtnl_lock()` + `dev_ip6_tnl_flow_offload()`（修复前位于 C:3545）。
- 编译条件：`ppe_flowtable.o`、`ppe_tunnel.o` 无条件编入 `qcom-ppe`。`dev_ip6_tnl_flow_offload()` 在 `net/core/dev.c` 中
  无条件定义并导出，带 `ASSERT_RTNL()`。ip6tnl 通过 `ndo_ip6_tnl_flow_offload` 提供实现。IPQ9574 和 IPQ5332 两个变体都是
  `has_tunnel_engine = true`。只要用户配置了 ip6tnl/MAP-E 且流经 flowtable 卸载，就会进入这个分支。
- 静态触发链：`NETDEV_DOWN` 通知（持 RTNL）→ `nf_flow_table_cleanup()` → `nf_flow_table_offload_flush()` →
  `flush_workqueue(nf_flow_offload_add_wq)`。如果此时 add 队列里正有隧道流在执行 `ppe_ft_flow_replace()` 并阻塞在
  `rtnl_lock()`，两者就会互相等待。桥分支此前已经由 0668 改为 trylock，隧道分支没有。
- 收益：默认源码不配置 IPIP6/MAP-E，实际业务未知，所以默认业务收益 **B=0**。只有隧道建流与持 RTNL 的拓扑操作真正重叠时，
  这项修复才有意义。本项没有复现死锁，也不据此推断必然有收益。

## 来源

- VIKINGYFY/immortalwrt `85ebb0d08e6845c54b50370482ed0dd92cf39958` 新增
  `target/linux/qualcommbe/patches-6.18/0682-net-ppe-avoid-rtnl-wait-in-tunnel-flow-add.patch`。
  用 `gh api` 取得的 blob 为 `04753b023fcfa2945f838b479aaa6ec5b5014ea4`，与该提交 contents API 一致，也与公开 inventory 记录一致
  （946 字节，sha256 `3e73c489…6e79`）。原文逐字保留，作者 ViKing 和 Signed-off-by 未改，工作树 `git hash-object` 结果相同。
- 固定终点 `dd8de871` 对应的是 `0514-04` 展开后 C:3693–3702，代码与本项结果逐行相同。重组后的 0514-04/0651 补丁没有搬入。
- `scripts/rc01-platform-sources.json`：85ebb 的 followup scope 从 0683+0684 扩为 0682+0683+0684，三个 blob 都列出，并写明按数字顺序以
  fuzz0 应用。它仍是 partial，不表示整个 85ebb 提交或 0651 重组已合入。`reviewed_head`（`7b3b0f0f…`）和 `full_merge: false`
  未改。`selected_commits` 已含 85ebb（28 项无重复），CI `viking_selected_commits` 与 previous+selected 的集合一致，不需要改动。
  `local_patchset` 的哈希范围包含 `target/linux/qualcommbe`，已覆盖新补丁。UP02 的出处已更正为 43d9，67bb 对应 769，本项没有改动这两处。

## 改动内容

只改了 `ppe_ft_flow_replace()` 隧道参数读取处的一处：

```c
		/* NETDEV_DOWN holds RTNL while flushing the add workqueue. */
		if (!rtnl_trylock()) {
			ret = -EAGAIN;
			goto err_prelock_ivp;
		}
		ret = dev_ip6_tnl_flow_offload(tdev, &key, &tnl_info);
		rtnl_unlock();
```

按真实代码核对的结果：

- 调用位置在取得 topology_seq 并 `mutex_unlock(&ft->lock)` 之后，在发布阶段的 `mutex_lock(&ft->lock)` 之前，所以失败时没有持有
  `ft->lock`，也没有新增 `ft->lock → RTNL` 锁序。
- `err_prelock_ivp` 是这个分支原本 `dev_ip6_tnl_flow_offload` 失败时就走的标签。它依次释放 vp/ivp、`tdev`，再进入 `err_free`：
  计数 reject，释放 path、rx/tx account、egress_dev，执行 `kfree_rcu`。不会经过要求已持 mutex 的 `err_unlock`/`err_vp`。
- trylock 失败时 RTNL 并未取得，所以不会 unlock。`bridge_rtnl` 在这里一定为 false：桥流若同时是隧道流，会在更早处被拒绝
  （`goto err_tdev`），因此 `err_free` 末尾的 `rtnl_unlock` 不会被触发。
- 成功路径不变。争用时只是放弃本次硬件卸载并返回 `-EAGAIN`，流继续走软件路径，之后的请求可以再次尝试。没有加重试框架。
- 没有改桥 trylock、active-list/drain、资源诊断、769 或其他功能。LWT 隧道不读取 ip6tnl 参数，不受影响。

## 验证（实际执行）

1. 有序补丁链（单文件）：从 `target/linux/qualcommbe/patches-6.18` 按数字顺序提取所有指向 `ppe_flowtable.c` 的段并应用，
   `patch --fuzz=0`，在 `/var/tmp/rc01-up06-tunnel-rtnl/` 下的新目录中执行。
   - 不含 0682：39 段，0 个 `.rej`，结果与主线程组合准备树 `rc01-upstream-integrated` 的同一文件逐字节相同（证明重放方法正确）。
   - 含 0682：40 段，0 个 `.rej`。0682 有 offset −148；0683 的 hunk 6–10 和 0684 的 offset 因此各变化 +4 行，都没有 fuzz。
     与不含 0682 的结果相比，唯一的差异就是上面这个 hunk。
   - 私有准备树：把组合准备树复制到本树的 `build_dir/up06-tunnel-rtnl/linux-6.18.60`，用含 0682 的结果替换该文件。
     `diff -rq` 显示只有 `ppe_flowtable.c` 不同（sha256 `58b7169d…`；组合树为 `720250b9…`）。主线程的路径没有改动。
2. 新测试 `test_tunnel_parameter_read_does_not_wait_for_rtnl`（`tests/rc01/test_flowtable.py`）：
   - 静态部分：在真实 `ppe_ft_flow_replace()` 中断言没有 `rtnl_lock()`；隧道块内不涉及 `ft->lock`；隧道块位于 topology 采样的
     unlock 之后、发布阶段的 `mutex_lock` 之前，中间没有其他 mutex_lock；桥 RTNL 分支会拒绝隧道流。
   - 动态部分：取真实的隧道块和 `err_prelock_ivp:` 到函数结尾的真实清理尾部，用宿主 cc（`-Wall -Wextra -Werror`）编译执行，
     RTNL、VP、dev、path、account、`kfree_rcu` 用计数桩代替。覆盖的场景：
     - 争用时返回 `-EAGAIN`，不调用参数读取，不 unlock，vp/ivp/tdev/path/account/egress 各释放一次，reject 计数加一，不发布。
     - 不争用时在 RTNL 内读取（key 地址和端口方向正确），读完即释放，资源不释放，进入发布。
     - 原有读取失败路径照旧 unlock，并执行一次清理。
     - LWT 不读取参数，也不取 RTNL。
   - 没有 `RC01_KERNEL_SOURCE` 时，测试用本树补丁离线重放，同样通过。用组合准备树（无 0682）运行时测试失败。
   - 变异检查（在私有副本上修改真实源码）共 6 种，全部导致测试失败：失败时 `ret=0`、失败后继续往下执行、失败时多一次 unlock、
     跳过 VP 清理（直接 `err_free`）、改走需持 mutex 的 `err_unlock`、恢复阻塞的 `rtnl_lock`。
3. 回归（`uv --offline --no-project`，`TMPDIR=/var/tmp/rc01-up06-tunnel-rtnl/tmp`，`RC01_KERNEL_SOURCE` 指向私有准备树，
   其余输入来自只读的共享 `env.sh`）：test_flowtable、test_platform（含 DTB）、test_ci_build、test_software_sources、test_status
   共 235 项通过，0 失败，0 跳过。`validate-rc01-platform.py <私有树> <共享 backports>` rc=0。`git diff --check` 无问题。
   没有重跑 597 项全回归，没有跑 firewall4（本项不涉及）。

## 能执行与仅静态核对的边界

- 只执行了隧道块和预发布清理尾部这一小段代码，用宿主桩单线程运行。trylock 在真实内核里的争用行为、`NETDEV_DOWN` 与 add 队列
  flush 的完整锁链、软件回退后流量是否继续可用，都只做了源码阅读，没有执行。
- 没有为测试改写整个 `ppe_ft_flow_replace()`，也没有模拟它前段的解析和资源获取。

## 未完成与待验门槛

- 目标 Kbuild、模块和镜像都没有编译：宿主缺少 aarch64 交叉编译器、clang 和 flex/bison/m4，按要求没有安装，也没有搭工具链。
- 没有做 lockdep，没有做真实 IPIP6/MAP-E 隧道建流与并发接口 down 的测试，没有确认争用时流量能回退到软件路径，
  也没有做硬件 PPE 隧道卸载的实机测试。这些都需要实际的隧道业务和设备环境。
- 本项没有处理 UP05（firewall4 重启）、0651 重组或无线门槛。
