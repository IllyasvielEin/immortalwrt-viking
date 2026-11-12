# 简化 S01（排序第3项）：同一次 collect 只枚举一次 MTD

基线 `ec07e9929d2533b224697c3e4fcd4637a1c02139`（S04 之后）；分支 `dev/simplify-01-test-shells`，在其上追加提交。
未构建固件，未连接设备，也未在实机验证。等待 Astra 独立验收，本分支不自行合入。

## 改动

- `package/emortal/rc01-platform/files/platform.uc`：新增 `mtd_records(root)`，每次 `collect` 调用一次：
  glob `mtd[0-9]*`，仍只保留 `^mtd[0-9]+$`（排除 `mtdNro`），每个节点读取一次 name（小写，缺失为 `''`）
  和原有 6 个属性，存入一个局部数组 `{ name, info }`。`partition(records, wanted)` 在该数组中按名称过滤，
  恰好 1 条才返回 `info`，否则返回 `null`；`layout_matches(records, contract)` 用 `length(records)` 计数。
  返回给 state 的分区对象字段不变（不输出 `name`）。没有跨调用缓存、注册表或通用框架。
- `package/emortal/rc01-platform/Makefile`：`PKG_RELEASE` 9 → 10。
- 未改动：`rc01.sh` 升级布局检查、`assess` 的身份/ECC 检查、fan/PPE/NSS/wireless/SFP/netled、驱动、设备树和上游包。

保留的语义：节点计数是 26 个真实节点，不按唯一名称计数；名称大小写归一，重名（包括只差大小写）返回 `null`，不会被后一条覆盖；
字段缺失或无法解析时为 `null`，不会补 0；offset/size/erasesize/writesize 仍按 26 项严格比较；ENV 映射比较的是实际解析到的 device；
LICENSE/bdata 的 bad_blocks/ECC 读不到时仍判失败。

## 测试（`tests/rc01/test_status.py`）

复用现有的生产入口夹具（每个测试都在新的临时目录里建 sysfs/proc）。原有用例已经覆盖：ENV 长度、offset 移动、缺契约、身份 ECC=1、
rootfs ECC 缺失，以及 ro alias。本项新增 6 个方法：

- 重名：`0:LICENSE` 完全相同、`0:license`/`BData` 只差大小写、`rootfs` 重复；对应分区为 `null`，identity-storage/nand-ecc 和 layout 都报错。
- 计数：第 27 个节点名为 `extra`，或没有 name 文件时，layout 报错，身份检查仍正常；26 个节点中有一个缺 name 时，layout 也报错。
- 字段缺失：LICENSE bad_blocks、bdata ecc_failures、rootfs bad_blocks/offset 缺失或无法解析时为 `null`，对应检查报错。
- 几何：非身份节点和 rootfs 的 size、erasesize、writesize 分别改错，layout 都报错。
- ENV：appsblenv 改编号为 mtd13 后，`/dev/mtd12` 映射报错，改为 `/dev/mtd13` 后通过。
- 同一个 ucode 进程内连续两次 `collect`，中间用 fs 修改 ECC、重命名 mtd41→mtd42，第二次反映新值（没有跨调用缓存）。

`Status` 有 4 个子类继承全部方法（原有约定），所以新增 6 个方法实际运行 30 次。

## 验证（实际执行）

环境：`build_dir/rc01-verify-inputs/env.sh` 中的公开输入（host ucode、BusyBox、LuCI JS、内核/无线源码），
uv 管理的 CPython 3.14，使用离线模式和专用 cache。

- `python -B -m unittest tests.rc01.test_status`：171 项 OK，0 skip（基线 141 项）。
- 与 platform.uc 相关的 6 个套件：test_status、test_status_ui、test_netled_status、test_fan_guard、test_layout、test_runtime，共 229 项 OK，0 skip。
- 基线对照：把 `git show HEAD:…/platform.uc` 写入本轮临时目录，在 `Status` 类（28 个方法，含新增）上运行，全部通过，说明新用例表达的是原有语义。
- 变异反证（只对临时副本）：重名取最后一条、名称不转小写、按唯一名称计数、不排除 ro alias、缺 bad_blocks 补 0、去掉 erase 或 page 比较、
  ENV 固定 `/dev/mtd12`。8 种变异都至少有 1 个用例失败。
- 计数插桩（只对临时副本）：把 `fs` 换成一个包装模块，统计 `sys/class/mtd` 下的 glob 和 readfile 次数（包括文件不存在的读取尝试）。
  新旧实现的 `storage` 输出逐项完全相同；同一进程连续两次 collect 的计数相同。

| 夹具 | 旧 glob/读 | 新 glob/读 |
| --- | ---: | ---: |
| 健康完整布局、ro alias、ENV 改编号、身份 ECC 缺失 | 31 / 960 | 1 / 182 |
| 缺契约 | 4 / 128 | 1 / 182 |
| 第 27 个节点、大小写重名 | 5 / 132–138 | 1 / 189 |

这些是 sysfs 访问次数，不是耗时测量。失败路径的读取反而增加（缺契约时 128 → 182），因为每个节点都会读全部属性。

- `git diff --check` 和 Python 语法检查通过。未运行完整的 566 项回归和目标构建。

## 未验证

- 一次枚举不是原子快照：sysfs 各字段仍分别读取。
- 实际 MTD 驱动下的节点和属性、目标 ucode 版本、镜像中的 rc01-check/LuCI 调用，都没有在目标或实机上验证。
