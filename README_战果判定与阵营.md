# 阵营、战术地图与战果特效

## 阵营

`AllianceProfile -> Side` 选择本机为 `Red` 或 `Blue`。它统一用于战术地图镜像、雷达/ESP 敌我颜色、视觉击杀过滤和状态栏标识。地图始终用固定红蓝表示队伍，本机队伍会附加 `YOU`。

## 战果来源

`EliminationFX -> Evidence Mode`：

- `Auto`：官方 MQTT 数据新鲜时仅认裁判事件；连续超时后改用视觉灯态。
- `Referee Only`：只接收官方 `Event`、单位血量归零、战亡状态和队内字节块扩展。
- `Vision Only`：只使用同济识别结果。

视觉判定只面向敌方目标：同一跟踪装甲必须先稳定识别为红/蓝亮灯，再连续至少 5 帧、约 0.45 秒被识别为 `extinguish`，同时灯条亮度较低，才触发 `VISION CONFIRMED`。遮挡、丢失、单帧低置信度和画面自动曝光突变不会触发击杀；亮灯恢复会重置该目标状态。

该桌面联调功能只驱动显示、音效和 mock 云台建议，不会发送发射或真实控制指令。

## GD656 资源与发布

击杀纹理和 OGG 音效位于 `assets/kill_fx/gd656/`，会被 `tools/build_release.ps1` 连同唯一启动入口 `启动.bat` 一起放入发布 ZIP。运行：

`EliminationFX -> Preset` 使用 GD656 原始 preset 结构：Scrolling、Combo、Card、Battlefield 1、Battlefield 5、字幕模式及全部 26 套 Valorant 皮肤。`Element Visibility = Preset` 严格使用上游预设元素；`Full HUD` 才额外开启击杀字幕、连杀字幕、分数和 BonusList。

特效内部不叠加 POLAR 文案或液态玻璃。`Safe Area` 只平移整个特效根节点以避开底部遥测；选择 `Original Position` 可按原始底部锚点显示。

```powershell
cd rm_custom_client
.\tools\build_release.ps1
```
