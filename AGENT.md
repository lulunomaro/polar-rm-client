# AGENT.md — 队内 0x5A 链路的模块化适配指南

**读者：AI 编码代理。** 本文的目标是：当电控改了 StandardRobot++ 固件的串口协议后，
你能在**只改两张表**的前提下完成客户端适配，而不是重写一遍适配层。

---

## 0. 先建立正确的心智模型

- `0x5A` 链路是**机器人自己的 USB 上位机通道**（队内 StandardRobot++ 私有协议）。
- 它与**裁判系统**（官方 `0xA5` 帧 / MQTT / `0x0301`）**并行存在，互不替代**。
  正式比赛两条同时用：血量/增益/能量走裁判系统，驾驶与本体遥测走 0x5A。
- 因此**不要**把这条链路挂到某个"场景/模式"下当开关。它是一个普通模块
  (`RobotLink`)，在 `OfficialEvent` / `CampusSimulation` / `LabDebug` / `TeamBench`
  任何场景下都可以启用。（早期版本犯过这个错，已修正，见
  `BACKEND_MATCH_READINESS.md` 的"架构定位"一节。）
- 模块与后端的对应关系（谁给这个模块喂数据、上行经过哪几个函数）见 `MODULE_BACKEND_MAP.md`，
  它是 `python tools/module_backend_map.py` 生成的；改完模块/后端记得重跑，别让文档单飞。

## 1. 协议真相在哪里：一个 JSON 文件

**`rm_client/backend/polar_protocol.json` 是队内链路的唯一协议定义。**
电控改固件后，正常情况下**只需要改这个文件**，不需要读也不需要改 Python。

已做到“改 JSON 就生效”的具体范围（均有断言）：

| 变更 | 只改 JSON？ |
|---|---|
| 改某段的字段顺序/格式串 | ✅ |
| 新增一个下行段（含位域解码）| ✅ |
| 新段字段落进状态/overlay | ✅ 写 `state_fields` |
| 改兵种限速/可否驾驶 | ✅ |
| 改驾驶键位掩码 | ✅（放宽会告警）|
| 字段需要计算/限幅/多字段合成 | ❌ 要写专用 handler |

它包含：

| 部分 | 内容 |
|---|---|
| `frame` | 帧头布局、CRC8/CRC16 参数、总长公式 |
| `downlink_segments[]` | 每个下行段：段号、名字、struct 格式串、输入字段、输出声明（含位域） |
| `uplink.robot_cmd` | 上行 0x01 布局 + `never_set`（永不置位的字段）+ **每个字段的固件消费者实况** |
| `uplink.virtual_rc` | 上行 0x03 布局、键位表、`drive_key_mask`、各键在固件里的真实含义 |
| `roles[]` / `fallback_role` | 每个兵种的底盘、限速、可否驾驶、可否控云台 |
| `firmware_build_switches` | 切兵种要改哪个头文件、`__RC_TYPE` 必须与车上遥控器配套（比赛用 `--rc dt7`）、键位记账合并等陷阱 |

代码侧只有三个文件承担协议知识：

- `polar_spec.py` —— 加载并校验 JSON（新文件，不含具体段知识）
- `polar_link.py` —— 通用编解码器，**从规格取表**；内置表仅作规格不可用时的兜底
- `robot_roles.py` —— 兵种限幅，**优先从规格取**，内置表同样只是兜底

`polar_transport.py`（串口线程/看门狗/归零）与 `manager.py`（授权/仲裁）
**与具体段无关**，电控改协议时不需要动。

### 改完规格后怎么确认生效

代码在规格坏掉时会**回退到内置表继续跑**（不能因为一个手滑的 JSON 就把链路弄砖），
所以“能跑”不等于“你的修改生效了”。两个地方会告诉你真相：

1. 自检报告 `field_evidence/polar-selftest-*.json` 里的 `protocol_spec` 字段：
   `loaded` 应为 `true`，`fell_back_to_builtin` 应为 `false`，否则看 `errors`。
2. 发布脚本会在打包前直接校验待发布树里的规格，不通过就报错中断。

```bash
python tests/polar_spec_test.py   # 含“改 JSON 确实改变行为”的回归断言
```

## 1b. 旧口径（已作废）

早期版本说“协议知识集中在 `polar_link.py` 的两张表”。现在不再是真相：那两张表已
降级为兜底，**首选是 JSON 规格**。不要再去 `polar_link.py` 里改段格式。

## 2. 新增/修改一个下行段：只改 JSON

在 `polar_protocol.json` 的 `downlink_segments` 里加一条：

```json
{
  "id": "0x2A",
  "name": "NewFirmwareSegment",
  "struct": "<ff",
  "inputs": ["alpha", "beta"],
  "outputs": [
    {"name": "alpha", "from": "alpha"},
    {"name": "mode", "from": "alpha", "shift": 0, "width": 3},
    {"name": "flag",  "from": "beta", "bit": 2}
  ]
}
```

规则：
- `id` 可写 `"0x2A"` 或 `42`；固件侧对应 `SEND_ID_*` 宏
- `struct` 用 Python struct 格式串，一律 `<` 前缀（小端）：
  `u8→B` `i8→b` `u16→H` `i16→h` `u32→I` `i32→i` `float→f` `bool→B`
- `inputs` 个数必须等于 struct 解出的值个数（校验器会查，不一致整份拒绝）
- 位域用 `{from, shift, width}` 或 `{from, bit}`
- **先核对 `sizeof(固件结构体)` 与 struct 算出的字节数**：不一致就是漏了 C 对齐填充

需要暴露给 HUD 的新字段，**也写在 JSON 里** —— 加一段 `state_fields`：

```json
{
  "id": "0x3C", "name": "NewSegment", "struct": "<fH",
  "inputs": ["pressure", "fault_bits"],
  "outputs": [
    {"name": "barrel_pressure", "from": "pressure"},
    {"name": "jam", "from": "fault_bits", "bit": 0}
  ],
  "state_fields": {"barrel_pressure": "polar_barrel_pressure",
                   "jam": "polar_barrel_jam"}
}
```

**新增一个下行段完全不需要改 Python**：没有专用解码器时走 `outputs` 通用解码，
没有专用处理器时走 `state_fields` 通用落库。这条已有回归断言看着：
`tests/polar_spec_test.py::test_new_segment_needs_no_python`。

两个安全默认：
- `state_fields` 的键必须是 `outputs` 里已声明的字段，否则整份规格被拒
  （防止拼错字段名后默默丢数据）
- 没写 `state_fields` 的新段仍计为丢弃，**不会静默产生假数据**

只有当新字段需要**计算、限幅、单位换算或多字段合成**时，才需要在
`polar_bridge.py` 里写专用 handler（专用 handler 优先级高于 `state_fields`）。

### 校验器会拦住什么

- JSON 语法错、段表为空、struct 非法、inputs 个数与 struct 不符、
  输出引用不存在的输入、位域 width 越界 → **整份拒绝 + 回退内置表**
- 把 `drive_key_mask` 放宽到 W/S/A/D 之外、或把 `fire` 从 `never_set` 移除
  → **允许加载但告警**（安全参数可以改，但不静默）

这些都有回归断言（`tests/polar_spec_test.py`）。

## 3. 表二：字段映射（段 → RobotState）

`polar_bridge.py` 把解出的段翻成 `RobotState` 上的 `polar_*` 字段，
最终由 `state.apply_polar_overlay()` 在 `tick()` **之后**统一回写。

### 三条硬规矩（违反会导致 HUD 显示假数据或被仿真值覆盖）

1. **只能写 `polar_*` 前缀的字段**。官方链路拥有的字段（如 `official_robot_health`、
   `map_robots`）不要写 —— 会互相覆盖。队内值写 `polar_ally_hp` / `polar_ground_positions`。
2. **注意容器类型**：官方侧 `official_robot_health` 是 `list`（`manager.py` 用 `[:10]` 切片），
   覆盖成 `dict` 会在渲染期抛异常。
3. overlay 只在 `polar_link_active` 为真时回写，否则让官方/仿真值说话。

## 4. 不可协商的不变量（改任何代码都必须保持）

这些是安全属性，不是风格问题。每条都有测试看着，改坏了测试会红。

1. **`fire` 恒为 0。** `encode_robot_cmd` 的签名**不暴露** fire 参数，body 里硬编码 0。
   开火权始终在驾驶员手上。不要因为"电控说支持了"就加这个参数。
2. **驾驶键位只放行 W/S/A/D。** 客户端 `RC_DRIVE_KEY_MASK`、`RC_MAX_ALLOWED_KEY_MASK`
   与固件 `VRC_DRIVE_KEY_MASK` 全是 `0x000F`。固件对 `Q`(启动摩擦轮=开火前置执行器)、
   `E`、`SHIFT`(小陀螺)、`F`(sc_flag) 有真实动作，**绝不允许**从上位机注入。
   注意“不允许”要做到**结构上不可达**，而不是仅靠调用方传 False——
   后者只要有一个遗漏的调用点就会漏。
3. **时间戳必须单调递增。** 固件的 `RECEIVE_TIME` 只在帧 `time_stamp` 严格变大时刷新，
   所以用 `PolarTimestamp`（64 位虚拟毫秒计数器 + `floor` 续接），不要直接塞 `HAL_GetTick()`
   或裸 `int(time.time()*1000)`。
4. **`bridge.active` 是驾驶前置。** 必须真的收到过机器人段才允许发驾驶指令。
   没接机器人时不活跃 = 正确的 fail-closed，不要为了测试方便绕过它。
   - ⚠ **只对 polar 0x03 那条出口。** 无线键鼠 `0x0311`（MQTT）**故意不看它** ——
     `manager.py:677-681` 逐字写着「拿 USB 链路状态当闸门会把它自己锁死」，
     因为那条出口的设计目的就是不插 USB 线也能控车。
   - ⚠ **第 167 轮：Rust 仓把这两道闸并成了一个 `drive_armed`**，
     **所以那里没有这道闸不是疏忽，是建模缺口** —— 记在
     `rmclient-rs/crates/rm-client/src/run.rs` 的 `drive_armed` 处。
     **补法是拆成两个布尔，而不是在 `drive_armed` 上加一道链路证据项。**
5. **断流要归零。** `stop()` / 急停 / deadman 超时都要先发零帧再关口，固件侧没有断流归零。
6. **`0x03` 会覆盖驾驶员真实摇杆**，所以只在驾驶已授权期间发；松杆后仅在下降沿补一次全零。
7. **云台角度只能有一个出口在发。** 同一份 `vision_aim_yaw/pitch_deg` 会被官方
   PRM1 kind=1（MQTT/图传）和队内 `0x01 RobotCmd.gimbal.*`（0x5A）两条通道读。
   单一裁决点是 `BackendManager.gimbal_egress_owner()`，返回
   `"polar" | "official" | "none"`；PRM1 侧在返回 `"polar"` 时必须跳过发送。
   为什么不能省：0x5A 侧的抢环锁出**只能看到另一个 0x5A 主控**，看不见 MQTT 这条，
   两边同时开会互相覆盖（云台抖动/乱转）。
   出口归属会出现在 `backend_stats["polar_link"]["gimbal_egress"]`，操作员看得见。
   回归断言：`tests/polar_bridge_test.py::test_gimbal_egress_is_mutually_exclusive`。
8. **键位只有两层，Tier2 已废除。** Tier1 驾驶 W/S/A/D 永远放行；
   Tier3 Q/E（摩擦轮=开火前置执行器）、F（sc_flag）、**以及 SHIFT** 永不下发。
   SHIFT 曾在 Tier2（需 “Allow Spin Key” 显式开启），现已下沉到 Tier3：
   固件对它是**边沿翻转**，客户端只能开、关不掉，再开一次反而关掉。
   `drive_key_mask()` 的 `allow_spin` 参数保留但**已无任何作用**（只为了不弄坏
   调用方）。回归断言：`tests/robot_roles_test.py::test_spin_key_tier_is_opt_in`
   （函数名沿用，语义已反转）。
9. **自转（小陀螺）只能走上行 0x04，且必须用 0x1F 回读闭环。**
   0x04 `ChassisCmd_t.spin_cmd` 是**设定**语义（0 不干预 / 1 开 / 2 关），
   刻意不提供“翻转”：客户端在收到 0x1F 之前不知道当前状态，发翻转就会
   在第二次把它关掉。三条必须保持的行为：
   - **电平重发**：每 tick 重发当前值。固件 `HOST_SPIN_CMD_VALID_MS=150ms`
     内没刷新就收回控制权，只发一次会在 150ms 后失控。
   - **不挂在 `drive_armed` 上**：0x04 不覆盖驾驶员物理遥控器（只维护
     `spin_flag`），没有 0x03 那种“冻结真实摇杆”的风险；而且“只开自转、
     不用客户端开车”是合理诉求。
   - **OFF 必须收敛到 NONE**：确认停转后就放手，否则会永久锁死驾驶员用
     物理遥控器开自转的能力。无 0x1F 回读时用 `SPIN_OFF_ASSERT_S=1.0s`
     有界压制兼底。
   客户端从未主张过自转时一律发 NONE：操作员可能用自己的遥控器开了，
   客户端无权替他关。回归断言：
   `tests/polar_bridge_test.py::test_spin_command_reaches_robot_without_drive_authorisation`、
   `::test_spin_off_is_bounded_without_firmware_readback`、
   `::test_spin_command_channel_is_independent_of_drive`。
   固件侧实现与缺口说明：`tools/firmware/host_bridge/GAPS.md`。
10. **英雄板 `s[0]` 拨杆会旁路客户端自转指令。** 麦轮 `chassis_mecanum.c:82-105`：
    下方=`CHASSIS_LOCK`(:86)、上方=无条件 `CHASSIS_SPIN`(:102)，两档都**不读
    `spin_flag`**；只有中间档(:90-97)客户端说话算数。排查“小陀螺没反应”时
    先确认拨杆在中间，再怀疑补丁。

## 5. 改完必须跑的验证

```bash
# 协议编解码 + 段映射 + 闸门 + 端到端
python tests/polar_link_test.py
python tests/polar_bridge_test.py
python tests/polar_integration_test.py
python tests/team_bench_entry_check.py

# 无头自检（不需要 GUI，能连真机器人时最有价值）
python main.py --polar-selftest --polar-enable-drive --polar-confirm-motion
```

`--polar-selftest` 会产出 `field_evidence/polar-selftest-*.json`，里面有逐段收包统计、
各段解析异常、以及 `chassis_responded`（车辆是否真的动了）。

### 与固件对拍的推荐流程

1. 让电控给出改动后的 `usb_typdef.h` 片段（结构体 + `SEND_ID_*`）。
2. 用 `sizeof` 校验你的格式串字节数。
3. 客户端发一帧，用固件侧 `Serial_Port_Assistant` 或抓包确认 CRC 与长度被接受。
   CRC 参数：CRC8 poly `0x8C` reflected init `0xFF`；CRC16 poly `0x8408` reflected init `0xFFFF`。
   可参照 `Serial_Port_Assistant/lib/CRC8_CRC16.py` 做第三方对拍。
4. 上车跑 `--polar-selftest`，把 JSON 带回来。

## 6. 明确不要做的事

- ❌ 不要新建一个"队内模式"来承载这条链路（那是本实现早期踩过的坑）。
- ❌ 不要为了跑通测试而绕过 `bridge.active`、驾驶掩码、或 fire 恒 0。
- ❌ 不要把协议字段顺序按"看起来合理"来猜 —— 必须从固件结构体逐字取。
- ❌ 不要在 `polar_transport.py` / `manager.py` 里塞协议知识，保持它们与具体段无关。
- ❌ 不要无条件 `memcpy` 覆盖 `rc_ctrl`（固件侧同理）：会冻结驾驶员摇杆。
- ❌ **不要用外部窗口截图判断界面有没有渲染。** Win32 `PrintWindow`
  （即使带 `PW_RENDERFULLCONTENT`）对 QOpenGLWidget 硬件表面经常拿到
  近乎空白的图，`CopyFromScreen` 也会跟着空白，两者一致但结论是错的。
  要验证渲染，用 `python tools/capture_live_window.py`（走
  `widget.grabFramebuffer()`，并**不能**设 `QT_QPA_PLATFORM=offscreen`）。

## 7. 当前已知的外部依赖（不是代码问题，需要人推进）

- 固件侧 `GetScCmdChassisSpeed/Velocity` 在 `master` 无消费者 → 底盘速度指令不会被执行。
  两个可选补丁（**互斥，别都打**）见 `tools/firmware/README.md`：
  - 方案 B（推荐，1 文件 +51 行）：把 `VIRTUAL_RC_CTRL.key.v` 按位 OR 进 `rc_ctrl`，
    复用固件已生效的 WASD 驾驶（自带斜坡/限幅/模式机）。
  - 方案 A（6 文件 +132 行）：新增 `CHASSIS_SC` 模式消费 `speed_vector`。
- 两个补丁都**未经编译**（本机无 Keil/ARM 工具链），必须电控编译并上板验证。
