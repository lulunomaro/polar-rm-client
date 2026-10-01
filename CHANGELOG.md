# Changelog

> 本文件只记**面向使用者/维护者的行为变化**，不记内部重构的中间步骤。
> 口径：以 `git log` 的提交说明为准，条目全部可回溯到提交号；
> 凡是本文件写不下来的（纯重构、无行为变化），就不写。

## Unreleased

### 2026-10-01 补记：2026-08-16 之后的 83 个提交

上一版记录停在 2026-08-16 的 `6b7880d`（更新 GD656 击杀图标资源至上游 1.1.0）。
从那以后到 `142074c`（polar_link 快照收敛成单一来源）之间有 **83 个提交**。
影响面按"净变化"记（`git diff --name-status 6b7880d..HEAD`）：**新增 140 个文件、
共改动 186 个文件**。若改成"逐次相加"（`git log --name-status`）则是 540 次文件
变更、其中 400 次 M —— 那是改动**次数**，与文件**个数**不是一回事，本文取前者，
因为读者关心的是影响面。下面按主题归并，括号里是代表提交。

#### DT7 / 遥控器取证（这轮最大的主线）

- 官方 12.x 选手端逆向结案：DT7 那条链路是 **虚拟串口**（官方驱动 INF 作证），
  不是私有 HID 协议；键值表被挪进配置文件，MQTT 是 12.x 才有的能力。（`7b2e004`、`d7d0834`、`49d0105`）
- **更正两处此前说重的结论**：`电脑经 DT7 控车这条路本来就是通的`，缺的只是
  `PC<->DT7` 私有协议；`__RC_TYPE` 配错**不会**让 DT7 车锁死（键鼠开车一直能用），
  真正致命的是反向情形——车上是 25 字节遥控器而宏选错那一家。（`9c8c68b`、`b47c32a`）
- 客户端新增 DT7 直连出口 `dt7_link.py`，帧结构按厂商反汇编复原；帧型实测为
  **5A/22 字节**（不是借来的 A5+17），CRC 表与厂商逐字节等价。（`eb45856`、`d916405`、`4c5a94a`）
- 图传键鼠链路（`0x0311` + `PRM1 kind=3`）两端接通，五档 `__CONTROL_LINK_KM` 落地；
  D 路（车端 USB<->WiFi 桥）加入，作为不依赖大疆客户端的无线控车备份路径。（`a0e8ed4`、`d149707`）
- C 路补齐三处会导致**第一次上车直接卡住**的缺陷：`DT7 Port = Auto` 以前开不了口；
  DT7 口被拔掉/睡过去后不会自己恢复；DT7 被拔期间限流标记要**用真出口**测。（`0aac1e5`、`813aac6`、`5a0dc12`）
- 新增三个现场工具：DT7 抓包差分（`test_frame_diff.py`，认得那 22 字节帧）、
  C 路扫帧型闭环自检（`dt7_sweep.py`，`NO-CHANGE` 之后一条命令定位是哪一种）、
  界面上的 `Run DT7 Link Selftest` 按钮（探针用 X 键，不会把车开走）。（`1807ca0`、`2d27f9a`、`e74c6d1`）
- 无线自检结论新增 `NOT-SENT`：客户端自己没发时，不许说成"DT7 没理这一帧"。（`143ece2`）
- 松手帧被 30Hz 闸吃掉 = 手松了车还在走：中立帧破例 + 补发记账。（`112a5dc`）

#### 电控侧（固件桥与工具链）

- 新增三套可直接 `git apply` 的固件桥：**速度回读桥**（`0x1E`，补固件缺代码）、
  **英雄云台直控桥**（`0x04 param0`，解掉英雄板"底盘+瞄准"死冲突）、
  **自转桥接**（`0x04`/`0x1F`）。`prepare_hero_build.py` 一键装。（`9a74edd`、`036f94e`）
- 驾驶键位改成**记账合并**：`HOST_RC_DRIVER_KEYS | HOST_RC_HOST_KEYS`，修掉两个方向相反的缺陷
  ——纯 `|=` 在无键盘遥控器上松键粘住（车刹不住），DT7 的整字段覆盖又抹掉客户端键位。
  新增 8 组可执行行为断言 + 阴性对照。（`1b8b03e`）
- 比赛链路切到 **DT7 + DR16**（`prepare_hero_build.py --rc dt7`，默认值），
  仓库出厂写的 `RC_ET08A` 保留在行尾注释里可来回切。（`1b8b03e`）
- **真工具链证据**：ARMCC 5.06 与 uVision 5.43.1 各编过一遍，162/162 编译单元
  0 error 0 warning；挡在中间的只有 MDK-Lite 32KB 授权上限。（`dea25fd`、`fe19500`）
  *（该数字来自提交说明，本轮未复核。）*
- 多个固件脚本原来收尾用 `git checkout -- .`，实际会把**整棵树**的改动冲掉；
  现在统一走 `bridge_fixture.py`（按字节快照、收尾原样放回）。（`105bd78`、`683593c`）
- 鼠标灵敏度默认值 0.5 是抄来的死配置，改成算得出来的 1000 并加每帧上限。（`96dcc43`）

#### 界面 / 模块接线（孤儿设置项基线 24 → 22）

- 第 8–19 批：ClickGUI 的孤儿设置逐项接线（启动二级页、锁定框、弹药告警、缓冲能量、
  装甲盒深度边、RM 指示灯、雷达照射高亮、锁定脉冲、地图标记类型与广播开关、
  云台电压、装甲盒开关、锁定目标加分、功率保护两个告警阈值、增益时间轴、装甲盒配色）。
- 第 20 批：`RobotOverlay` 装甲盒配色三个消费点统一走助手（基线 24→23）。（`63a16ab`）
- 第 21 批：`RadarOverlay`/`Map` 接线，默认 `Both` 等于接线前的画面（基线 23→22）。（`17e98fb`）
- **设置项"有没有人读"从今天起是两层的**：`tools/audit_settings.py` 负责数字，
  孤儿基线 **22** 并做成只许减不许增的闸门（`verify_all --max-orphans 22`）。（`da8c646`）

#### 架构 / 性能（全部声明为零像素变化）

- `PolarRMWindow` 拆进 `ui/polar/` 子包：Mixin 与渲染器拆成独立模块
  （提交说明写"9 Mixin + 6 渲染器"；2026-10-01 实盘 `ui/polar/mixins/` 与
  `ui/polar/renderers/` 各 6 个 `.py`，不含 `__init__.py`——差异是提交说明的
  计数口径，本轮不追），`polar_app.py` 提交说明记 16934 → 4288 行。（`7d5f43a`）
  *（行数会随 `perf/modular-rust` 上的性能改造继续变，故只记提交说明的值。）*
- 圆角外露修复：引入 `clip_radius` SDF 裁剪管线与 jello 模块行圆角。（`a05297e`）
- gd656 移植：Heli Gimbal 锁定框/锁定加分/空中荣誉/数字滚动/文本入场，
  以及荣誉徽章/命中信息/基地推进条，并接进 ClickGUI 逐元素开关。（`fff8b9e`、`20838a7`）
- AI 推理后处理向量化、overlay 写入白名单、MQTT 自回环日志洪泛：三条都不碰画面。（`7679f01`）
- "不许动视觉"从口头约定变成**机器可校验的红线**（`tools/visual_freeze_guard.py`，
  400 个受保护文件逐字节校验 + 8 条冻结规则）。（`0071906`）
- polar_link 快照收敛成单一来源；敌方基地血量不再被当成机器人血量。（`142074c`）
- 候选包预检退出码验证加强；预检不再污染用户数据。（`c105cc8`、`55224ef`）
- 三个本机垃圾文件（临时产物/日志）挡在版本库外，`git status` 干净。（`3e37203`）

#### 本轮（2026-10-01，docs-and-tests 组）新做

- `MODULE_BACKEND_MAP.md` 重新生成（产物此前已过期 5 行）；生成器新增 `--check`
  模式与 11 个**现查**行号锚点——原来写死的 9 个行号里有 3 个已经漂到无关代码，
  重跑脚本也修不好，因为那是字符串字面量。
- `tests/smoke_test.py`：把 9 个**定义了却从未被调用**的测试函数接进 runner
  （8 个 VisionKillDetector 击杀判定分支 + 1 个 GL pixel-store 状态还原回归），
  并加一条末尾自检：任何漏接的测试函数当场 exit 1。调用数 63 → 72。
- `BACKEND_MATCH_READINESS.md` 全文对账：删掉 4 处"选 TeamBench / 按 4"的过期指引，
  换掉一个 git 历史里从未存在的测试文件引用，"357 项断言"改成实测 432 项。
- 新增 `tests/docs_and_tests_test.py`：把上面三条钉死，并列出 `verify_all` /
  `build_release` 的测试注册漏项（两处都是显式清单、不会自动发现测试；开工时
  `tests/` 下 39 个 `.py`，verify_all 注册 26 个、build_release 注册 14 个）。
  实时数字看这条测试自己打的 `[INFO]` 行，别抄本文里的数——别的组还在加测试。

### Field-chain hardening

- Isolated malformed Tongji UDP observations, made calibration/control flags fail closed, and kept the receiver alive for subsequent valid packets.
- Bounded and validated fragmented video frame assembly, including conflicting totals, duplicate chunks, gaps, in-flight floods and aggregate retained memory.
- Stopped 60 Hz demo telemetry from overwriting authoritative referee state outside `LabDebug`.
- Added a 250 ms neutral KMC heartbeat only while match control is explicitly armed in the fighting stage.
- Expired stale normal-video frames after two seconds, isolated frame ownership across video sources, and removed the fake default supply-zone assertion.

### Low-resolution UI

- Small text uses a supersampled alpha atlas, linear filtering, safe UV padding and physical-pixel snapping.
- Animated labels retain subpixel motion and settle to sharp resting positions.
- Dynamic Island icons use transparent GPU SDF circles without rectangular alpha artifacts.

### Economy workspace

- Added a standalone POLAR economy workspace for ammo, recovery, revive and air-support actions.
- Offers adapt to RMUL 3v3/RMUC 7v7, robot profile and live referee permissions.
- Added protobuf-compatible `CommonCommand` and `AirSupportCommand` publishing with QoS 1 and a 10Hz ceiling.
- Official requests wait for referee state confirmation; desktop Mock requests remain deterministic and isolated.
- Matched the workspace to the Control Center hierarchy: one treated live backdrop with independent liquid-glass modules instead of a frosted root sheet.
- Applied the reference spring, module spacing, five-percent container tint and compact interactive lens behavior.
