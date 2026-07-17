# RM 自定义客户端接口与需求说明

## WASD 是否已经有传输接口

有。当前版本新增了键盘控制桥接层：

- 文件：`rm_client/control.py`
- 类：`KeyboardControlBridge`
- 命令结构：`ChassisCommand`
- 接入位置：`rm_client/ui/polar_app.py`

默认行为是 mock 输出，不会真实控制机器人。也就是说，按下 `W/A/S/D` 会生成底盘控制命令和 payload，但默认只保存在客户端内部，不会发给真实串口设备。

## WASD 数据流

```text
键盘按下/松开
-> PolarRMWindow.keyPressEvent / keyReleaseEvent
-> KeyboardControlBridge.set_key()
-> ChassisCommand
-> JSON payload
-> mock 缓存或 SerialBridgeStub.write()
```

## 键位映射

| 键位 | 当前含义 | 输出字段 |
| --- | --- | --- |
| W | 前进 | `vx = +1` |
| S | 后退 | `vx = -1` |
| A | 左移 | `vy = -1` |
| D | 右移 | `vy = +1` |
| Q | 左旋预留 | `wz = -1` |
| E | 右旋预留 | `wz = +1` |

斜向移动会自动归一化，避免 `W+D` 这种组合导致速度超过 1。

松开按键时也会重新发送一次命令。例如松开 `W` 后，如果没有其他移动键按下，会生成 `vx = 0, vy = 0, wz = 0` 的停止命令。

## 当前 payload 格式

`ChassisCommand.to_payload()` 当前输出 JSON bytes，协议名为：

```text
rm_control.chassis.v1
```

示例：

```json
{
  "seq": 1,
  "timestamp": 1783000000.0,
  "vx": 1.0,
  "vy": 0.0,
  "wz": 0.0,
  "spin_mode": true,
  "speed_limit": 420,
  "power_limit": 70,
  "mock_output": true,
  "source": "keyboard",
  "protocol": "rm_control.chassis.v1"
}
```

## 安全策略

真实机器人控制默认关闭。

当前 `SerialBridge` 模块里有 `Mock Writes` 设置，默认是 `ON`。此时：

- WASD 会生成控制命令。
- 命令会保存在 `KeyboardControlBridge.last_command`。
- payload 会保存在 `KeyboardControlBridge.last_payload`。
- 不会写入真实串口。

后续如果要接真实机器人，必须同时完成：

- 替换 `SerialBridgeStub` 为真实串口桥。
- 明确关闭 `SerialBridge -> Mock Writes`。
- 在下位机侧实现 `rm_control.chassis.v1` 或替换为正式二进制协议。
- 加入急停、限幅、心跳超时、裁判系统状态保护。

## 已有接口清单

### Module

文件：`rm_client/model.py`

每个功能模块都有：

- `name`
- `category`
- `enabled`
- `keybind`
- `settings`
- `on_enable`
- `on_disable`
- `on_tick`
- `on_frame`
- `on_message`

用途：统一管理 ArmorAutoAim、EnergyAutoAim、SpinMode、HUD、CameraFeed、SerialBridge 等功能。

### KeyboardControlBridge

文件：`rm_client/control.py`

用途：

- 接收 WASD/QE 键位状态。
- 生成 `ChassisCommand`。
- 默认 mock。
- 后续可接串口、MQTT 或其他控制链路。

### SerialBridgeStub

文件：`rm_client/transports.py`

用途：

- 保留串口写入接口。
- 当前只保存 `last_write`。
- 后续替换为真实 `pyserial` 实现。

### MQTTTransport

文件：`rm_client/transports.py`

用途：

- 预留 SharkDataSever MQTT 状态接入。
- 后续可接收裁判系统、机器人状态、雷达信息、比赛事件。

### UDPVideoReceiver

文件：`rm_client/transports.py`

用途：

- 预留 SharkDataSever UDP 图传包接入。
- 已有分片重组 `FrameAssembler`。

### CameraFeed

文件：`rm_client/transports.py`

用途：

- 当前用电脑摄像头作为机器人图传。
- 摄像头不可用时客户端自动使用合成图传。

### VisionPipeline

文件：`rm_client/vision.py`

用途：

- 当前是 `MockVisionPipeline`。
- 后续替换为真实自瞄算法。
- 输出目标框、置信度、目标 ID 等数据。

### GUI / HUD

文件：`rm_client/ui/polar_app.py`

用途：

- PySide6/QPainter 渲染。
- 四套 GUI：`PolarOld`、`PolarPanel`、`PolarNew`、`PolarDropdown`。
- KeybindOverlay。
- 右侧 HUD 多模式。
- ModuleList 多样式。

### UIScale

文件：`rm_client/model.py`

用途：

- `Auto Scale`：按窗口尺寸自动放大/缩小 UI。
- `HUD Scale`：单独调整水印、右侧 HUD、状态卡片等覆盖层大小。
- `GUI Scale`：单独调整 ClickGUI 大小。
- `Animation Speed`：调整主要 UI 动画速度，数值越大动画越快。
- `Reference Width/Height`：自动缩放参考分辨率，默认 1280x720。

大屏幕 UI 显得过小时，优先打开 `UIScale` 模块并调高 `HUD Scale` 或 `GUI Scale`。

## RM 功能需求

### 图传与视觉

- 使用电脑摄像头作为默认图传。
- 保留 UDP 图传入口。
- 支持目标框、装甲板框、锁定状态、距离、置信度 HUD。
- 自瞄算法先 mock，后续替换真实模型。

### 底盘控制

- WASD/QE 输入需要转为标准底盘命令。
- 默认 mock，不直接控制机器人。
- 需要保留串口、MQTT 或其他链路适配点。
- 必须支持急停、限幅、心跳超时和断连保护。

### 云台与自瞄

- `ArmorAutoAim` 面向打车/装甲板目标，提供装甲目标框、锁定状态、置信度和云台输出预留接口。
- `EnergyAutoAim` 面向 7V7 能量机关，提供能量机关目标框、符进度、旋转方向、预测参数和输出预留接口。
- 两者使用独立模块开关和独立按键，避免“打车”和“打符”共用一个按钮导致策略混乱。
- 锁定框样式可在模块设置里切换：`Polar Corner`、`White Outline`、`Minimal Bracket`、`Rune Ring`。
- 输出接口需要预留 yaw/pitch、目标类型、目标 ID 或能量机关阶段、锁定状态、置信度。
- 默认不写真实云台。

### 小陀螺

- `SpinMode` 提供小陀螺状态、参数和底盘旋转输出预留接口。
- 当前影响 HUD 和控制命令里的 `spin_mode` 字段。
- 后续接底盘时根据 `Spin Speed` 和方向生成实际旋转指令。

### RM 状态显示

需要覆盖：

- 血量
- 热量
- 弹量
- 底盘能量
- 电容
- 功率限制
- 速度限制
- 摩擦轮
- 锁定状态
- Buff
- 反小陀螺
- 发弹允许量
- JUP/PTE/WHE/LES 状态

这些元素已经从 RMUIgenerator 语义转成 Polar 风格模块/HUD。

## 后续接真实机器人前必须补齐

- 正式串口协议编码/解码。
- 控制命令 CRC 或帧头帧尾。
- 下位机 ACK/心跳。
- 断连自动归零。
- 急停快捷键。
- 裁判系统状态保护。
- 比赛规则限制校验。
- 真实云台和底盘输出模块。
- 操作日志与回放。
