# 同济真实自瞄联调

## 职责划分（硬规则）

| 位置 | 职责 |
|------|------|
| **完整同济 Python** `sp_vision_py/` | **默认 Lab 外挂**：读 **Gestalt VisionBridge SHM** → YOLO → EKF → Aimer → UDP 框；可选 `RBExtAim` |
| **官方 C++ 桥** `sp_vision_25_gestalt_system_bridge` | `gestalt.exe`：同协议 SHM + GameLink 整局接管（参考实现） |
| **Lab 入口** `tools/tongji_desktop_runner.py` | 默认 `gestalt_shm_runner`；`POLAR_TONGJI_PIPELINE=obs|legacy` 才走 OBS |
| **POLAR client** | 可仍用 OBS 作图传显示；框来自 :9871；AA 开关 :9872 |
| **游戏** | 须 `-visionbridge` 导出共享内存帧 |

```text
Gestalt 游戏 ──SHM VisionBridge──► sp_vision_py（完整自瞄）──:9871──► client 画框
                 └──WS GameLink──►（可选）RBExtAim 外控云台
client 图传可仍接 OBS（仅显示，不喂自瞄）
ArmorAutoAim ──:9872──► 门控 lab_aim / 注入
```

## 端口

| 端口 | 协议 | 方向 |
|------|------|------|
| **9871** | `rm.vision.tongji.v1` | 外挂 → client（框 + command 误差） |
| **9872** | `polar.aa.state.v1` | client → 外挂（`armor_auto_aim` bool） |

## ArmorAutoAim

- **开**：client 画框；外挂 `armor_auto_aim=true` 可输出 `lab_aim`；Gestalt Lab 可跟枪  
- **关**：不画框；外挂应停止 control/lab_aim  
- **不**启停外挂进程  

## Lab 启动顺序（Gestalt）

1. 游戏 + OBS 虚拟摄像头  
2. Attr 桥 + Relay（状态）  
3. **Client**（`Vision Source=Tongji`）  
4. **外挂（完整 Python 自瞄）**：`start_lab_launcher.bat` 内「同济自瞄 / MAGI-A」，或  
   `python tools\tongji_desktop_runner.py --source 0`  
   - 环境变量：`POLAR_OBS_CAMERA_ID`（默认 0）、`POLAR_VISION_UDP_PORT`（9871）  
   - 配置：`sp_vision_py/configs/gestalt_lab.yaml`（低阈值 / enemy=any）  
   - 回退旧简化管线：`set POLAR_TONGJI_PIPELINE=legacy`  
   - 真机 C++：仍可用 `同济自瞄/sp_vision_25`，协议兼容 :9871/:9872  
5. Client 上 **R** 开 ArmorAutoAim，**F7** 开注入 → 跟视角  

### 依赖

```powershell
# client venv 建议同时装上完整管线依赖
cd rm_custom_client
.\.venv\Scripts\pip.exe install -r requirements.txt
# 或独立：
cd ..\sp_vision_py
pip install -r requirements.txt
```

模型权重：`sp_vision_py/assets` 目录联接指向 `同济自瞄/sp_vision_25/assets`。

## 水印

`AA on/off` · `VIS Tongji|Local-display-only|…` · `TEL` · `CTL`

## 协议摘要

框包 `rm.vision.tongji.v1` 可带：

```json
"command": {
  "control": true,
  "lab_aim": true,
  "fire": false,
  "err_x": 0.12,
  "err_y": -0.05,
  "yaw_rad": 0.0,
  "pitch_rad": 0.0
}
```

`err_x/err_y`：目标相对画面中心，约 ±1。client 仅在 AA 开且注入启用时用于鼠标。

AA 状态包：

```json
{"protocol":"polar.aa.state.v1","t":1234567890.1,"armor_auto_aim":true}
```
