# 上游 UP03 / PPE-C：PPE 物理槽位与隔离预算诊断

基线 `c9d16c45f5e6dce0c4952bfe36f020044f4bad19`（已含 UP01/0683）；分支 `dev/simplify-01-test-shells`，在其上追加一个提交。
没有构建固件或目标对象，没有连接设备，也没有做实机验证。等待 Astra 独立验收，本分支不自行合入。

## 来源

- 上游 VIKINGYFY/immortalwrt `85ebb0d08e6845c54b50370482ed0dd92cf39958`（作者 VIKINGYFY，2026-11-11），
  这次只取其中的 `0684-net-ppe-report-physical-flow-budget-in-diagnostics.patch`。
- 原文通过 `gh api repos/VIKINGYFY/immortalwrt/git/blobs/335c56c4dfb827025e0070eb88db8e53d92442e5` 获取，
  获取后在本地重算了 Git blob SHA-1，结果是 `335c56c4dfb827025e0070eb88db8e53d92442e5`，与公开 commit 清单
  （`rc01-upstream-20261112/inventory/85ebb0d0….json`）一致。文件 sha256 为
  `6117a1869aa407602c3366bcae558d7fd98a0da7ccde5f1cd0c5b2a059bae941`。
  新增文件逐字节保留原文，From/Date/Signed-off-by（ViKing）和说明都没有改，工作树中 `git hash-object` 的结果仍是这个值。
- 0682（隧道 RTNL trylock，对应 UP06）仍然没有纳入。固定终点 `dd8de871` 的 `0651-04` 是由 0651 与 0684
  合并、重组而来。本项没有搬入 0651 的重组内容、RTL837x 或其他功能；终点 0651-04 只用于比对。
- `scripts/rc01-platform-sources.json` 中 85ebb0d0 的 `followup_selection` scope 由“只有 0683”扩为
  “0683 + 0684”，并明确排除 0682。`reviewed_head`、`baseline`、`full_merge: false` 和
  `selected_commits` 列表都没有改，不表示整个提交已经合入。UP02 也会修改这个 JSON，之后合入时的冲突由主线程处理。

## 内核改动（0684，逐字）

`ppe_ft_resource_stats_show()` 在原有 `mutex_lock(&ft->lock)` 区间内读取 IPv4/IPv6 活动方向计数并遍历 `ft->zombies`。
原有首行 `host … flow active:V4/V6 capacity:N`、failure 行和 nexthop 行保持不变，在首行之后追加两行：

```text
flow ipv4_directions:N ipv6_directions:N active_slots:V4+2*V6 capacity:N
quarantine rules:N slots_budget:N
```

紧凑接口 `active_flows` 没有改动。

本地适配：补丁正文没有改动。补丁以 `--fuzz=0` 应用，唯一的 hunk 只有行号偏移（−197）。本地缺少的是上游的中间改动，
例如 0682，不涉及语义适配。应用后，该函数与终点 `endpoint-expanded` 的同名函数逐字相同。0684 是最后一个修改
`ppe_flowtable.c` 的补丁，所以后续补丁的应用顺序不受影响。

## collector / RPC / 页面

- `platform.uc` 的 `ppe_resources()` 继续沿用 `bounded(…, 8192)`、`token_groups`、`counters_only`，以及现有的
  `missing/unreadable/truncated/malformed` 语义，没有新增解析框架。新增规则如下：
  - 不带首行 `active` 比值的 `flow` 组记为 `slots`，`quarantine` 组记为 `quarantine`。
  - 新格式下 `ignored_groups` 为 0；原来是 `ok/ignored_groups=2`。
  - 只要出现新组，就必须带全各自的键（slots 4 个，quarantine 2 个），并且值都是计数。缺键、坏值或键重复时，整个
    来源判为 `malformed`，全部值都不输出。
  - 旧内核不输出这两行时，`slots`/`quarantine` 为 `null`，表示不可用；数值 `0` 照常作为有效值输出。
  - 所有值都保留十进制字符串，不转换成数字，因此大数不会丢精度。
- RPC 和 ACL 都没有改：仍是 `rc01.status` 只读，没有写方法，也没有加速开关。`flows` 与 `reason_counters` 这两个重视图
  仍是 `not-collected`，不会打开，也没有新增整表硬件读取。
- 页面只在原有资源区（资源来源 `ok` 时）增加三行：流方向数（IPv4 / IPv6）、加权流槽位（活动 / 容量）、
  隔离规则（规则数 / 槽位预算）。旧内核下这三行显示“此内核未提供”。另加一段通俗说明，内容包括：
  - 方向数不等于连接数；
  - IPv6 规则按 2 个槽位计；
  - 隔离预算是加权的所有权估计，不是去重后的占用行数；
  - 活动槽位加隔离预算得不出精确的剩余槽位或利用率；
  - 规则离开活动计数不代表硬件行已经删除。

  页面不展示吞吐，也不推算剩余槽位。读取失败、截断或格式错误时，只显示来源状态。由于每次轮询整页重绘，
  之前成功读到的值不会保留。模块 01–08 的其他区域（状态、风扇、RGB、SFP、无线）没有改动。
- 翻译：`rc01.po` 新增 5 条中文翻译。
- 版本：先核实了基线，当前为 `rc01-platform` `PKG_RELEASE:=11`、`luci-app-rc01` `PKG_RELEASE:=5`，
  按现有约定分别提升到 12 和 6。

## 验证（实际执行）

1. 内核源树：新建 `build_dir/up03-ppe-c/`，从受审 UP01 的已准备树 `build_dir/up01-ppe-b/src` 只读复制
   （原路径没有修改）。用 `git diff 10d1a4d c9d16c4 -- target/ package/kernel scripts/ include/` 确认，
   从 UP01 提交到当前主线，内核补丁链没有变化。归档以符号链接只读引用 `rc01-verify-inputs/dl`，sha256
   `3c2f44ef…621e` 与 `kernel-6.18` 中的固定值一致。在副本上应用 0684 的结果：`--fuzz=0`，0 个 `.rej`。
   与 UP01 树做 `diff -rq`（忽略 `.orig`），唯一的差异文件是 `ppe_flowtable.c`，其中只有上述函数的变化。
2. 新增和更新的针对性测试都在 `tests/rc01/test_status.py`，统一使用真实 host ucode 运行生产 collector，
   用 Node 渲染真实页面。所有夹具都是本轮在临时目录中新建的，没有读取宿主或设备。
   - `test_kernel_resource_output_is_read_by_the_collector`：用宿主 cc 编译补丁后的真实
     `ppe_ft_resource_stats_show()`，内核边界用桩替代，并断言 `atomic_read`/`seq_printf` 都在锁内执行。
     程序输出交给 collector 解析。覆盖两种情况：隔离为 0（rules/slots_budget 输出 `"0"`），以及 3 条隔离
     （2 条 IPv6 + 1 条 IPv4），预期 slots_budget 为 5；同时检查 active_slots=5+2×2=9，u64 失败计数保持精度。
     `RC01_KERNEL_SOURCE` 指向没有 0684 的 UP01 树时，该测试失败。
   - `test_slot_budget_lines_are_read_and_old_kernels_are_unavailable`：新格式 → `ok`、`ignored_groups=0`，
     原 flow 字段不变，零值保留为 `"0"`；旧格式 → `slots`/`quarantine` 为 `null`。
   - `test_bad_or_incomplete_slot_lines_withhold_all_values`：覆盖缺键、坏值和重复键，结果都是 `malformed`，
     且不输出任何值。
   - `test_slot_budget_rows_render_and_a_failed_read_drops_them`：用 collector 依次采样“新格式 → 旧格式 → 新格式 →
     超出 8 KiB → 新格式 → 不可读”，并经页面轮询逐次重绘。检查内容：新行与说明出现；旧格式下 3 行显示不可用；
     截断和不可读的页面不再出现之前的 `12 / 3`、`18 / 4,096`、`2 / 3` 和 u64 值；u64 按千分位完整显示。
   - 原有的 `test_hardware_table_views_are_never_opened` 改用新格式夹具，继续用无写端 FIFO 确认重视图不会被打开。
   - 变异检查：把 collector 换回基线版本，或去掉 slots 识别分支后，上述新测试都会失败。
3. 选定回归：设置 `RC01_KERNEL_SOURCE=build_dir/up03-ppe-c/src/linux-6.18.60`，其余输入使用主线只读
   `rc01-verify-inputs/env.sh`，`TMPDIR` 指向本轮新建的 `/var/tmp` 目录。结果：
   - test_status、test_status_ui、test_netled_status、test_flowtable、test_platform、test_firmware_pairing、
     test_ds_lifecycle、test_group_teardown、test_fan_guard、test_sfp_led、test_rgb、test_netled：403 个，全部通过，0 跳过。
   - test_nand、test_runtime、test_images：43 个，全部通过。
   - test_software_sources、test_ci_build、test_package_updates：43 个通过。其中 1 个 setUpClass 因缺少固定 feed
     git 对象而跳过，原因与本项无关。
   - 没有重跑完整的 597 项。
4. `validate-rc01-platform.py <本项内核副本> <共享 backports>`：rc=0。

## 未完成与待验门槛

- 目标对象未编译：宿主没有 aarch64 交叉编译器、clang、flex、bison 或 m4，按要求没有安装，也没有构建工具链。
  内核函数只经过宿主桩编译（`-Wall -Werror`），并与终点代码做了逐字比对。`validate-rc01-nand.sh` 需要实际的 `vmlinux`，没有执行。
- 未做实机验证：没有真实的 debugfs 输出、容量、隔离样本或 LuCI 浏览器渲染。宿主测试只覆盖单线程、顺序执行的语义。
- 诊断数值只用于解释资源压力，不代表吞吐、稳定性或精确的剩余物理槽位。
- 不涉及隧道 trylock、769、firewall/NSS 或无线；HW1 文案与实际代码判定之间的偏差，按外层 CONTRACTS 的记录处理，本项不改。
