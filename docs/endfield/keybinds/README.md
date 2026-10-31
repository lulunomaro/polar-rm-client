# 独立快捷键管理

用户截图中的 `Keybind Manager` 已由独立的 Endfield 风格实体键盘替换。主界面为84键布局，包含功能键区、主键区、独立编辑区和方向键；长空格与左右修饰键保留物理比例。Esc 菜单的快捷键入口、模块改绑入口仍分别打开自己的窗口，不跳转或嵌入设置中心。设置中心已有的键位草稿弹窗保持原有归属。

生产入口沿用 `rm_client.ui.jello_keybinds.build_keyboard_manager/open_keyboard_manager`，Endfield 表现层位于 `rm_client/ui/endfield/keybinds/`。2026-10-26 接入 UI 切换后，工厂按当前界面选择：Endfield 使用此键位页，Jello 复用原 `KeyboardCanvas`，两者共用绑定会话及校验。

## 本体依据

从 `W:\yj games\Arknights Endfield\Endfield_Data\StreamingAssets\VFS` 只读导出并重新校验 87 个资源包，生成 84 个按键映射、原版键帽 PNG、弹窗背景及原始曲线。离线工具不会进入运行时。

| 本体资源 | 用途 |
| --- | --- |
| `settings/widgets/keysetting.prefab` | 键位控件：430×100，分隔层 428×82，按键图标高度 52，文字字号 25.55 |
| `settings/gamesettingkeycodepopuppanel.prefab` | 独立输入弹窗：内容参考 900×380，标题字号 36 |
| `animations/setting/keysetting_btn.controller` | 高亮原始 StreamedClip 三次多项式，83.33 ms 到 64/255 透明度；按下态为 0.1 |
| `animations/gamesettingkeycodepopup_in.anim` | 横向展开、0.5 遮罩、内容闪现，约 283.33 ms |
| `animations/gamesettingkeycodepopup_out.anim` | 原始透明度退场，约 166.67 ms |
| `initialassets/ui/sprites/keyicon/icon_key_*.png` | 实际按键键帽图标，覆盖原入口支持的全部 84 个键名 |
| `arts/ui/sprites/commonpopup/pup_up_*.png` | 输入弹窗的背景、阴影和装饰 |

沿用设置表现层已经导出的字体、等高线/点阵材质、黄色选择纹理和下拉动效。完整本体UI资源清单未找到整张实体键盘的预制体，实体键盘几何采用客户端的标准QWERTY适配，未冒称为游戏原版键盘模型。按键本身复用原版键帽及控件曲线，编辑区使用原版字体绘制短标签，保持小窗口可读。没有把游戏引擎或专有 shader 嵌入客户端。`assets/PROVENANCE.json` 保存各源包在本体 VFS 中的偏移与 SHA-256，`MANIFEST.json` 校验整个独立资源包。`evidence/20261026-keyboard/source-reference.json` 记录本次重新读取本体核心包的校验结果。

## 行为与归属

- 点击键帽选择按键，下方横向显示该键的绑定；点击绑定标签的×可清除。主界面不再逐项竖排全部功能。
- 从功能下拉框选择功能后，点击“绑定到此键”写入草稿；搜索会过滤功能候选并在实体键盘上定位匹配的键。
- “按键输入…”保留独立原版输入弹窗；冲突或保留键错误不覆盖其他功能的绑定。
- 改绑和清除均只写入该窗口的 `BindingSession`；“确认快捷键”才使用原配置事务提交。返回、关闭和 Esc 丢弃未提交更改。
- F11 仍属于客户端窗口，F12 始终走既有急停服务；Tab 可在战术地图的原有允许规则内被捕获。
- 设置、快捷键及显示入口作为 Esc 子模态打开。主菜单保持打开，排队和模态期间暂停原生菜单点击；关闭子界面直接返回原菜单。
- 窗口/全屏切换复用设置壳已有的同步覆盖与延后重排，输入弹窗同步跟随其父界面。

## 实体键盘验证

- 11项快捷键测试：84键完整性与命中、五种尺寸（720×540至2560×1440）边界、长键比例、独立方向区、拖动取消、Q/E/Tab真实按下反馈、点击改绑/清除、冲突、取消/确认、嵌套F11/F12、资源与原始曲线。
- 53项兼容/菜单/独立服务测试、11项几何测试、设置界面6项布局与8项滚动测试通过；静态质量检查通过。
- `evidence/20261026-keyboard/native-windowed/report.json` 和 `native-fullscreen-150/report.json`：窗口及150%全屏模式下真实Qt键帽点击、绑定清除、搜索定位、按键输入弹窗、F11往返、F12急停及12次Esc父菜单退回。原生菜单的语义动作返回值使用注入，D3D菜单、Qt窗口和模态路径实际运行；截图来自客户端自有窗口或D3D渲染器，设备/网络传输为无副作用替身。

## 早期竖列版本验证记录

- 8 项独立快捷键测试覆盖全部键帽、冲突后重试、取消、确认提交、保留键、搜索、F11/F12、原版曲线及资源完整性。
- 11 项几何回归、设置界面的 6 项布局及 8 项滚动回归通过。
- 菜单及旧设置相关测试继续通过，旧键盘画布的专用手势用例通过明确的旧版夹具执行。
- `evidence/20261026-native-windowed/report.json` 与 `evidence/20261026-native-fullscreen-150/report.json`：真实 Windows D3D Esc 菜单、客户端窗口和生产模态路径；注入原生语义动作返回值，子界面使用真实 Qt 按钮/键盘事件。两个报告各覆盖设置与快捷键的返回、右上角关闭、Esc 三种退回路径，共 12 次往返，期间原生菜单始终保持请求打开且回到可见阶段。
- 原生验证包括实际按键输入弹窗、F12 单次分发和草稿保留；设备/网络传输为无副作用替身。截图仅来自客户端窗口或其自有 D3D 渲染器。

复核命令：

```powershell
.venv/Scripts/python.exe tools/endfield/keybinds/check_assets.py --asset-root rm_client/ui/endfield/keybinds/assets
.venv/Scripts/python.exe tests/endfield/settings/test_keybinds.py
.venv/Scripts/python.exe tools/endfield/keybinds/verify_desktop.py --out tmp/endfield_keybinds/native
$env:QT_SCALE_FACTOR='1.5'
.venv/Scripts/python.exe tools/endfield/keybinds/verify_desktop.py --fullscreen --out tmp/endfield_keybinds/native150
```
