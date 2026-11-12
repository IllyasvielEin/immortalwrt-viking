# 上游 UP01 / PPE-B：PPE active list 与按 block 排空

基线 `ccb3270cdc1040abcbf960f92e5490c90ded7b6e`；分支 `dev/simplify-01-test-shells`，在其上追加一个提交。
未构建固件或目标对象，未连接设备，也未在实机验证。等待 Astra 独立验收，本分支不自行合入。

## 来源

- 上游 VIKINGYFY/immortalwrt `85ebb0d08e6845c54b50370482ed0dd92cf39958`（作者 ViKing，2026-11-11），
  只取其中 `0683-net-ppe-drain-flow-blocks-outside-rcu-walks.patch`。
- 用 `gh api repos/VIKINGYFY/immortalwrt/git/blobs/fcd69f0c…` 取原文并本地重算 Git blob SHA-1，
  结果为 `fcd69f0c13e602e076ac0cede5678d0148eb8573`，与提交清单一致。新增文件逐字节保留原文（作者、
  Signed-off-by、说明不变），工作树中的 `git hash-object` 也是这个值。文件名沿用上游 0683；该序号在本地未被使用，排在 0681 之后。
- 同一提交中的 0682（隧道 RTNL trylock，对应 UP06）和 0684（槽位/隔离诊断，对应 UP03）未纳入本项。
- 固定终点 `dd8de87169f1c1d091a81eb237341b2de09bd5bf` 已把这部分合进 `0651-03`。本项没有引入该补丁的重组，
  也没有带入 RTL837x 等其他功能。
- 已在 `scripts/rc01-platform-sources.json` 的 `selected_commits`/`followup_selection` 中登记为 partial。
  `reviewed_head`、`baseline`、`full_merge: false` 以及原有 selected 范围都保持不变。85ebb0d0 晚于
  `reviewed_head`，scope 中已注明这一点。

## 本地差异

补丁文本没有改动。它对本地 37 段 `ppe_flowtable.c` 补丁链以 `--fuzz=0` 应用，十个 hunk 只有行号偏移
（−2…−197，本地没有 0682 等上游中间改动）。展开后以下函数与终点展开代码
（`build_dir/rc01-ppe-audit-20261112/endpoint-expanded`）逐字相同：`count_inc/count_dec`、
`ppe_ft_flow_destroy_locked`、`ppe_ft_flow_destroy`、`ppe_ft_flow_drain`、`ppe_ft_block_release`、
`ppe_ft_flow_forget`、`ppe_ft_invalidate_locked`。

## 改动内容（0683）

- `struct ppe_ft_flow` 新增 `active_node`，`struct ppe_flowtable` 新增 `active_flows`。前者在
  flow 分配处初始化，后者在 `ppe_flowtable_setup` 中初始化。
- `count_inc` 在 `ft->lock` 下把对象加入 active list，`count_dec` 用 `list_del_init` 移出（均带
  `lockdep_assert_held`）。现有计数入口保持一次增、一次减，没有新增入口：
  - 发布成功：`rhashtable_lookup_insert_fast` 成功后 inc。
  - VP 发布失败回滚：remove 后 dec，再进入 `err_hw_flow`。
  - forget（stale/replace/index 冲突/invalidate 成功）：仅对非 zombie 对象 dec。
  - 普通 destroy：dec。
  - 拓扑失效时硬件删除失败：先 dec，再 quarantine。
- 从 `ppe_ft_flow_destroy` 中提取 `ppe_ft_flow_destroy_locked`。硬件回读、按所有权删除、失败转
  quarantine 的流程不变。
- `ppe_ft_flow_drain(ft, block)` 在 `ft->lock` 下用 `list_for_each_entry_safe` 遍历 active list，
  只处理指定 block 的对象；传 NULL 时处理全部剩余对象。block release 照旧计入 `destroy_calls`。可睡眠的
  退休操作因此不再处于 rhashtable walk 的 RCU 读侧临界区内，也不必每删一条就重启整表查找。
- `ppe_ft_block_release` 改为 drain 自己的 block 后 `kfree(fb)`。最终 teardown 改为 `ppe_ft_flow_drain(ft, NULL)`。
- 保持不变的部分：callback 引用与最后解绑契约（仍由 `flow_block_cb` 生命周期驱动 release）、index
  ownership、硬件 readback、zombie/quarantine 以及 teardown 中的 zombie 释放。没有新增生命周期框架。
- 硬件删不掉的规则会离开 active list 和活动计数，作为 zombie 保留其硬件依赖。这不表示硬件行已经清空。

## 验证（实际执行）

1. 完整补丁链：在 `build_dir/up01-ppe-b/` 校验 linux-6.18.60 tarball 的 sha256（与 `kernel-6.18` 固定值一致；
   tarball 以符号链接只读引用主线 `rc01-verify-inputs/dl`）后解压。按 `rc01-verify-inputs.sh` 的顺序应用
   generic files 以及 backport/pending/hack，再用 `scripts/patch-kernel.sh` 应用全部 qualcommbe 补丁（含 0683），
   结果 0 个 `.rej`。与只读的共享准备树 `rc01-verify-inputs/src/linux-6.18.60` 做 `diff -rq`（忽略 `.orig`），
   唯一差异是 `ppe_flowtable.c`。该文件与单文件展开结果逐字相同。
2. 入口审计：在展开源码中逐一核对所有 `count_inc/count_dec`、`rhashtable_remove_fast/insert`、
   `forget`、`destroy`、`drain` 调用点，结论见上文。`deps_put`、`hw_del`、`quarantine` 都不会删除
   其他对象，所以 `_safe` 遍历只需应对删除当前项。
3. 新增测试 `test_block_drain_retires_outside_rcu_and_keeps_other_blocks`（`tests/rc01/test_flowtable.py`）：
   编译补丁后的真实函数，桩只覆盖链表、锁和硬件边界。场景为 block1 含一条正常 IPv4 规则、一条硬件删除失败的
   IPv6 规则和一条先被 forget 的规则，block2 含一条规则。检查项：
   - 退休时持锁且不在 RCU 内。
   - 失败规则进入 zombie，未释放，且已移出 active list 和计数。
   - 先被 forget 的规则不会再被访问。
   - block2 不受影响，`destroy_calls` 为 2。
   - 最后 `drain(NULL)` 清空 active list 和计数，且不再增加 `destroy_calls`。

   变异检查：分别删除 `count_dec` 的 `list_del_init`、去掉 block 过滤、在遍历中模拟 RCU 读侧、
   删除 `count_inc` 的入链，4 种变异全部使测试失败。
4. `test_worker_drain_callers_do_not_hold_rtnl_or_flow_lock` 中原先对旧调用 `ppe_ft_flow_drain(ft)` 的定位已更新为
   `ppe_ft_flow_drain(ft, NULL)`。
5. 宿主回归：`RC01_KERNEL_SOURCE` 指向本项补丁后的副本，其余输入用主线只读 `rc01-verify-inputs/env.sh`，
   `TMPDIR` 设为本轮新建的 `/var/tmp` 临时目录（`/tmp` tmpfs 几乎写满，会导致 ENOSPC）。共运行 575 个方法：
   - 0 失败。
   - 3 个 setUpClass 跳过：dnsmasq 准备源码、packages feed、固定 feed git 对象。这些输入本 worktree 没有，
     与本补丁无关，也未为本项补齐。
   - 1 个偶发错误：`test_fan_guard` 某个夹具清理时 `rmtree` 报 Directory not empty。单独重跑 3 次都通过，
     且该测试不使用 PPE 源码。
   - 首轮未设 TMPDIR 时 575 个方法 0 失败/0 错误。

   主线此前的 597 方法包含上述已准备输入，不应和本次数字比较。
6. `validate-rc01-platform.py <本项内核副本> <共享 backports>`：rc=0。
7. 未执行：`validate-rc01-nand.sh` 需要实际构建的 `vmlinux`，本项没有。

## 未完成与待验门槛

- 目标对象/模块未编译：宿主没有 aarch64 交叉编译器或 clang。尝试 x86_64 COMPILE_TEST 构建时，kconfig 因缺少
  flex/bison（以及 m4）而失败，uv 离线缓存中也没有 kconfiglib。按要求未安装全局工具，也未构建工具链。因此
  `guard(mutex)`、去掉 `const` 后的签名等只经过上述宿主桩编译（`-Wall -Wextra -Werror`）和与终点代码的逐字比对，
  没有经过真实 Kbuild 编译。
- 未执行 lockdep/多 block 并发解绑、硬件退休失败的真实注入、最终 teardown 实机，也未验证吞吐或稳定性。
  宿主测试只覆盖单线程顺序语义。
- 本项不改变隧道 trylock、诊断输出、769、firewall/NSS 或无线门槛。HW1 文案与代码的偏差由主线程单独记录。
