# 后端比赛实战就绪审计

审计日期：2026-08-14（**2026-10-01 做过一次全文对账**，见下方"对账记录"）
分支：`deepseekv4flash后端`（审计当时的分支；本轮对账在 `perf/modular-rust`）

## 结论

**NOT READY FOR REAL MATCH**

本轮已经修复可由桌面代码证明的 P0/P1 缺陷，但真车、裁判系统、正式图传发射端和队内机器人固件尚未形成现场闭环。`rm_client.backend.readiness.audit_backend()` 继续 fail-closed；缺少任一现场证据时绝不会报告 READY。

## 2026-10-01 对账记录（本文自身的可信度）

这份审计写于 2026-08-14，主体结论（NOT READY）不变，但**正文里有几处已经与代码对不上**，
照着做会浪费时间。本轮逐条核过并改掉的：

| 原文 | 问题 | 现在 |
|---|---|---|
| 「新场景 TeamBench（启动页第 4 项 / 快捷键 `4`）」 | `TeamBench` 早已删除，本文"架构定位"一节自己就写了"已彻底删除"——同一篇里自相矛盾 | 标为作废并说明 `RobotLink` 现在是普通模块 |
| 「TeamBench 场景下 `allow_mqtt_publish=False`…」 | 同上，是被删掉的旧做法 | 标为作废，指向"架构定位"一节 |
| 「启动页第 4 项（或按 `4`）选队内台架/实车（TeamBench）」 | 启动页根本没有第 4 项，照做选不到 | 改成三个场景任选其一（键 1/2/3） |
| 「验证见 `tests/team_bench_entry_check.py` 第 5 节」 | 该文件在本仓库 git 历史里**从未存在** | 换成三个真实存在的测试与函数名 |
| 「共 357 项断言」 | 已过期且无法复核 | 2026-10-01 实测 **432 项通过**（81+184+73+51+43），并写明复核命令 |
| 「verify_all 从 33 组涨到 34 组，全部 GO」 | 清单已涨到 54 组；"全部 GO"是 2026-09-03 的结论 | 改成实测组数，并注明本轮没跑 verify_all、不给 GO/NO-GO |
| 「确认 `Allow Drive` 为开（由场景写入）」 | 与本文"架构定位"那张表现状相反（现在由模块设置项决定，切场景不覆写） | 改成"由模块自己的设置项决定" |
| 「拆掉全部**六个**场景耦合点」 | 紧接着的表里是七行 | 改成七个 |

仍然待确认 / 未修（不要当已解决）：

- **本文其它段落里引用的固件行号**（`usb_task.c:692-694`、`chassis_omni.c:171-217` 等）
  本轮**没有重新核对** —— 那些行号指向的是**外部固件仓库**（StandardRobotpp `master` @ `2722e0d`），
  本地没有那份工作树，无法验。"2026-09-03 电控侧收口"那一节里写的
  `162/162 编译单元 0 error 0 warning` 同理，是提交说明记载，本轮未复核。
- `tools/verify_all.py` 与 `tools/build_release.ps1` 的注册清单都有漏项
  （本轮开工时实测：`tests/` 下 39 个 .py，verify_all 只注册 26 个、
  build_release 只注册 14 个；之后别的组还在加测试，实时数字看
  `python tests/docs_and_tests_test.py` 打的 `[INFO]` 行），
  清单化与防倒退闸门见 `tests/docs_and_tests_test.py`；补注册要改这两个文件，
  不在本轮可改范围内。

## 2026-08-12 已修复

- 解码器附加的 `__present__` 元数据不再进入 `int()`；单条坏裁判消息有异常隔离和错误统计，不会中止该帧后续消息。
- `robot_health` 按官方 5+5（1/2/3/4/7）映射，且依据本机阵营构造双方 ID。
- `Event.param` 支持官方逗号格式；数值比赛阶段统一映射，Hero 吊射不再因 `MATCH`/`4` 双写被锁死。
- 协议层标记为 V1.3.1：`MapClickInfo` 是下行，`MapClickCmd` 是上行；哨兵控制值修正为 0/1。
- 键盘 16 位、鼠标三轴和三键进入官方 `KeyboardMouseControl` MQTT 链路，按 75 Hz 门控；按键释放会可靠重试。
- F12 先尽力发布全零 KMC，再锁止全部上行；`Ctrl+Shift+F12` 仅用于人工确认后的解锁。
- 正常退出会先发送全零 KMC，再发布 retained offline；场景/客户端 ID 重连不会发布可能与新连接竞争的离线状态。
- 115200 串口明确拒绝 `0x0311`。CustomControl 只经官方 MQTT/图传链路发布。
- 自瞄提供显式、版本化的 14 字节 `PRM1 kind=1` 队内 CustomControl 载荷；桌面端永不置 fire 位。
- 小陀螺提供 12 字节 `PRM1 kind=2` 启停/方向/速度载荷；`AutoSpinOnHit` 的裁判受击判定可驱动同一路径。两者默认 Mock，必须在机器人固件实现同一 schema 和超时归零后手动启用。
- 官方场景下经济请求默认走 MQTT。确认只接受本机弹量、血量、复活或支援状态变化，团队金币变化不再作为成功依据；超时不自动重试，避免 QoS1 重复扣款。
- UDP/串口/同济接收线程退出后清除 running；YOLO 加载失败有退避；Qt 逻辑阶段逐项隔离异常。
- H.264/H.265 解码支持系统、发布包内置或 `imageio-ffmpeg`，普通 HEVC P/B slice 在已锁定流中不会被误丢。
- 配置版本非法时安全返回；保存采用临时文件、`fsync` 和原子替换。
- 自动保存已做 250 ms 合并，拖动滑块不再逐事件全量写盘；运行时每秒执行 fail-closed readiness audit 并写入 `RobotState.backend_stats["readiness"]`。
- 发布脚本只打包 Git 跟踪内容，校验核心 backend/Hero/Jello/Minecraft 文件、默认 JSON 和 Python 编译，并内置 ffmpeg；不会打包工作区中用户修改的 `configs/default.json`。

## 2026-08-14 收口

- 同济 UDP JSON 入口现在验证有限数值、尺寸、时间戳和观察字段；单个坏目标会被丢弃，坏包有计数且不会终止接收线程，下一包可继续恢复。校准、控制、开火和锁定布尔值按 fail-closed 解析，缺失校准声明不能解锁控制语义。
- `FrameAssembler` 校验每帧 `total_bytes` 一致性、连续分片索引、冲突重复与精确总长；单帧、分片数、同时在途帧和 64 MiB 聚合缓存均有硬上限并可观测丢弃。
- `RobotState.tick()` 的桌面模拟仅在 `LabDebug` 工作；正式/校内裁判消息到达后不会再被 60 Hz Mock 覆盖，首条地图标记和低频 HUD 数据可跨帧保留。
- 未知的补给区位置不再默认伪造为 `True`。普通兑换仍交由裁判服务器按规则最终判定；只有未来获得权威区域状态时，客户端才执行本地位置门控。
- 只有处于权威比赛阶段且已授权的比赛控制，才会在无输入时每 250 ms 发布一次全零 KMC；`LOCKED/QUALIFICATION/ESTOP` 不由普通输入桥发送心跳，退出、断线和急停的独立归零路径保持不变。
- 普通 UDP 图传连接状态按新鲜解码帧复位；超过 2 秒后主动释放旧 MJPEG、GPU 上传键和液态玻璃源快照并显示断流状态。Webcam、UDP、Native 与 Synthetic 各自持有显示帧，切源不再继承旧纹理；Hero 的 Native 独占/禁止回退策略不变。
- 本机指定 FFmpeg（SHA-256 `79BDD4...EB7`）完成 H.264/HEVC 持续开管、首帧与下一参数集/关键帧恢复测试；该结果仍不能替代正式发射端现场验证。

## 2026-09-03 电控侧收口（DT7 比赛链路 + 驾驶键位账本 + 真工具链取证）

结论仍是 **NOT READY FOR REAL MATCH**：桌面与固件两侧能自证的部分都做完了，
缺的还是硬件（真车、真对频、真图传）。今天新增的证据：

- **遥控器类型对齐**：比赛链路是「DT7 遥控器 → 2.4G → 车上 DR16 → UART3 的 18 字节
  SBUS」，而仓库出厂写 `RC_ET08A`，已做成 `prepare_hero_build.py --rc dt7`（默认值，
  原始值留在行尾注释里，可来回切）。**顺带纠正一处我之前说重的表述**：DT7 的 18 字节帧
  是按*帧长*分派、与 `__RC_TYPE` 无关（`remote_control.c:252` 无条件进 `sbus_to_rc()`），
  所以「用 DT7 键鼠开车」一直是通的、没有坏过；这个宏配错真正致命的是**反向情形**
  ——车上是 25 字节遥控器而宏选错那一家，`#if` 链全不成立、整帧不解析。
  配 et08a 跑 DT7 的实际损失只有 `GetRcOffline()` 恒真，而它的下游（板间广播、
  平衡底盘错误位）对麦轮英雄这台车几乎无感（`chassis_task.c:98` 是 `__weak` 空实现、
  离线警报曲 `music_task.c:38` 默认关）。详见 `tools/firmware/host_bridge/RC_LINKS.md` 第 2 节。
- **驾驶键位改成记账合并**：`key.v` 的三个写入者各记一本账（驾驶员 / 客户端），
  谁写都写并集。修掉两个方向相反的缺陷：纯 `|=` 在无键盘遥控器上**松键粘住**
  （车刹不住），DT7 的整字段覆盖又会**抹掉客户端键位**并让 SHIFT 边沿乱跳。
  新增 8 组可执行行为断言 + 阴性对照（旧写法被抓到 4 处）。
- **真工具链**：ARMCC 5.06 命令行与 uVision 5.43.1 各编过一遍，162/162 编译单元
  0 error 0 warning；挡出 hex 的只剩 MDK-Lite 32KB 授权上限（L6050U）。
  dt7 与 et08a 两种配置都编过，`--rc` 来回切不会污染工作树。
- **离线回归**：`tools/verify_all.py --firmware` 从 33 组涨到 **34 组，全部 GO**（这一句记的是 2026-09-03 的状态；2026-10-01 重新数了一遍清单，已涨到 **54 组** = 26 个 Python 套件 + 26 个 C 编译/行为校验 + 1 个孤儿数审计 + 1 个固件准备自检。逐组 GO 需要真跑一次 `python tools/verify_all.py`，**本轮没有跑**，故不给 GO/NO-GO 结论）。

仍然只有硬件能证明的事：DT7 与 DR16 对频后的摇杆方向与中值、`s[0]` 三档实际行为、
客户端与驾驶员同时按键时的手感、以及 0x1E/0x1F 回读在真车上的时序。

## 架构定位：0x5A 是**与裁判系统并行的独立通道**，不是另一个模式

早期实现错把队内链路挂在 `TeamBench` 场景下（`if key == "TeamBench": polar.start()`、
`link.enabled = key == "TeamBench"`、HUD 面板也按场景名显示）。那个前提是错的：
**正式比赛本来就要同时用两条通道** —— 血量/增益/能量来自裁判系统（MQTT/0x0301），
驾驶与机器人本体遥测来自它自己的 USB（0x5A）。挂在单一场景下等于
**选了大疆官方就开不了车**。

现在已拆掉全部**七个**场景耦合点（后三个是拆完前四个后，由集成测试补暴露出来的；2026-10-01 对账时数了下表头，这句原先写的是"六个"，但表里明明是七行）：

| 位置 | 旧行为 | 现行为 |
|---|---|---|
| `manager.py` 启动 | 仅 TeamBench 起 transport | 无条件起；端口为 `""`/`Stub` 时 transport 自己拒连 |
| `manager._retry_polar_connect` | 受场景 `allow_polar_drive` 短路 | 只看“用户是否配了真端口” |
| `scenarios.py` 模块启用 | `enabled = (key==TeamBench)`（其他场景强制关） | 只在 TeamBench 顺手打开，**其他场景不强制关** |
| `scenarios.py` Port / Allow Drive | 每次切场景都覆写（官方场景写回 Stub/False） | 仅当场景自带真端口时才预置；驾驶权限不随场景覆写 |
| `manager._apply_config` | `polar.mock_writes = not allow_polar_drive` | 默认 fail-closed，由模块 `Mock Writes` 打开 |
| `manager._configure_polar_link` | `allow_drive = config.safety.allow_polar_drive` | `allow_drive =` 模块的 `Allow Drive` 设置项 |
| `module_list.py` HUD 面板 | 场景名为 TeamBench 才显示 | 配了真端口或链路活跃就显示 |

> 最后两行才是**真正致命的那两个**：只拆前四个的话，大疆官方场景下 `mock_writes`
> 仍为 True、`allow_drive` 仍为 False，机器人**一帧都收不到**且没有任何报错。
> 是 `tests/polar_integration_test.py::test_drive_works_in_official_scenario` 把它扳出来的。
> 任何把驾驶重新绑回场景名的改法，都会让这条测试红。

`TeamBench` **已彻底删除**，启动页回到原本的三个场景。原因：把队内链路做成
一个专属模式本身就是错的思路 —— 它会让“选了大疆官方”看起来像“不能开车”。
现在 RobotLink 是一个普通模块，端口/波特率/频率/驾驶授权全部由它自己管，
在三个场景下行为一致且随配置持久。

> 删除时发现一个会**当场崩溃**的回退：启动页两条渲染路径与按键表仍按 4 个键
> 枚举，而 `SCENARIOS` 里只剩 3 个 —— 渲染时对不存在的键抛 `KeyError`。
> 已全部回退到 3 个键，并加了守卫断言 `_references_retired_scenario()`：
> 任何渲染/按键代码引用不存在的场景，测试直接红（这个问题在全部单元测试
> 绿着的情况下存在，只有真实启动才会爆）。

> 同时补上两条“两通道并行”的直接证据断言：官方场景下经济链路与官方 MQTT
> **仍为真发**，同时 RobotLink 可用。早期专属场景方案下这两者是互斥的。

驾驶仍由五道独立闸门把关，一道都不能省：`Allow Drive` → `Client Chassis Drive` →
`module.enabled` → 未被急停 → `bridge.active`（**必须真的收到过机器人段才允许发驾驶指令**，
没接机器人时不活跃是正确的 fail-closed）。验证见
`tests/polar_integration_test.py::test_drive_works_in_official_scenario`（官方场景下
逐项拆闸门并跑完整回环）、`tests/polar_integration_test.py::test_unpatched_robot`
（链路不活跃时禁止驾驶）与 `tests/polar_bridge_test.py::test_manager_gate_requires_three_switches`
（manager 侧三开关的判定）。

> ⚠️ 本段原先写的是 `tests/team_bench_entry_check.py` 第 5 节 —— 那个文件
> **在本仓库的 git 历史里从来不存在**（`git log --diff-filter=A -- tests/team_bench_entry_check.py`
> 无输出），照着找的人只会浪费一轮。2026-10-01 对账时改成上面这三个真实文件。
> 原句里"证明 `_retry_polar_connect` 真的执行到了连接"这半句仍然成立：那个方法
> 还在 `rm_client/backend/manager.py`，由 manager 的 tick 调用，
> `test_drive_works_in_official_scenario` 里的断言"官方场景下链路能自动连上"
> （`polar_drive_active is True`）就是它的结果。

## 仍阻止实战签字的现场项

1. 正式 MQTT 下行、75 Hz KMC/CustomControl 上行和机器人侧超时归零未做真车全链路测试。
2. `PRM1` 队内 CustomControl schema 需要机器人固件实现、代码评审和台架验证；Hero 仍只做决策辅助，绝不自动开火。
3. 正式 UDP HEVC 与 Native `0x0310` 发射端尚未覆盖丢包、乱序、SPS 恢复、分辨率切换和解码器崩溃。
4. 经济协议没有逐请求官方 ACK；当前采取“本机结果字段确认 + 禁止自动重试”的保守策略，仍需裁判系统实测。
5. 相机标定、Hero yaw/pitch/炮口/初速、部署模式生命周期、小陀螺误触发均需隔离场地验证。
6. 需要完整赛程的 P50/P95/P99 延迟、CPU/GPU/显存、队列、网络断链和热稳定 soak。
7. 需要操作员和安全负责人保存审计 JSON、日志与现场签字。

## 最低现场证据

- `video_source` 明确为 `udp` 或 `native_0310`，并持续产生新解码帧。
- `codec_field_test`、`estop_robot_test`、`command_ack_test`、`control_heartbeat_test`。
- `robot_firmware_schema`：固件正确解析 `PRM1 kind=1`，并证明 fire 位保持 0。
- `spin_firmware_schema`：固件正确解析 `PRM1 kind=2`，并证明启停、方向、速度和失联超时归零。
- `projectile_hit_spin_test`、`hero_deploy_status_test`、`hero_lob_live_test`。
- `loss_recovery_test`、`latency_soak_test`、`field_signoff`。

软件测试通过不能替代上述现场证据。

---

# 队内 StandardRobot++ 0x5A 链路对齐

对齐对象：https://gitee.com/SMBU-POLARBEAR/StandardRobotpp （`master` @ `2722e0d`，最后一次代码推送 2026-04-08）。

## 已实现并离线验证

- `rm_client/backend/polar_link.py`：0x5A 帧（4 字节帧头 `sof/len(u8)/id(u8)/crc8` + `u32 time_stamp` + 载荷 + CRC16），14 个**固件当前真的在发**的下行段解码，上行 `RobotCmd` 编码。CRC 与队内 `Serial_Port_Assistant/lib/CRC8_CRC16.py` 做过 2000 组随机数据逐字节对拍。
- `rm_client/backend/polar_transport.py`：串口收发、本地看门狗、急停/退出前归零突发帧、时间戳单调契约。
- `rm_client/backend/polar_bridge.py`：段→HUD 映射、云台控制仲裁、指令-实测交叉校验。
- `rm_client/serial_ports.py`：COM 口动态探测与 `Auto` 选择（只认可带 VID/PID 的 USB 设备）。
- ~~新场景 `TeamBench`（启动页第 4 项 / 快捷键 `4`）~~ **已作废**：`TeamBench` 场景在
  2026-09 前后被**整个删掉**了，启动页是三个场景（见上文"架构定位"一节）。
  留下的是 `RobotLink` 模块与 HUD `LNK/DRV/GIM/MOT` 芯片与诊断行 —— 这两样不受影响。
  （删除前本条写的是一个已经不存在的场景入口，与上文"已彻底删除"那段直接打架，
  而且照着做的人在启动页根本找不到那一项。2026-10-01 对账时划掉。）
- 验证：`tests/polar_link_test.py`、`tests/polar_bridge_test.py`、`tests/polar_startup_test.py`、`tests/polar_integration_test.py`、`tests/polar_selftest_test.py` —— **2026-10-01 实测 432 项通过**（81 + 184 + 73 + 51 + 43，命令是逐个跑 `python tests/<name>.py` 数 `[PASS]` 行）。
  本文原先写的"共 357 项断言"已经过期，且无法从文中复核，故改成实测值 + 复核命令；
  `api_freeze_test` 与基线零差异；`smoke_test` 通过（2026-10-01：72 个测试函数全部被调用）。
- 上述四个 polar 测试与 `api_freeze_test` 已加入 `tools/build_release.ps1` 的发布门禁（`Invoke-RequiredTest` 使用显式清单、不会自动发现测试，以前 `api_freeze_test` 也不在清单里）。
- `tests/polar_integration_test.py` 用 Python 复刻了 StandardRobot++ 上位机接口的行为语义（虚拟机器人），跑完整回环：连接 → HUD 转真 → 按 W 机器人收到 1.5 m/s → 0x1E 回读 → MOT 变 OK → F12 全零 → 拔线判不活跃。它同时跑“**已补底盘消费者**”和“**未补（当前 master 真实行为）”两种机器人，后者必须报 `NO_MOTION → STUCK` 而不是假装正常。

## 与官方链路的关系

两条链路**结构不兼容**，不是配置差异：官方裁判帧 SOF `0xA5`、帧头 5 字节、`len` 为 u16、`cmd_id` 为 u16 且在帧体内；队内帧 SOF `0x5A`、帧头 4 字节、`len` 为 u8、段 id 为 u8 且在帧头内、数据段前置 `u32 time_stamp`。CRC8/CRC16 算法两者逐位相同，因此复用同一实现。

> **曾经**是这样：专属场景 `TeamBench` 下 `allow_mqtt_publish=False`（官方上行保持 mock），
> 驾驶能力位 `allow_polar_drive` 仅该场景为真，两条链路因此不同时争抢控制。
> **现在这条描述已作废** —— `TeamBench` 场景已删除，0x5A 链路是普通模块
> `RobotLink`，官方场景与队内链路**本来就并行**（血量/经济走裁判 MQTT，
> 驾驶与本体遥测走 0x5A），见上文"架构定位"。写在这里是为了不让人误以为
> "两条链路要靠切场景来互斥"，那正是被删掉的做法。

## 阻止“操控车辆”真正生效的固件侧阻塞项

客户端已能正确发出指令，但下位机当前**没有消费者**，因此车不会动。需电控完成：

> 上述前两项（底盘消费者 + 断流超时归零）已写成可直接应用的补丁：
> [`tools/firmware/polar-chassis-sc.patch`](tools/firmware/polar-chassis-sc.patch)，说明见
> [`tools/firmware/README.md`](tools/firmware/README.md)。
> 该补丁在基线 `2722e0d` 上 `git apply --check` 通过，并已逐符号追 include 链做静态审查；
> 但**本机无 ARM/Keil 工具链，未经任何编译**，必须电控在 Keil 里完整编译（工程开了
> `--diag_error=warning`，警告即错误）并上板验证后才能用。

> **后续深查修正（重要）**：固件里**本来就有一套已生效的 WASD 驾驶** ——
> `chassis_omni.c` 的 FOLLOW 分支（`:171-217`）与 SPIN 分支（`:240-293`）读
> `rc->key.v & KEY_PRESSED_OFFSET_W` 直接产生带斜坡与限幅的速度，
> `chassis_mecanum.c:157-160` 同理；只是它读的是**物理遥控器**键位而非上位机。
> 而上位机注入该位图的通路已铺到最后一公里：`usb_task.c:692-694` 已接收 0x03 帧、
> `:1225` 已 memcpy 进 `VIRTUAL_RC_CTRL`、`:272` 已 Publish，**只差接入 `rc_ctrl` 一步**。
>
> 因此存在一条约 **1–10 行**的替代路线（方案 B），它直接复用固件既有的斜坡、
> 低通、限幅与模式机，比本补丁（6 文件 +132 行）改动小、风险低。
> 客户端已实现 0x03 编码（`encode_virtual_rc`），其 `key.v` 位序与官方
> `keyboard_value` **逐项一致**（有断言看着），并保证**只在驾驶已授权期间发 0x03**
> —— 否则全零 0x03 会冻结驾驶员真实摇杆。两方案对比与安全红线见
> [`tools/firmware/README.md`](tools/firmware/README.md)。
>
> 仍需注意：`__RC_TYPE` 必须与车上接收机配套。比赛配置是 **DT7 遥控器 + DR16 接收机**
> ⇒ `RC_DT7`（18 字节帧自带键盘位与鼠标位移），由
> `prepare_hero_build.py --rc dt7`（默认值）负责切；仓库出厂写的 `RC_ET08A`
> 配 DT7 硬件会让 `GetRcOffline()` 恒真、驾驶员键位鼠标全丢。
> 键位现在是**记账合并**（`HOST_RC_DRIVER_KEYS | HOST_RC_HOST_KEYS` 写回 `key.v`），
> 置零宏不再清 `key.v`，所以**依然不能拿“key.v 非零”当接管判据** —— 要看 0x03 的
> 150ms 新鲜度（`usb_task.c` 的 `VRC_FRESH_TIMEOUT_MS`）。详见
> [`tools/firmware/host_bridge/RC_LINKS.md`](tools/firmware/host_bridge/RC_LINKS.md)。

| 阻塞项 | 证据 | 影响 |
|---|---|---|
| `GetScCmdChassisSpeed` / `GetScCmdChassisVelocity` 在 `application/` 下零调用者 | `usb_task.c:1275` / `:1297` 仅定义；历史上 `v1.7.33-RMUL2025` 的 `chassis_omni.c:231-233` 有 `CHASSIS_NAVI` 消费，v1.9.0 重写时被删 | **WASD 底盘完全不动** |
| 无断流归零：`GetCmdData()` 每 1ms 无条件拷贝，`USB_OFFLINE` 分支不清数据 | `usb_task.c:282` / `:285-287` | 客户端崩溃或拔线后**机器人保持最后指令**（安全项，必须修） |
| `GetScCmdChassisAngle` 只有声明无实现 | `supervisory_computer_cmd.h:29` | 谁调用谁链接失败 |
| `GetScCmdFricOn` / `GetSCcmdtracking` 零调用者 | `usb_task.c:1333` / `:1343` | 摩擦轮与 tracking 无法由上位机驱动；且 `fire` 不隐含摩擦轮（`shoot_fric_trigger.c:100-116`），**只发 fire 不会开火** |
| `ChassisPublish()` 仅平衡底盘实现 | `chassis_balance.c:122`；omni/mecanum 落回 `chassis_task.c:86` 空桩 | `0x1E RobotMotion` 回读恒空（`usb_task.c:1186-1188` 直接 return）。**客户端已改用 `0x0A` 测速模块坐标的位移做兜底验收**，因此缺 0x1E 不再等于“无法验证车是否动”。**2026-09-03 更新：已可补**——`tools/firmware/motion_feedback/` 速度回读桥用实测轮速反解机体系速度（与下发式同系数，运动学往返已验证），`prepare_hero_build.py` 自动套；打完桥后反馈为 2ms 控周期实测速度，未打桥的固件仍走 0x0A 兜底 |
| `UsbSendBuffData()` 漏填 `cooling_buff` | `usb_task.c:948-958` | 冷却增益数值不可信（客户端已只记录不驱动 HUD） |
| `LATEST_RX_TIMESTAMP` 仅在下位机 `UsbInit` 清零，而 `RECEIVE_TIME` 只在帧 `time_stamp` 严格变大时刷新 | `usb_task.c:699-702` | 上位机时间戳回退会**永久判离线，重新插拔不恢复**。客户端已用 epoch 毫秒单调源规避，仍建议固件改为纯 `HAL_GetTick` 判活 |

客户端侧已做的对应缓解：本地看门狗 0.2s 过期归零、F12 同帧锁链路、退出前归零突发帧、`MOT` 芯片与 `NO_MOTION/STUCK/NO_FEEDBACK` 琥珀告警（可在 1.2s 内指出“在发速度但车不动”）。但这些**防不了客户端自身崩溃或拔线**，固件超时归零不可省略。

## 需与固件作者确认的 4 点

1. `vx/vy` 取机体系还是云台系：`doc/API.md:849-857` 写“底盘坐标系”，但历史 `CHASSIS_NAVI` 分支乘了 `cosf(chassis.yaw_delta)`，两者差一个偏航旋转。
2. 各轴符号方向（协议只给单位）：落地前逐轴单发 `+0.2` 观察车体。
3. `chassis.sc_flag`（F 键翻转、当前无人读取）挪作“上位机驾驶开关”是否与其原始意图冲突。
4. `CHASSIS_RC_MAX_VELOCITY = 2.0 rad/s` 注释标注“仅用于无云台模式”，作为上位机 wz 限幅是否偏小。

## 实车启动清单

### 第 0 步（建议）：先跑一次无头链路自检

不启 GUI、不动机器人，先把“连不连得上、14 段对不对、机器人听不听话”跑成一份 JSON：

```powershell
# 只读：连上、收 3 秒下行、报告哪些段到了、CRC 有无错误、部件健健
python main.py --polar-selftest --polar-port Auto --polar-seconds 3

# 运动回环：必须两个开关同时给，且机器人需在架子上或安全区
python main.py --polar-selftest --polar-enable-drive --polar-confirm-motion
```

安全设计：默认只读；运动脉冲限幅 0.30 m/s / 0.8 s、总预算 ≤ 2.5 s，无论成败异常都先归零再关口；**既没有 0x1E 速度回读、也没有 0x0A 测速坐标**时，或有部件错误、或未确认机器人身份时，**直接拒绝发运动指令**（避免盲开）。`fire` 全程为 0。

运动验收证据有两条路径，自动选择：优先 `0x1E` 实测速度；缺它时改用 `0x0A` 测速模块的**位移**（阈值 0.05 m）。报告里 `evidence` 字段会标明用的是哪一条（`0x1E` 或 `0x0A-position`）。这对全向轮步兵/麦轮英雄尤其关键 —— 它们没有速度观测器，若只依赖 0x1E，自检永远只能报 NO_FEEDBACK。

输出落在 `field_evidence/polar-selftest-<ts>.json`，退出码有含义：`0` 全通过、`2` 连不上、`3` 必需段缺失、`4` **机器人收到速度但未响应**、`5` 被安全门拦下。

报告里 `authoritative: False` 是**刻意**的：自检结果只是证据草稿，不能绕过 `QualificationManager` 的 HMAC 与双人署名去铸造正式现场证据。

### GUI 启动流程

1. 双击 `启动.bat`。若提示 `.venv` 不可用，按屏上给出的两条命令为可用解释器装一次依赖后重跑。
2. 启动页选场景（`OfficialEvent` / `CampusSimulation` / `LabDebug` 三个里的任意一个，键 `1`/`2`/`3`；**没有第 4 项**），再选赛制。选哪个都行 —— RobotLink 是普通模块，不靠场景开关。
3. 打开 ClickGUI（右 Shift / F8）→ System → **RobotLink**：`Port` 选 `Auto` 或显式 COM 号；确认 `Allow Drive` 为开（**由模块自己的设置项决定，切场景不再覆写它** —— 本条原先写的是"由场景写入"，与上面那张表里 `manager._configure_polar_link` 的现状正好相反，2026-10-01 对账时改正）。
4. 插上机器人 USB。HUD 右上 `RMIndicatorsHud` 的 **`LNK` 变绿**、底行显示 `ROBOT COMxx · N seg`。连不上时底行会是琥珀色 `LINK OFF · <状态码>`，悬停看中文原因与已探测到的端口清单。
5. 开驾驶：勾 `Client Chassis Drive` → `DRV` 变绿。
   驾驶键是**可改绑**的：ClickGUI → RobotLink → `Bind W/S/A/D …`（默认等于历史物理键）。
   改绑只改变“哪个物理键触发哪个协议槽”，**不改变上位机下发的槽位**，
   因此固件语义与驾驶键掩码都不受影响；绑定会随配置 save/load 存活，
   且有端到端断言看着（`tests/polar_startup_test.py::test_rebind_works_through_real_window_and_persists`）。
6. 云台：勾 `Client Gimbal Control`；英雄板需 `tools/firmware/gimbal_host/` 云台直控桥（打好后 s[0] 用**中间档**：底盘听自转指令、瞄准听上位机；没打桥仍是只能上档、且上档强制自转）；**视觉 NUC 自瞄在跑时请保持 `Vision NUC Occupied` 为开**，客户端会自动让位；若客户端发出角度而云台不收敛，会判为抢环并锁出（`GIM` 灭），需手动清除锁出。
7. 按 W 试车：`MOT` 应在 1.2s 内变绿。若底行出现 `NO_MOTION` / `STUCK`，说明**固件底盘消费者未接线**（见上表）；若出现 `NO_FEEDBACK`，说明 `0x1E` 回读缺失，无法验证车辆响应。`NO_MOTION` 从第一帧就带原因文字，不会只给一个无信息量的黄字。
8. `fire` 由客户端**恒置 0**（`encode_robot_cmd` 签名不暴露该参数，有不变量测试），开火权始终在驾驶员手上。
9. 收工：F12 急停或直接关窗，客户端会先发归零突发帧再关闭端口。断线超过 2 秒链路自动判为不活跃并禁止驾驶。
