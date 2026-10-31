# Jello Esc menu

On the unobstructed main HUD, Esc opens a second-level Jello menu. Esc closes
it. Existing keybind capture, search/chat, student panels, Economy, and
ClickGUI keep their existing first refusal on Esc. Arrow keys or Tab select;
Enter/Space activates. Pointer activation requires press and release on the
same row, preventing stale HUD hotspots from receiving clicks.

- Continue: dismiss and keep held controls released.
- Return to startup: lock control through the existing backend lifecycle,
  neutralize inputs, return to scene selection. Never switch real output to mock.
- Exit client: confirmation with Cancel selected; final exit uses the existing
  lock/release/estop/shutdown closeEvent lifecycle.

The menu does not stop any timer, backend tick, or safety monitoring. Input
ownership is blocked at both event dispatch and the combat permission predicate.
F12 emergency stop remains available. Opening/closing neutralizes the existing
KeyboardControlBridge, including forced DT7/custom-controller release frames.

Rendering uses the existing unified GPU Jello 20px backdrop treatment and a
QPainter card on its interaction target, using the existing FontBook jello_light
asset and near-white Jello text. No new heavy resources are loaded. Emergency compatibility uses the same
card without CPU blur, framebuffer readback, or new per-frame image processing.
The MC/CS/BF6 HUD and Jello module list are unchanged. Base HUD refresh under this
menu is throttled to 4Hz while backend/safety updates retain their normal cadence.

## Verification

`QT_QPA_PLATFORM=offscreen python tests/test_escape_menu.py` exercises real Qt
input events on the production input/menu mixins, with inert hardware adapters.
It tests held controls and neutral frames with the real KeyboardControlBridge,
repeated Esc, modal precedence, input isolation, resize/hit testing, return lock,
exit confirmation, F12, and continued timer activity. This does not certify
physical robot safety or real driver/GPU behavior.

`python tests/test_escape_menu.py --preview` writes an actual offscreen QWidget
capture to `/tmp/polar-jello-menu-offscreen.png`. The image explicitly identifies
its inert background and lack of GPU blur; it is not a live hardware screenshot.

The offscreen capture verifies only emergency-card layout and input. It cannot
demonstrate the live Jello GPU backdrop, GPU compositing or hardware frame pacing;
those require a real OpenGL driver/display and remain unverified here.

## 菜单精简（2026-10-25）

Windows 原生 Esc 菜单保留左侧更新日志与一个声音设置入口。右侧更新日志、重复声音入口及英雄部署已移除，剩余条目连续排列。英雄部署控制台、快捷键特殊处理、发送接口、资格检查和自动图传切换一并删除；`DeployModeStatusSync` 仅保留官方遥测解码，旧部署指令在统一发送门禁中拒绝。独立的 `HeroLob` 弹道预览及标定保持可用。

## 入口换位（2026-10-26）

左侧固定栏原快捷键位置改为功能列表，右侧原功能列表位置改为快捷键。原生导航交换两个动作的槽位，图标、中文/英文标题及点击功能一起迁移，沿用各自原有入口和菜单生命周期。随附原生 DLL 已重新构建，运行中的客户端需重启后加载。

## UI 切换（2026-10-26）

右侧 `UI切换` 已接通动作 21；点击后先确认，取消或 Esc 返回原菜单。Endfield 当前界面使用白底黑字确认框，Jello 当前界面直接复用 `JelloDialog`、原有字体及进退动画。非原生兼容菜单同样提供切换入口。

界面选择单独保存在 `user_data/ui_theme.json`，缺失或无效时默认 Endfield。一次进程固定一种界面：功能列表的 renderer/state/timeline、设置中心、视频控件、滚动区及键位管理使用同一选择。Jello 使用现有卡片和键盘画布；Endfield 使用 Wiki 功能列表及独立键位页。Esc 原生导航共用同一入口，机器人配置与 UI 偏好分别保存。

确认后调用正常窗口关闭，沿用配置保存、输入锁定及异步连接/线程/原生资源释放。仅当 Qt 主循环退出且生命周期已确认释放、完成最终清理，才通过当前运行环境自动拉起终端。源码版使用同一虚拟环境的 `pythonw.exe`（可用时），便携版使用原可执行文件。拒绝关闭会恢复原 UI 偏好；保存或拉起失败会显示错误。重新启动不会恢复原来的驾驶按键。

相关代码位于 `ui_theme.py`（偏好）、`settings_ui.py`（设置表现层接口）、`ui_switch.py`（确认及重启）。原生构建更新为 `build_e50d62220b6001bb`，完整性清单与 CURRENT 同步更新。

验证入口：`python -m unittest tests.test_ui_switch tests.endfield.test_endfield_menu tests.test_escape_menu`；`python tools/verify_ui_switch_desktop.py --theme jello --out <新目录>` 可验证真实 Windows 窗口，`--theme endfield` 验证另一套界面。桌面验证使用临时配置及空设备启动，不连接机器人。
