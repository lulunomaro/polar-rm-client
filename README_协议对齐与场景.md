# POLAR-RM 协议对齐与使用场景

本文档说明当前客户端如何对齐 RoboMaster 2026 自定义客户端、串口帧、SharkDataServer 视频链路，以及如何在实验室调试、校内模拟、官方赛事三种场景下切换。

## 资料依据

- 本仓库内官方 PDF：`RoboMaster 2026 机甲大师高校系列赛通信协议 V1.3.1（20260519）.pdf`
- 本仓库内官方 PDF：`RoboMaster 2026 机甲大师高校系列赛机器人制作规范手册V1.4.1（20260421） (1).pdf`
- 本仓库内官方 PDF：`RoboMaster 2026 机甲大师超级对抗赛比赛规则手册V1.5.0（20260519） (1).pdf`
- 官方资料入口：`https://www.robomaster.com/zh-CN/resource/download/competition`
- SharkDataServer 本地参考：`SharkDataSever-main/SharkDataSever-main/proto/messages.proto`
- Shark UDP 图传参考：`SharkDataSever-main/SharkDataSever-main/js/udp-video-streamer.js`

## 启动页场景

打开客户端后先进入 POLAR 风格启动页，启动流程分为两级：

1. 选择运行场景：`DJI Official / Campus Sim / Lab Debug`。
2. 选择比赛制式：`RMUC 7v7 / RMUL 3v3`。

也可以在 `Scenario` 与 `MatchMode` 模块里重新切换。两种比赛制式共享同一套 ClickGUI、模块注册表、通信接口和 mock 安全边界，只加载不同默认 HUD、官方小地图底图和比赛状态展示。

| 场景 | 用途 | MQTT | UDP 视频 | 串口 | 默认输出 |
| --- | --- | --- | --- | --- | --- |
| `LabDebug` | 实验室离线调试、UI/算法联调 | `127.0.0.1:3333` | `127.0.0.1:3334` | `Stub / 115200` | mock |
| `CampusSimulation` | 校内比赛、SharkDataServer 模拟 | `127.0.0.1:3333` | `127.0.0.1:3334` | `Stub / 115200` | MQTT 可真实发布，串口 mock |
| `OfficialEvent` | 官方赛事网络参数预设 | `192.168.12.1:3333` | `0.0.0.0:3334` | `Stub / 115200` | mock |

官方赛事场景仍然默认 `Mock Writes = ON`。原因是现在客户端只负责协议边界和 UI/HUD，不应该在没有下位机确认、急停、心跳、裁判状态保护前直接控制真实机器人。

## 比赛制式与官方地图

| 制式 | 官方场地尺寸 | 官方底图 | 默认 HUD |
| --- | --- | --- | --- |
| `RMUC7v7` | `28m x 15m` | `assets/maps/rmuc_7v7_official.png` | 小地图、基地/前哨、敌方基地/前哨、金币、Buff、弹丸、热量、底盘功率、剩余比赛时间 |
| `RMUL3v3` | `12m x 8m` | `assets/maps/rmul_3v3_official.png` | 小地图、控制区占领、双方胜利点、队友状态、金币、弹丸兑换、热量、底盘功率、剩余比赛时间 |

小地图底图来自本地官方规则手册 PDF 渲染裁剪，不再使用手绘近似图。底图只做等比缩放、暗化和透明度适配；机器人点位、雷达高亮、地图标点、控制区/补给区提示使用 POLAR 风格动效覆盖在官方地图上。

地图坐标不直接写像素点，而是从真实场地坐标转换：

- 7v7 使用 `28m x 15m` 坐标。
- 3v3 使用 `12m x 8m` 坐标。
- `MatchMode -> Alliance = Blue` 且 `TacticalMap -> Mirror Blue = ON` 时只镜像显示，不修改原始数据。

目前地图数据来自 mock 的 `RobotState.map_robots`、`RobotState.map_markers`、`RadarInfoToClient` 接口预留位。后续接入官方通信协议时，应把 `RobotPosition`、`RadarInfoToClient`、`MapClickInfo`、`MapClickCmd` 写入这些统一字段，再由 HUD 统一绘制。

## 串口协议对齐状态

已实现：

- `rm_client/protocol.py`
- `RefereeFrameCodec`
- 官方帧结构：`SOF(0xA5) + data_length(uint16 LE) + seq(uint8) + CRC8 + cmd_id(uint16 LE) + data + CRC16`
- 自定义控制容器：`CustomControl / cmd_id 0x0311`
- `CustomControl` 载荷限制：最多 30 bytes
- `ChassisCommand.to_custom_control()`：把 WASD/QE 生成的底盘速度、功率限制、小陀螺状态编码成 30 bytes 团队本地载荷

未直接写真机：

- `SerialBridgeStub` 仍是 stub，只缓存最后一次写入。
- 真实串口桥需要后续实现 `PySerialBridge`，用 `RefereeFrameCodec.encode()` 包官方帧后写入串口。
- 下位机必须实现同一份 `CustomControl` 30 bytes 载荷解析，否则只能说明“容器对齐”，不能说明“控制语义已对齐”。

建议的真串口接入顺序：

1. 在桌面端保留 `Mock Writes = ON`，用日志确认 `cmd_id`、长度、seq、CRC。
2. 在下位机侧实现官方帧解码和 30 bytes 载荷解析。
3. 增加心跳、急停、超时归零、裁判系统状态保护。
4. 台架测试通过后再允许 `SerialBridge -> Mock Writes = OFF`。

## 自定义客户端 MQTT/UDP 对齐状态

官方自定义客户端链路与当前实现的对应关系：

| 官方/参考链路 | 当前代码 | 状态 |
| --- | --- | --- |
| MQTT `192.168.12.1:3333` | `OfficialMqttBridge` | 已留发布边界，默认 mock |
| Protobuf v3 | `OfficialMqttBridge.publish_binary()` | 待生成 Python protobuf 类 |
| `KeyboardMouseControl` | `publish_keyboard_mouse()` | 已留 topic 入口 |
| `CustomControl` | `publish_custom_control()` | 已限制 30 bytes |
| UDP `3334` 图传 | `UDPVideoReceiver` / `VideoPipeline` | 已按 Shark 8-byte header 重组 |
| HEVC 视频帧 | `VideoPipeline.last_frame` | 已重组 bytes，暂未接 HEVC 解码显示 |
| 视频转发 | `VideoPipeline.set_forward_target()` | 已留 UDP 转发接口 |

SharkDataServer 的 UDP 包头为：

```text
frame_id      uint16 big-endian
chunk_index   uint16 big-endian
total_bytes   uint32 big-endian
payload       bytes
```

当前 `FrameAssembler` 已按这个结构重组分片。后续若要真正显示 Shark/官方 HEVC 图传，需要增加 HEVC 解码器，把 `VideoPipeline.last_frame` 转为 RGB/QImage，再替换现有摄像头或合成图传。

## WASD 控制链路

当前 WASD/QE 已有传输接口：

```text
键盘输入
-> PolarRMWindow.keyPressEvent / keyReleaseEvent
-> KeyboardControlBridge.set_key()
-> ChassisCommand
-> JSON 调试 payload
-> 30 bytes CustomControl payload
-> mock 缓存或 SerialBridgeStub.write()
```

含义：

- `W/S`：前后速度 `vx`
- `A/D`：左右速度 `vy`
- `Q/E`：旋转速度 `wz`
- `SpinMode`：写入 `spin_mode`
- `PowerLimit` / `SpinMode`：写入功率和速度限制

JSON payload 用于实验室观察；30 bytes payload 用于后续对齐 `CustomControl`。

## 三种场景的目标

### 实验室调试

- 默认电脑摄像头或合成图传。
- MQTT、UDP、串口全部可不连接。
- 用于 UI、HUD、自瞄框、模块设置、键位绑定、算法 mock 调试。

### 校内模拟

- 优先连接本机或局域网 SharkDataServer。
- UDP 接收 Shark 分片图传。
- MQTT 允许模拟发布，适合校内比赛、训练赛、战术系统联调。
- 串口仍默认 mock，避免误控真实机器人。

### 官方赛事

- 网络参数预设为官方自定义客户端环境。
- 使用官方限制：载荷长度、端口、频率和安全策略后续都应在此场景集中收口。
- 默认不真实写入串口、不真实发布控制，除非队内完成完整真机验收。

## 后续给人或 AI 的改动目标

1. 生成 Protobuf：从官方/队内 `.proto` 生成 Python 类，替换 `OfficialMqttBridge.publish_binary()` 的裸 bytes 调用。
2. 接 HEVC 解码：把 `VideoPipeline.last_frame` 解码成 `QImage`，替换当前摄像头预览。
3. 接真串口：新增 `PySerialBridge`，复用 `RefereeFrameCodec`，串口默认 115200。
4. 接云台/底盘输出：把 `ArmorAutoAim`、`EnergyAutoAim`、`SpinMode` 的 mock 输出转换为下位机能解析的控制字段。
5. 增加官方赛事保护：心跳超时归零、急停、裁判阶段判断、热量/功率/射速限制、断连禁发。
6. 加回放与日志：记录输入、模块状态、目标框、MQTT topic、串口帧，方便实验室复盘。

## 当前边界结论

- 串口“帧结构”已经可以和官方附录对齐。
- 自定义客户端 MQTT/UDP“端口、topic 边界、UDP 分片结构”已经可以和官方/Shark 链路对应。
- 视频“接收重组”已经有接口，真正 HEVC 解码显示还没完成。
- 控制“容器”已经对齐到 30 bytes `CustomControl`，但具体语义仍需队内下位机同步。
- 所有真实控制输出默认 mock，这是当前版本正确的安全状态。
