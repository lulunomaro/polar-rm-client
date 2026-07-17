# Gestalt + POLAR-RM：需求、改动与使用说明

> 路径：`RMaccseeplan/rm_custom_client/README_需求与改动说明.md`  
> 工作区：`G:\SteamLibrary\steamapps\common\GestaltSystem\RMaccseeplan\`  
> 关联文档：  
> - `README_GESTALT_LAB.md` — 简版启动与验收  
> - `README_同济真实视觉联调.md` — NUC / 视觉职责与端口  
> - `README.md` — 通用 POLAR 客户端说明  

**最后同步：同济职责对齐（外挂自瞄 + client 画框/开关 + Lab 跟枪）· 键位 R/V/Shift · Tab 放大 · Attr 状态栏 · Win/部署放行 · Relay 安装**

---

## 一、你的需求（归纳）

### 1.1 总目标

用 **rm_custom_client（POLAR）** 在 **Gestalt System** 里尽量模拟 **真实 RoboMaster 操作手端**：看图传、看状态、看自瞄框、用 client 快捷键操作模块；需要时用 client 键鼠开车。  
**完整自瞄算法与火控不在 client 内实现**，而在 **同济外挂**（Lab 默认 **`sp_vision_py` 完整 Python 管线**；真机可选 C++ `同济自瞄/sp_vision_25`）。

### 1.2 分项需求

| # | 需求 | 说明 |
|---|------|------|
| 1 | Client 功能尽量对齐 | 状态 HUD、图传、自瞄框显示、模块系统 |
| 2 | 状态统一在 client 看 | 热量、冷却、本车血、基地血等，**优先游戏 Attr** |
| 3 | 图传用 OBS | OBS 捕捉游戏 → client `CameraFeed` |
| 4 | 自瞄检测用 OBS | 外挂读 OBS（模拟图传）；**真自瞄在完整同济** |
| 5 | 自瞄开关对齐 client | `ArmorAutoAim`：**画框** + **外发开关给外挂**；Lab 下可门控跟枪 |
| 6 | 协议可参考 Shark | 端口/习惯可对齐；**数值优先游戏 Attr 真源** |
| 7 | 实验室 LabDebug | 本机调试默认场景 |
| 8 | 数据对齐游戏根源 | 热/血/基地可追溯；fallback 必须标明 |
| 9 | Client 操作进游戏 | WASD/L-Shift/鼠标注入后游戏要有反应 |
| 10 | **全部 client 快捷键可用** | RightShift、R/V、F7–F9、Tab 等，**注入时也要在 client 生效** |
| 11 | 部署机器人 | 游戏原生 **Alt+1/2/3**；注入钩子不得吞掉 |
| 12 | 键位偏好 | **R=自瞄**，**V=小陀螺**，**L-Shift=加速**；Tab=**按住放大地图**（非开关） |
| 13 | 系统键 | **Win 键等不得屏蔽** |

### 1.3 架构原则（当前定稿）

```text
Gestalt Lab：
  OBS              → 游戏画面（虚拟摄像头）
  POLAR client     → 图传 + HUD + 画框 + 模块快捷键
                   → WASD/L-Shift/鼠标注入 + Alt+1/2/3 放行
                   → ArmorAutoAim 开关 → UDP :9872（polar.aa.state.v1）
  同济外挂（Lab 默认） → sp_vision_py 完整管线（YOLO+Solver+EKF+Aimer）
                       via tools/tongji_desktop_runner → :9871 框+误差
  同济外挂（真机 C++） → 同济自瞄/sp_vision_25：工业相机 + C 板
  Workshop Relay   → 游戏 Attr → HTTP :9841 → UDP :9840 → client 状态
```

**禁止：**

- 用 client 内嵌 YOLO 冒充「完整同济自瞄」  
- `ArmorAutoAim` 启停外挂进程  
- 假数据冒充 `TEL gestalt`  
- 注入时吞 **Win / Alt+1–3** 等系统或部署键  
- Tab 绑定把地图模块 **toggle 关掉**（应为按住放大）

---

## 二、关键结论

### 2.1 数据根源

| 问题 | 结论 |
|------|------|
| 游戏有无热/血/基地 | **有**（HUD Attr） |
| client 能否直接读 | **不能**（只推 iframe） |
| 对齐路径 | **Telemetry Relay** → `:9841` 桥 → `:9840` → client |
| 实测例 | Relay OK 时蓝方基地可读（如 **200/200**，训练场上限） |
| Shark 随机数 | 仅 `fallback`，不算真源 |

### 2.2 控制与快捷键

| 问题 | 结论 |
|------|------|
| 如何开车 | **F7** / Inject ON + 已上车；**仅 WASD + L-Shift + 可选鼠标** 进游戏 |
| 小陀螺 | client **V** 开 SpinMode → 向游戏注入 **R**（游戏小陀螺键；client 的 R 留给自瞄） |
| 加速 | **L-Shift** 注入游戏；**RightShift** 仍开 ClickGUI |
| 部署 | **Alt+1/2/3** 钩子放行，不进 client 吞键逻辑 |
| Win 键 | **永久放行**（LWIN/RWIN/Apps/PrtSc） |
| Tab | **按住放大** TacticalMap，松开缩小，**不开关模块** |
| 为何曾快捷键全废 | SetForegroundWindow 抢焦点 / 钩子吞非 WASD；已修 |

### 2.3 同济自瞄（易混淆点）

| 组件 | 是什么 |
|------|--------|
| `sp_vision_py/` | **完整同济 Python 化**（检测/PnP/EKF/弹道/瞄准，对齐 C++） |
| `tongji_desktop_runner` | Lab 外挂入口；**默认 full=sp_vision_py**，可 `legacy` 回退简化版 |
| `同济自瞄/sp_vision_25` | 原版 C++（真机相机/C 板） |
| client 内 `Local` YOLO | 仅可选「显示检测」，默认应 **Tongji 外接** |
| client `ArmorAutoAim` | 画框 + **:9872 开关**；Lab 下可 **Inject Mouse 跟枪** |

---

## 三、已做改动（按主题）

### 3.1 文件清单（核心）

| 文件 | 改动摘要 |
|------|----------|
| `rm_client/gestalt_input.py` | 键级钩子；WASD/L-Shift 注入；Spin R；Alt+1–3 部署；Win 等 OS 放行；Lab 鼠标 force |
| `rm_client/gestalt_attrs.py` | Attr→状态；热量/冷却双管；基地按 TeamID；未知字段 -1；map 槽位 |
| `rm_client/gestalt_telemetry.py` | UDP 收包；新鲜度 ~3.5s |
| `rm_client/tongji_bridge.py` | 框协议；**AA 状态 9872 发布/接收**；Lab aim 误差缓存 |
| `rm_client/control.py` | 底盘键含 SHIFT；注入白名单 |
| `rm_client/model.py` | 模块默认键位；tick 真源不覆盖状态栏字段 |
| `rm_client/ui/polar_app.py` | 键路由、Tab hold、AA 广播、Lab 跟枪、水印、状态栏 [g]/[f] |
| `tools/tongji_desktop_runner.py` | 默认 **sp_vision_full**；OBS→完整管线→框+`err_x/err_y` |
| `../sp_vision_py/` | 同济 Python 包 + `polar_lab_runner` + `gestalt_lab.yaml` |
| `tools/gestalt_attr_http_bridge.py` | Relay HTTP→UDP |
| `workshop_telemetry_relay/*` | 游戏侧 Attr 继电器 |
| `configs/default.json` | Lab 默认（Tongji、OBS、键位等） |
| Steam Workshop 覆盖 | 已把订阅项 `4007690/3698375578` 换成 POLAR Relay（官方示例有备份） |

### 3.2 键位定稿（`default.json` / `model.py`）

| 键 | 作用 |
|----|------|
| **R** | ArmorAutoAim（画框 + 外发 AA 开关；Lab 跟枪门控） |
| **V** | SpinMode（小陀螺模块；开时注入游戏 **R**） |
| **L-Shift** | 游戏加速（注入） |
| **RightShift / F8** | ClickGUI（开时暂停注入） |
| **F7** | 注入总开关 |
| **F9** | GUI 模式 |
| **Tab** | 按住放大战术地图 |
| **Alt+1/2/3** | 游戏部署（钩子放行） |
| **Win 等** | 系统键，不劫持 |

EliminationFX **不再占用 Shift**。

### 3.3 注入白名单（进游戏的键）

- **WASD** 移动  
- **L-Shift** 加速  
- **SpinMode 开** 时自动按住游戏 **R**（小陀螺）  
- **鼠标**（Inject Mouse 或 Lab 自瞄 `force`）  
- **Alt+1/2/3** 部署（放行/转发）  
- 其余模块键、RightShift、F7–F9、Tab 等 **只走 client**

### 3.4 Tab / 地图

- 根因：Tab 曾被当成 `TacticalMap` 的 **toggle**，变成开关地图。  
- 改法：路由 **先 hold 放大**；`dispatch_client_hotkey` **跳过 TacticalMap**。

### 3.5 状态栏 / 热量冷却

- 真源：`TEL gestalt` 时 HEAT/POWER/基地等来自 Attr；未知显示 **—**，不再假 5000。  
- fallback：sin 波形，tooltip **`[f]`**；真源 **`[g]`**。  
- 依赖：Relay + `gestalt_attr_http_bridge`。

### 3.6 Workshop Relay 安装

- 官方示例在：`steamapps/workshop/content/4007690/3698375578/`  
- 已覆盖为 **POLAR Gestalt Telemetry Relay**；备份：`3698375578_backup_official_example`  
- 游戏内需 **重载 HUD / 重进对局**；左上角 **Relay OK** 即通。

### 3.7 同济职责对齐（本轮重点）

| 端口 | 协议 | 方向 |
|------|------|------|
| **9871** | `rm.vision.tongji.v1` | 外挂 → client（框 + `err_x/err_y` / command） |
| **9872** | `polar.aa.state.v1` | client → 外挂（`armor_auto_aim`） |

**Lab 跟枪（已实现）：**

1. 外挂读 OBS，检出目标 → 算画面中心误差  
2. 仅当收到 client `armor_auto_aim=true` 时 `lab_aim/control=true`  
3. Client：`ArmorAutoAim` 开 + **F7 注入** → `mouse_move(..., force=True)` 拨游戏视角  

**完整 C++ 同济：** 仍应读 :9872 门控；真机串口不在 client；可接同一框协议。

### 3.8 历史修复摘要

| 主题 | 处理 |
|------|------|
| 注入抢焦点 | 去掉周期性 SetForegroundWindow；Keep Client Focus |
| 光标飞出 | 禁用有害 SendInput+还原死循环；Lab 跟枪用 force 相对移动 |
| 全部键无效 | eventFilter + client 键优先 + 钩子分流 |
| 部署 Alt+1/2/3 | 钩子放行，不吞 |
| Win 键 | OS_PASSTHROUGH 永久放行 |

---

## 四、数据流 / 控制流

### 4.1 状态

```text
游戏 Attr → Workshop Relay → HTTP :9841 → 桥 → UDP :9840
  → GestaltAttrReceiver → RobotState → HUD / 小地图旁状态栏
无继电器 → TEL fallback
```

### 4.2 图传

```text
Gestalt → OBS 虚拟摄像头 → CameraFeed → 底图
```

### 4.3 自瞄（Lab）

```text
OBS → tongji_desktop_runner（或完整 sp_vision_25）
    → UDP :9871 框+误差 → client 画框
client ArmorAutoAim → UDP :9872 → 外挂门控
AA 开 + Inject → 误差 → 游戏视角
```

### 4.4 人操作

```text
client 键盘 → 钩子：WASD/L-Shift/部署/Win 分流
            → 模块键只 client
            → 注入 PostMessage/SendInput 到游戏窗口
```

---

## 五、使用步骤（Lab 推荐顺序）

1. **Gestalt** 启动，进可玩状态；**Alt+1/2/3** 部署  
2. Workshop 加载 **POLAR Telemetry Relay**，见 **Relay OK**  
3. **Attr 桥**：一键台 `start_lab_launcher.bat` 或 `python tools\gestalt_attr_http_bridge.py`（`:9841`）  
4. **OBS** 捕窗口 → 启动虚拟摄像头  
5. **Client**：`启动.bat` → LabDebug；图传选 OBS，Camera ID 对齐  
6. **同济 Lab 外挂**：一键台或 `python tools\tongji_desktop_runner.py`（`POLAR_OBS_CAMERA_ID`）  
7. Client：**R** 开 AA，**F7** 开注入；水印应有 `TEL gestalt` / `AA on` / `VIS Tongji`  

### 5.1 水印

```text
CAM|SYN | 场景 | 模式 | TEL … | CTL … | AA on|off | VIS … | …
```

| 标记 | 含义 |
|------|------|
| TEL gestalt / fallback | Attr 真源 / 本地模拟 |
| CTL live / disabled / paused_gui / error / filter* | 注入状态 |
| AA on / off | ArmorAutoAim |
| VIS Tongji / Local-display-only / … | 视觉来源 |

### 5.2 默认模块键位（以配置为准）

| 键 | 模块 |
|----|------|
| R | ArmorAutoAim |
| V | SpinMode |
| G | EnergyAutoAim |
| C | ChassisAssist |
| H | HUD |
| Tab | TacticalMap（按住放大） |
| RightShift/F8 | ClickGUI |
| F7 | 注入 |
| F9 | GUI 模式 |

---

## 六、故障排查

| 现象 | 处理 |
|------|------|
| client 键全无效 | Focus Game=OFF；点活 client；重启 |
| 鼠标飞出 | Inject Mouse 慎用；重启 |
| 部署不了 | 确认 Alt+1/2/3 钩子版；注入中也放行 |
| Win 键没反应 | 升级含 OS 放行的 `gestalt_input`；重启 client |
| TEL 一直 fallback | Relay OK？桥 :9841？重载 HUD |
| 状态栏假/5000 | 要 `TEL gestalt`；未知应显示 — |
| 无框 | 外挂 runner 是否跑、:9871、Vision=Tongji、AA 是否开 |
| 有框不跟枪 | AA 开？F7 注入？外挂日志是否 `AA` 与 err 非零？ |
| 同济「开了但 0 armor」 | OBS 画面是否含装甲；Camera ID 0/1/2 |
| 误以为 client 就是完整自瞄 | 完整算法在 `sp_vision_py`（Lab）或 C++ `同济自瞄`；client 只画框/跟枪 |

---

## 七、限制

- Attr 真源依赖 Relay + 桥  
- 训练场基地血上限可能是 **200**（非 RMUC 5000）  
- 完整 C++ 同济需单独编译/部署；Lab runner 无下位机串口  
- 游戏 **无公开 bot_add**；Bot 能力以游戏版本 UI 为准  
- HIVE 裁判全量未默认接入  

---

## 八、验收清单

- [ ] OBS 图传可见  
- [ ] Relay OK + 桥 → `TEL gestalt`；基地/热量变化；未知为 —  
- [ ] 外挂跑着：:9871 有包；client 有框（AA 开）  
- [ ] AA 关：不画框；外挂 `armor_auto_aim=false`、不跟枪  
- [ ] AA 开 + F7：有目标时视角有跟进（Lab）  
- [ ] WASD / L-Shift 进游戏；RightShift/R/V/F 仍 client  
- [ ] Tab 按住放大地图  
- [ ] Alt+1/2/3 可部署；Win 键能用  

---

## 九、改动时间线（便于对照会话）

1. Lab/OBS/Attr/注入骨架  
2. 快捷键与焦点、注入白名单  
3. 键位 R/V/Shift、Tab 放大、热量冷却  
4. Alt 部署、Win 放行、状态栏 Attr  
5. Workshop 目录覆盖为 POLAR Relay  
6. **同济职责**：9872 AA 状态、Lab 跟枪、runner 门控与文档  

---

*再改 Gestalt / 同济接入时请同步更新本文、`README_GESTALT_LAB.md`、`README_同济真实视觉联调.md`。*
