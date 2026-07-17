# Gestalt Lab 快速手册

工作区：`RMaccseeplan/rm_custom_client`  

**完整需求与改动史：** 见 [`README_需求与改动说明.md`](./README_需求与改动说明.md)

---

## 职责

| 组件 | 职责 |
|------|------|
| OBS | 游戏画面 → 虚拟摄像头 |
| POLAR client | 图传、状态 HUD、**全部模块快捷键**、画框；可选 WASD/鼠标注入 |
| 同济外挂 | Lab 默认 **完整 Python**：`sp_vision_py` via `tongji_desktop_runner`（YOLO+EKF+Aimer→:9871）；真机可选 C++ `同济自瞄/sp_vision_25` |
| Telemetry Relay + HTTP 桥 | 游戏 Attr → 热/血/基地进 client |
| Gestalt 游戏 UI | **选车 / 准备 / 部署**（SDK 无部署接口） |

---

## 启动顺序

### 推荐：EVA / AIR 风格一键启动台

```powershell
cd rm_custom_client
.\start_lab_launcher.bat
# 或
.\.venv\Scripts\python.exe tools\gestalt_lab_launcher.py
```

Magi/NERV 界面风 + AIR「真心为你」题跋。  
点 **START ALL UNITS** 按序拉起：

1. **数据桥** `gestalt_attr_http_bridge`（:9841 → :9840）  
2. **同济自瞄 SHM**（默认）读游戏 **VisionBridge 共享内存**（非 OBS）→ UDP :9871  
3. **POLAR Client**（`Vision Source=Tongji`）  

游戏启动需带 **`-visionbridge`**（外控再加 `-externalvisioncontrol`）。  
PIPELINE：`shm`（默认）/ `obs`（旧虚拟摄像头）/ `legacy`。  
WS PORT=0 时自动找 `*.wsport` 或游戏进程。

### 手动顺序（等价）

1. **Gestalt** → 房间内 **选车并进入可操控**（部署只在这里做）  
2. **OBS** 捕获窗口 → **启动虚拟摄像头**  
3. （状态）游戏内加载 `workshop_telemetry_relay`  
4. **`start_lab_launcher.bat`** → START ALL（数据桥 + 同济自瞄 + Client）  
5. 图传：`CameraFeed = Gestalt SHM`（默认，读游戏 VisionBridge；无需 OBS）  
6. Client：`Vision Source=Tongji`；**R** 开/关 ArmorAutoAim（门控外挂 **RBExtAim**，不再用鼠标跟枪）  
   游戏需 `-externalvisioncontrol`；外挂认领 `ExtAimClaim` 后写 `UEExec RBExtAim`  

（仅开 Client 时可用 `启动.bat`。）

---

## 快捷键（注入时也要在 client 生效）

| 键 | 作用 |
|----|------|
| **Right Shift / F8** | ClickGUI（打开时 **暂停注入**） |
| **F7** | 开/关游戏注入 |
| **F9** | GUI 模式 |
| **R** | ArmorAutoAim：画框 + 外发开关给同济；Lab 跟枪门控 |
| **V** | client **小陀螺**（SpinMode；开时向游戏注入 R） |
| **L-Shift** | 游戏 **加速**（Inject ON） |
| **RightShift / F7–F9** | 仅 client（GUI / 注入开关） |
| **Tab** | **按住放大**小地图，松开缩小（不开关模块） |
| **WASD** | Inject ON 时进游戏移动 |
| **鼠标** | Inject Mouse ON 时进游戏视角 |
| **Alt+1 / Alt+2 / Alt+3** | 游戏 **部署**（钩子放行，不被 client 吞掉） |
| **Win / Apps / PrtSc** | **系统键永久放行**（注入开着也不屏蔽） |

### 热量 / 冷却 / 小地图旁状态栏真源

需：Workshop **Telemetry Relay** 已加载 + `tools/gestalt_attr_http_bridge.py`（:9841→UDP :9840）。

| 水印 | 状态栏 |
|------|--------|
| `TEL gestalt` | HEAT/POWER/基地等来自游戏 Attr；未知项显示 `—`（不再假 5000） |
| `TEL fallback` | Lab 假波形，tooltip 带 `[f]` |

HEAT 提示含 `CD` 冷却率与 `[g]`/`[f]` 来源。

### GestaltControl 设置（必看）

| 设置 | 推荐 |
|------|------|
| Inject Enabled | 开车时 ON（**F7**） |
| Inject Mouse | 要视角时 ON |
| Keep Client Focus | ON |
| Focus Game | **OFF（勿开）** |

硬规则：**只映射 WASD + 鼠标**；快捷键废了说明还在用旧抢焦点逻辑——重启 client。

---

## OBS 图传

1. OBS 窗口捕获 → 启动虚拟摄像头  
2. Client Lab → CameraFeed → **OBS VirtualCam** → Camera ID 试 0/1/2  

详见需求文档 §5.1。

---

## 状态 / 框 / 部署

| 需求 | 做法 |
|------|------|
| 热/血/基地 | Relay + attr bridge → `TEL gestalt` |
| 自瞄框 | NUC + `Vision Source=Tongji`；ArmorAutoAim 只画框 |
| 部署车 | **游戏 UI**；Workshop SDK **无 deploy** |

---

## 水印

`TEL gestalt|fallback` · `CTL live|disabled|error|paused_gui`

---

## 故障速查

| 现象 | 处理 |
|------|------|
| **鼠标停不住 / 光标飞出 client** | **Inject Mouse=OFF**；Focus Game=OFF；重启 client |
| client 快捷键全无效 | Focus Game=OFF；Keep Client Focus=ON；点活 client 窗口 |
| 游戏不动（WASD） | F7 注入 ON；已上车；水印 `CTL live`；游戏窗口标题含 RobotBridgeDemo/Gestalt |
| 图传黑 | OBS 虚拟摄像头 + Camera ID |
| 无框 | NUC、Tongji、ArmorAutoAim |
| TEL fallback | Relay + bridge |
| 部署不了 | 游戏内选车准备，不是 client |

---

## 文件

| 路径 | 说明 |
|------|------|
| `rm_client/gestalt_input.py` | 注入（不抢前台） |
| `rm_client/ui/polar_app.py` | eventFilter 键路由、快捷键优先 |
| `workshop_telemetry_relay/` | Attr 转发 HUD |
| `start_lab_launcher.bat` | 一键启动台（桥+自瞄+client） |
| `启动.bat` | 仅启动 POLAR Client |
