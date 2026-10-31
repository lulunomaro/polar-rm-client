# Issue #27：CPU 热路径、显卡选择与诊断

> 以下保留 2026-10-26 首轮结果。2026-10-31 的集成修复及最终验收见
> [ISSUE31.md](ISSUE31.md)：已整合最新主线/PR #30，修复原失败测试与发现的
> 集成缺口；75 套所选回归全部通过。原生 DLL 也已重新组合构建，下面旧版号仅为历史记录。

基准为 `main@7c6b4f2a1467ac4ce857cc9031ae8cab06c6f5e5`。没有改液态玻璃
shader/参数、Endfield 美术/布局/动画曲线、裁判协议、KMC 或 F12 急停链路。

## 修复内容

1. MC HUD 的双语言计分板测量、逻辑/屏幕几何改为有界缓存。数值文本改变但
   测量范围不变时保留几何；窗口、倍率或文本范围变化时重新计算。对外返回
   Qt 几何副本，不能通过修改一个 QRectF 污染之后的命中区域。
2. 目标 HUD 不可见且准星关闭时，目标 FBO 清理后直接完成，不再每帧创建、
   栅格化、清理 alpha 并上传一张透明 ROI。可见目标和准星路径保持原样。
3. 添加有界的 OpenGL 初始化/失败计数、GPU 路径/兼容帧计数、最近回退原因、
   实际 GL 驱动及 CPU 提交 P95/P99 诊断。Qt 在 OpenGL FBO 上运行 QPainter
   仍是 GPU 绘制；有 CPU 栅格素材也不代表整个客户端走纯 CPU。软件 GL 名称
   明确标识，不把配置里的“Unified GPU”当作实际设备证据。
4. 视频设置新增 DXGI 硬件设备列表、自动选项、未检测到旧选择的诊断、实际
   OpenGL/Esc D3D 设备、刷新按钮及 Windows 图形设置入口。选择原子保存，
   重启恢复；保存草稿不擅自重建上下文或修改 Windows 注册表。
5. Esc 原生 D3D 真正按 LUID 选择设备。设备缺失/创建失败时尝试系统硬件
   默认并报告原因；不悄悄改用 WARP。设备身份在其自己的原生线程记录。
   原生呈现增加绝对截止时间节奏，跟随视频帧率上限（30–240），不按帧数
   推进动画或补跑积压帧。低于上限时不能据此声称 CPU 一定下降。
6. 重新构建随附 DLL，更新版本指针和哈希清单；构建缓存纳入本地头文件依赖，
   防止改了显卡策略头文件却复用旧对象。原始游戏资源和官方场地材料不变。
   验证构建使用本机已有 Zig 0.14.1 / Clang 19.1.7，编译工具本身不提交。

## 显卡选择的明确边界

**此处显式选择控制 Esc 的 Direct3D 设备，不是假装控制主窗口的 OpenGL。**
Qt 6 的 QOpenGLWidget 使用 WGL，不是 Qt Quick 的 D3D/RHI 路径。
主窗口的显卡路由由 Windows/驱动决定；需要在 Windows 图形设置选择此客户端
可执行文件，重启后核对诊断中的 GL_RENDERER。没有使用无效的 Qt Quick
环境变量冒充切卡，也没有改全局显卡/游戏配置。[Qt 平台说明](https://doc.qt.io/qt-6.8/windows-graphics.html)

Esc 通过 DXGI 枚举及显式 adapter + D3D_DRIVER_TYPE_UNKNOWN 创建设备，
回报真正创建成功的设备；自动模式保留系统默认。[微软设备创建说明](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-d3d11createdevice)

LUID 是本机当前适配器标识。系统重启/驱动重装/移除设备可能令它失效；此时
保留用户选择、明确报告回退并允许重新选择，不按序号偷偷选择另一张卡。
双显卡使用不同设备时可能引入跨适配器合成成本，优先核对两条实际设备名称。
H.264/HEVC 当前仍为 FFmpeg 软件解码；本轮没有伪造硬解或 Video Decode GPU。

## 实测与复现

Windows，RTX 5070 Ti Laptop，2560×1440 DPR1，90 FPS 上限，VSync关闭，
相同仓库默认 HUD，30Hz 合成图像，禁止相机/推理/真实控制。独立目录中运行
旧/新代码，不修改个人配置。预热12秒后采样8秒，无 cProfile：

| HUD 指标 | 基准 | 修复 |
| --- | ---: | ---: |
| CPU 绘制均值 | 6.45 ms | 5.32 ms |
| CPU 绘制 P95 | 10.34 ms | 9.38 ms |
| CPU 绘制 P99 | 11.44 ms | 10.53 ms |
| 实际帧间隔推算 FPS | 86.46 | 88.68 |
| CPU 单核等效占用 | 85.24% | 74.16% |

以上是最终带源文件/采样器指纹的复测。此前同条件一对样本为6.14→5.00ms、
P95 10.33→8.64ms、单核等效81.66→71.74%；波动如实保留在证据中，不用
最高FPS或最好的一次结果代表所有运行。

CPU 单核等效不是整机 CPU 百分比，也不包括另一个 FFmpeg 进程。GPU 3D/
Video Decode 通过 Windows PDH 按本进程的每个引擎采样；未取得计数是 null，
不能当作0。[PDH 数组接口](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw)

启动页、ClickGUI、Esc、设置、快捷键也完成短时采样。它们不都改善：例如
独立 Esc 复核中 CPU 单核等效从约108%到126%，原生实际呈现均约75 FPS，
虽新上限为90也没有提速；不能宣布整个客户端 CPU/FPS 已达成验收。
这些短样本也不足以区分温度/驱动/预热波动。原生节奏控制的作用是限制
不受限情况下的提交，并不是这次 HUD 提速的来源。

复现命令（输出目录必须新建；基准需明确准备的未修改 checkout）：

```powershell
python tools/profile_hud_live.py --source-root BASELINE --out BEFORE --width 2560 --height 1440 --warmup 12 --seconds 8 --fps-limit 90 --vsync 0
python tools/profile_hud_live.py --out AFTER --width 2560 --height 1440 --warmup 12 --seconds 8 --fps-limit 90 --vsync 0
python tools/diagnostics/compare_mc_cache.py --baseline-root BASELINE --out mc-parity.json
python tools/diagnostics/verify_graphics_selection.py --out NEW_NATIVE_OUTPUT
```

采样器还支持 `--scene startup/clickgui/escape/settings/keybinds`，记录Qt绘制
事件、现有各绘制 pass 的 CPU 耗时、进程 CPU 与 GPU 引擎。合成帧会经过实际
OpenGL 呈现，但不是图传解码验收；`--webcam`/`--local-vision` 仍需明确选择。

## 验证与未完成项

- 新自动测试覆盖偏好迁移/持久化、单/双卡枚举、失效卡与枚举错误、初始化
  失败、真实设备诊断、启动前配置、节奏更新、缓存容量/失效和透明 ROI。
- 与未修改基准源码比较24对MC画面：四种分辨率、倍率和DPR 1/1.5/2，
  文字/像素及功能栏命中几何相同。不是全客户端逐像素或动画录像认证。
- 本机实际 RTX/Intel D3D 选择及不存在设备回退均有非空自有渲染输出、正常
  owner 退役证据；不把枚举成功或 Mock 测试当成切卡成功。
- 所选回归、已知基准失败及原生/OpenGL结果见
  `evidence/issue27-20261026.json`，不隐藏未通过项。
- 最终所选33套：32 PASS、1 FAIL、0 SKIP、0 INCOMPLETE；32套通过用例合计
  323项。本次新增15项全部通过，真实OpenGL两套冒烟及原生三场地六个尺寸
  组合通过。整体验证仍不能标成全绿。
- `test_cs2_crosshair_import` 的四个小窗口滚轮子案例仍失败；在未修改的
  基准上逐一复现相同失败，不属于本次显卡/缓存修改。没有删掉或放宽断言。
- 未做另一台电脑、驱动故障注入、正式图传硬件/真实压缩流的端到端性能和
  比赛现场验证；未增加硬件解码。本PR不应自动关闭整个 Issue #27。
- 个人 default.json、旧私有素材包、游戏安装目录保持不动；没有推送配置或
  本机临时输出/编译工具。
