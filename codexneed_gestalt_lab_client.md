# POLAR Client × Gestalt Lab — 本轮架构与实现说明（Codex / 接手人）

> 路径：`rm_custom_client/codexneed_gestalt_lab_client.md`  
> 范围：Gestalt 联调、自瞄、输入夺舍、图传流畅度相关改动（**不是**早期 liquid glass 启动页那套，见 `codexneedread.md`）。  
> 代码树：`rm_custom_client/` + 兄弟目录 `sp_vision_py/` + `polar_input_bridge/`。

---

## 0. 一句话目标

**游戏只负责画面与官方外控通道；Python Client 负责 HUD/模块/夺舍决策；外挂负责检测与 RBExtAim；可选 DLL 把键鼠干净地送进游戏进程。**

不要用系统级「假鼠标跟枪」当自瞄，不要把整包 ClickGUI 做成 ImGui 重写（曾讨论过 Vape 全壳，**未采用**）。

---

## 1. 应对场景（Why）

| 场景 | 痛点 | 本轮对策 |
|------|------|----------|
| Lab 读 OBS 图传跟枪 | 分辨率/设备号乱、和自瞄争摄像头 | 默认 **VisionBridge SHM** 读游戏内存帧 |
| 外置 `SendInput` 键鼠 | 焦点玄学、P 无反应、光标飞出 client | **Input Bridge DLL** 进程内注入 + client 夺舍表 |
| 鼠标注入跟枪 | 灵敏度/焦点/GUI 打架、「抽象 bug」 | 自瞄改为 **RBExtAim 闭环**；鼠标只做可选手操视角 |
| Client 按 V 小陀螺无效 | `R` 被当成 client 保留键拦注入 | `set_game_spin` **force_game 注入游戏 R** |
| SHM 图传卡 | 全分辨率 + BGR→RGB + 每帧 `QImage.copy` + 重建纹理 | **限帧限宽 + BGR 直传 GPU `glTexSubImage`** |
| 一键启动分散 | 人肉开桥/自瞄/注入/client | **Lab Launcher** 串起四步含 DLL 注入 |

---

## 2. 总体架构

```text
┌─────────────────── 游戏 Shipping ───────────────────┐
│  -visionbridge  → VisionBridge SHM (R10G10B10A2…)    │
│  -externalvisioncontrol + WS → GameLink             │
│  polar_input_bridge.dll（可选注入）                   │
│     pipe \\.\pipe\POLARInputBridge.<pid>            │
│     Key / MouseMove / Exclusive                     │
└───────────────┬───────────────────┬─────────────────┘
                │ SHM               │ WS RBExtAim
                ▼                   ▼
┌──────────────────────┐   ┌──────────────────────────┐
│ sp_vision_py 外挂     │   │ ExtAimClaim + RBExtAim   │
│ YOLO+灯条+EKF+Aimer  │──►│ 绝对 yaw/pitch 闭环       │
│ UDP :9871 框         │   └──────────────────────────┘
│ ← :9872 AA 门控      │
└──────────┬───────────┘
           │ rm.vision.tongji.v1
           ▼
┌─────────────────────────────────────────────────────┐
│ POLAR Python Client (PySide6)                         │
│  · CameraFeed SHM 显示（GPU blit）                    │
│  · Armor3DESP 画框（吃 :9871）                        │
│  · ArmorAutoAim → :9872                               │
│  · 键夺舍：模块键本地；WASD/P… → Input Bridge          │
│  · V=SpinMode → 注入游戏 R                            │
│  · R=ArmorAutoAim（不注入游戏 R）                      │
└─────────────────────────────────────────────────────┘
           ▲
           │ START ALL
┌──────────┴──────────┐
│ gestalt_lab_launcher│  MAGI-I 注入 → B 桥 → A 自瞄 → S client
└─────────────────────┘
```

### 职责切分（务必遵守）

| 组件 | 做 | 不做 |
|------|----|------|
| **游戏** | 渲染、SHM、WS 白名单外控 | 不跑 Python 自瞄 |
| **sp_vision_py** | 检测、跟踪、RBExtAim、发框 | 不画 ClickGUI |
| **polar_input_bridge.dll** | 进程内键鼠落地 | 不画 HUD、不解算自瞄 |
| **Python client** | UI、夺舍决策、画框、图传显示 | 不应用 SendInput 假跟枪当主自瞄 |

---

## 3. 设计思路（How we think）

### 3.1 自瞄：官方通道优先

Gestalt 已提供：

- **VisionBridge SHM**：图  
- **GameLink `ExtAimClaim` + `UEExec RBExtAim`**：世界系绝对角 + 租约  

闭环写法对齐官方 demo（`spvision_closed_loop` 一类）：

1. 读炮口 `TurretYaw/Pitch`  
2. 装甲像素误差 → FOV/`atan2` → Δ°  
3. `RBExtAim(pid, cur+Δ, fire)`  
4. Client `ArmorAutoAim` 经 **:9872** 门控 claim  

**放弃**把 `err_x/err_y` 打成鼠标相对移动当主路径（lab_aim 仅遗留调试，默认关）。

### 3.2 输入：Client 夺舍 + DLL 落地（非全 Client DLL）

曾评估「整包 client 做成 ImGui DLL（Vape 壳）」——可行但工期大、液态玻璃/ClickGUI 要重做。

采用 **更贴痛点** 方案：

- **夺舍逻辑仍在 Python**（模块键表、F7、ClickGUI）  
- **落地用注入游戏内的小 DLL**，经命名管道收 Key/Mouse  
- 游戏窗口可不抢焦点也能收键（DLL 内 `SendInput` + 必要时短暂 `SetForegroundWindow`）

键语义：

```text
物理键
  → client 保留？ ──是──► 只 client（R 自瞄、V 陀螺模块、RShift GUI…）
                 └──否──► Input Bridge → 游戏（WASD、P、Space…）

模块副作用
  V 开 SpinMode ──► force 注入游戏 **R 按住**（小陀螺）
  V 关 ──► 松开游戏 R
  R 开 AA ──► :9872 armor_auto_aim=true（不注入游戏 R）
```

### 3.3 图传：显示与自瞄解耦

| 路径 | 分辨率/帧率 | 用途 |
|------|-------------|------|
| 外挂读 SHM | 全分辨率、尽量高帧 | 检测 + RBExtAim |
| Client 读 SHM | **限宽 + 限 FPS** | 给人看的底图 |

显示热路径目标：

```text
decode BGR →（可选缩到 Display Max Width）→ 持有 numpy
  → paintGL: glTexSubImage2D 更新同一纹理 → blit
  → QPainter 画 HUD/框（不再每帧 CPU drawImage 全图）
```

---

## 4. 实现清单（What / Where）

### 4.1 自瞄与协议（`sp_vision_py` + client 协议）

| 文件 | 作用 |
|------|------|
| `sp_vision_py/sp_vision/apps/gestalt_shm_runner.py` | SHM→检测→:9871；RBExtAim；灯条 merge |
| `sp_vision_py/sp_vision/io/rbext_controller.py` | claim/心跳/像素闭环 |
| `sp_vision_py/sp_vision/io/gestalt_shm.py` | SHM v3、WS 端口探测 |
| `sp_vision_py/sp_vision/io/gestalt_ws.py` | GameLink / `ext_aim` |
| `sp_vision_py/configs/gestalt_shm.yaml` | 阈值约 0.08（Gestalt 域） |
| `rm_client/tongji_bridge.py` | `:9871` / `:9872` 协议 |
| `rm_client/gestalt_lightbar.py` | 灯条补检测 |
| `tools/tongji_desktop_runner.py` | Lab 外挂入口，默认 pipeline=shm |

### 4.2 Input Bridge DLL（新目录）

路径：`RMaccseeplan/polar_input_bridge/`（**不在** `rm_custom_client` 内，与 client 兄弟）。

| 路径 | 作用 |
|------|------|
| `src/dllmain.cpp` | 注入后起管道服务 |
| `src/ipc/protocol.h` + `pipe_server.cpp` | `PLIN` 二进制帧协议 |
| `src/input/key_inject.cpp` | 键；必要时让游戏收输入 |
| `src/input/mouse_inject.cpp` | 相对鼠标 + **注入后还原光标** |
| `src/input/exclusive_filter.cpp` | 可选独占；**仅游戏前台吞原生输入** |
| `src/inject/injector_main.cpp` | LoadLibrary 注入器 |
| `tools/build_mingw64.ps1` | MSYS2 UCRT64 **`-static`** 构建（避免缺 `libwinpthread`） |
| `inject.bat` | 同目录绝对路径注入 |
| `build/bin/polar_input_bridge.dll` | 产物 |

**协议摘要**（`protocol.h`）：

| type | 含义 |
|------|------|
| Hello / Status | 握手、hwnd/pid |
| Key | vk + down + scancode |
| MouseMove | dx, dy（相对） |
| MouseButton | L/R/M |
| SetExclusive | 独占开关 |
| Heartbeat / ReleaseAll | 保活与松键；超时约 2.5s 自动释放 |

管道名：`\\.\pipe\POLARInputBridge.<game_pid>`。

### 4.3 Python Client 输入

| 文件 | 作用 |
|------|------|
| `rm_client/input_bridge.py` | 管道客户端 + 心跳线程 |
| `rm_client/gestalt_input.py` | 夺舍钩子；**优先 bridge**；`set_game_spin(force_game)`；鼠标还原光标 |
| `rm_client/model.py` `GestaltControl` | Inject Enabled / Use Input Bridge / Exclusive / Inject Mouse |
| `rm_client/ui/polar_app.py` | F7、模块切换副作用、中心锁定视角、环境变量覆盖 bridge |

**关键 bug 修复：小陀螺**

- 错误：`set_key("R")` 因 `R` 在 client 保留表被直接拒绝  
- 正确：`set_game_spin` → `set_key("R", enabled, force_game=True)`  
- 用户侧：按 **V** 开模块 → 游戏 **按住 R**；需 **Inject Enabled**

### 4.4 图传流畅度

| 文件 | 作用 |
|------|------|
| `rm_client/vision.py` `CameraCaptureWorker` | SHM 抓帧；**max_display_fps / display_max_width**；去重 frame_id；少 BGR→RGB |
| `rm_client/ui/polar_app.py` `consume_capture_frame` | 持有 `_video_bgr`；CPU QImage 降频 |
| `rm_client/ui/polar_app.py` `draw_combat_gpu_background` | 优先 `upload_bgr_numpy` |
| `rm_client/ui/glass_gl.py` `upload_bgr_numpy` | **glTexSubImage2D(BGR)** 复用纹理 |
| `rm_client/model.py` `CameraFeed` | Display Max FPS / Width；SHM Native 默认关 |

### 4.5 Lab Launcher

| 文件 | 作用 |
|------|------|
| `tools/gestalt_lab_launcher.py` | START ALL：`MAGI-I` 注入 → B → A → S |
| `configs/lab_launcher.json` | `auto_inject_dll` / `client_use_input_bridge` / `client_bridge_exclusive` |
| `start_lab_launcher.bat` | 入口 |

环境变量（launcher → client）：

- `POLAR_USE_INPUT_BRIDGE=1|0`  
- `POLAR_BRIDGE_EXCLUSIVE=1|0`  
- `POLAR_VISION_SOURCE=Tongji`  
- `POLAR_TONGJI_PIPELINE=shm`  

---

## 5. 关键路径走读

### 5.1 开机联调

1. Steam 游戏：`-visionbridge -externalvisioncontrol -wsbind=127.0.0.1`  
2. `start_lab_launcher.bat` → START ALL  
3. MAGI-I 日志：`INPUT BRIDGE ARMED`  
4. Client：F7 Inject Enabled  
5. R 开自瞄；V 小陀螺；图传 CameraFeed=Gestalt SHM  

### 5.2 自瞄框

```text
gestalt_shm_runner 检测 → build_tongji_packet → UDP :9871
  → TongjiVisionReceiver → draw_armor_3d_esp
ArmorAutoAim OFF → 可不画框（HUD 门控）；:9872 false → 外挂 release claim
```

### 5.3 键 V → 游戏陀螺

```text
物理 V
  → 保留键 → Qt / hook 队列 → toggle SpinMode
  → on_module_toggled → set_game_spin(True)
  → force 注入 vk=R down（bridge 优先）
  → 游戏小陀螺
```

### 5.4 手操视角（可选）

```text
Inject Mouse ON + 战斗 HUD（非 ClickGUI）
  → 光标锁窗口中心
  → 位移 → bridge MouseMove
  → DLL：尽量让游戏收输入 + SendInput + 还原光标
自瞄仍 RBExtAim，不要靠这条路径跟枪
```

### 5.5 图传 GPU 直传

```text
CameraCaptureWorker._run_shm
  → grab / 限帧 / 缩宽 / BGR 持有
consume_capture_frame
  → self._video_bgr = frame.bgr
paintGL
  → draw_combat_gpu_background
  → glass_gpu.upload_bgr_numpy (SubImage)
  → blit_scene_fullscreen
  → paint_overlay 画框与 HUD
```

---

## 6. 配置速查

### GestaltControl

| 设置 | 默认 | 含义 |
|------|------|------|
| Inject Enabled | 关（F7） | 总开关：向游戏送键（及可选鼠标） |
| Use Input Bridge DLL | 开 | 优先管道；失败 fallback 外置注入 |
| Exclusive Capture | 关 | 仅游戏前台时吞原生键鼠 |
| Inject Mouse | 关 | 中心锁定手操视角 |

### CameraFeed

| 设置 | 默认 | 含义 |
|------|------|------|
| Source | Gestalt SHM | 内存图传 |
| Display Max FPS | 30 | 显示限帧 |
| Display Max Width | 960 | 显示限宽 |
| SHM Native Size | 关 | 勿无脑开满原生分辨率 |

### lab_launcher.json

| 键 | 默认 |
|----|------|
| pipeline | `shm` |
| aim_control | true（外挂 --control） |
| auto_inject_dll | true |
| client_use_input_bridge | true |
| client_bridge_exclusive | false |

---

## 7. 构建与故障

### 编 DLL（MSYS2 x64）

```powershell
# 需: C:\msys64 + pacman -S mingw-w64-ucrt-x86_64-gcc
cd ...\polar_input_bridge
.\tools\build_mingw64.ps1
# 必须 -static，否则游戏内报缺 libwinpthread-1.dll
```

`g++ -dumpmachine` 必须是 **`x86_64-...`**。旧 `C:\MinGW` 的 `mingw32` **不能用**。

### 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 注入弹找不到 DLL | 动态链 pthread | static 重编；用 `inject.bat` 绝对路径 |
| V 不陀螺 | R 被保留表拦截（已修） | 更新 client；F7 开注入；看 toast |
| 光标飞出 client | 相对 SendInput 动系统光标 | 已还原光标；视角用中心锁定 |
| 有光标但不转视角 | client 前台时 UE 不收 SendInput | DLL 内短暂保证游戏可收输入；重注入新 DLL |
| 图传卡 | CPU copy + 双读 SHM | 限宽限帧 + GPU SubImage；Glass Lite |
| :9871 无框 | 旧 client 与显示争端口 / 外挂挂 | 只开一个 Listen；外挂 yolo/lb 日志 |

---

## 8. 明确未做 / 不做

- 不把整包 ClickGUI/液态玻璃搬进 DLL（Vape 全壳）  
- 不把 YOLO 塞进 DLL  
- 不把 lab_aim 鼠标当默认自瞄  
- 不保证 Exclusive 全局钩在所有杀软/多屏下完美  
- SHM **显示**与**自瞄**仍是两次解码（可用限流缓解，未做共享解码缓冲）

---

## 9. 文件地图（本轮相关）

```text
RMaccseeplan/
  polar_input_bridge/          # 键鼠 DLL + 注入器 + 构建脚本
  sp_vision_py/                # SHM 自瞄外挂
  rm_custom_client/
    codexneed_gestalt_lab_client.md   # 本文
    codexneedread.md                  # 早期 UI/玻璃启动问题（另一议题）
    start_lab_launcher.bat
    configs/lab_launcher.json
    tools/
      gestalt_lab_launcher.py
      tongji_desktop_runner.py
    rm_client/
      input_bridge.py
      gestalt_input.py
      gestalt_lightbar.py
      vision.py
      tongji_bridge.py
      model.py
      ui/
        polar_app.py
        glass_gl.py
```

---

## 10. 给后续 Agent 的操作建议

1. 改输入：先看 `gestalt_input.py` + `input_bridge.py` + `polar_input_bridge/src/input/*`，勿再引入默认 lab_aim 鼠标跟枪。  
2. 改自瞄：`gestalt_shm_runner.py` / `rbext_controller.py`，门控永远对齐 `:9872`。  
3. 改图传流畅度：显示侧 `vision.py` + `glass_gl.upload_bgr_numpy`；不要拖慢外挂全分辨率路径。  
4. 改一键启动：`gestalt_lab_launcher.py` 的 `ServiceProc` 顺序（I→B→A→S）。  
5. 验证：游戏 flags → 注入 ARMED → F7 → V 陀螺 toast → R 自瞄有框 → 图传 GPU blit。  

---

*文档随 Gestalt Lab / Input Bridge / 图传 GPU 直传会话整理，供 Codex 与人类接手对齐上下文。*
