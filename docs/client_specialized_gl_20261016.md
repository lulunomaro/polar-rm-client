# Specialized GL 资源所有权修复（2026-10-16）

## 范围与证据边界

本阶段以 `37d5048eddfe41d1b5ff03a53f45c9800bd71092` 为起点，仅修改
`StartupGpuCompositor`、`QuickActionWheelGpuRenderer`、`IslandIconGpuRenderer`、
`VisionWireGpuRenderer` 的内部资源边界，以及共享的 `gl_resources.py`。
不修改窗口编排、外层 FBO/compositor、帧遥测、视觉参数或 shader 算法。

被测 production 代码为 `d8599b4`；`51fd911` 进一步固定 shader 来源字节。
环境：Linux 6.18.44 x86_64 / glibc 2.41、Python 3.12.14、PySide6 6.8.2。
测试调用真实 production 方法，只替换 native GL 句柄与 GL functions；
图像测试使用真实 Qt `QImage`。这些结果不代表实际 GPU 驱动、原生窗口、
呈现节奏或 native framebuffer 像素验收。没有关闭 GPU、降低分辨率或更改效果
来通过测试，也没有触发设备、收费 CI 或远端发布。

## 问题、修复与所有权

- **准入拒绝。** shader program / VBO 的 `bind()` 返回 bool，False 不再继续
  绘制或报告成功；Qt VAO / texture 的 bind 以及本阶段的 release / destroy
  是 void 接口，正常的 None 不被误认为失败。FBO 的 bool release 不在这个
  helper 的 void cleanup 契约内。
- **独立 unwind。** `GlDrawBindings` 只接收 program、VBO、VAO 与明确的 state
  reset 操作，不接收窗口。attribute、buffer、VAO、texture、program 和 state
  reset 分别尝试清理。wrapper 在 native 副作用之后抛错的情形也会清理；
  bool bind 正常返回 False 则撤销该绑定的清理登记。成功路径保留原清理顺序。
- **首因与清理分离。** `GlResourceOwner.last_result` 分别记录普通 rejection、
  typed primary fault 与 cleanup faults，复用 `RenderFaultDiagnostics` 保留
  类型、cause 与原 traceback 日志。仅保留最新操作结果，日志按阶段/异常类型
  限频；限频 key 最多 64 个。有限操作次数不意味着 native 驱动调用有时限。
- **release / initialize。** release 首先撤销 available，独立尝试全部 owner，
  完成后重新抛出首个原异常，保留既有 void/异常调用契约。成功的 program /
  texture 引用清空，失败引用保留；buffer / VAO Python wrapper 可复用。
  shader 编译过程中也登记候选 program。初始化失败清理不再覆盖首因；
  再次初始化在旧 program 或待清理 owner 处置成功前不创建替代者。
- **startup 上传候选。** 至多一个 `_pending_texture` 拥有半初始化/上传候选。
  create、storage、filter 或 setData 故障不会丢失引用；候选销毁失败保留至
  后续明确 update / release 重试，每次 update 至多尝试一次旧候选清理。
  尺寸变化时，旧 spare 只有 destroy 成功才清空。上传失败不更改已显示的
  current / previous、sequence、rect 与 texture size。release 清空帧身份，
  重新进入时相同 sequence 也能重新上传。

公开方法的 bool/void 返回契约不变；`_build_program` 与 `_allocate_texture`
作为类内部私有方法现在显式登记构建中候选。没有生产调用的旧 `_new_texture`
helper 保留原样，不把它的存在描述为本阶段已经审核所有潜在纹理入口。

## 可复现阶段

1. `1a82d47`：实际 render/release 反例，7 项测试中 19 个失败子例；
   `0af4552` 修复准入与独立 unwind，并扩展 typed 故障/状态清理测试。
2. `344022a`：initialize / candidate / retry 反例；
   `390a8ae` 保留初始化首因及失败资源，阻止旧 owner 被静默替换。
3. `ddad041`：native 副作用后抛错的回归；
   `b09abe6` 补 program / buffer / VAO / attribute / texture 的部分准入清理，
   并把状态清理标签改成 `disable_blend` 等可定位语义名。
4. `4289a73`：startup 上传候选丢失反例；`d8599b4` 修复候选所有权。
   测试最初错误地把 QImage 的 constBits 指针保存到对象释放之后才读取；
   修正为在模拟 native setData 调用中同步复制后，固定旧树 `b09abe6`
   仍有 4 个失败、4 个错误子例；修复树全部通过。
5. `51fd911`：新增四组 shader 精确字节 SHA 回归，来源仍为原始 `37d5048`，
   没有以修改后 shader 替换基线。

## 验证

以下命令均在仓库根目录、上述隔离环境执行，退出码均为 0：

```sh
export CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1

python -m unittest test_specialized_gl_owners \
  test_specialized_gl_initialization test_startup_texture_ownership
# 28 tests，PASS

python -m unittest test_gpu_warmup_budget test_render_owner_boundaries \
  test_clickgui_renderer_faults test_compositor_plan test_window_wheel_adapters \
  test_render_performance_regressions test_startup_display_locale \
  test_background_display_locale test_startup_settings_fullscreen
# 92 tests，PASS

python tests/gpu_compositor_test.py
# gpu compositor test passed

git diff --check
```

新增三套测试已通过 `tools.test_registry.discover/select` 确认自动登记为 offline。
原始成功路径基线是 `tests/fixtures/specialized_gl_trace.json`：固定真实 render
产生的状态指令、uniform、attribute 布局、draw 顺序及上传顶点字节摘要。
`tests/fixtures/specialized_gl_shader_sources.json` 固定真实 initialize 提交给
编译器的原始 vertex/fragment 文本。真实 QImage 测试另外覆盖一次原点翻转、
RGBA 上传字节、三纹理轮转与 release 后重进，不替代 native driver 渲染。

当前只列直接运行的相关检查。全仓聚合回归由主整合任务遵循共享锁后运行；
不能把本阶段的定向成功改写为整合树的完整 offline/release 通过。
原生桌面 `startup_gpu_runtime_smoke.py` / `gpu_renderer_runtime_smoke.py`
未运行，仍需在可用 OpenGL 上下文中独立验证。

## 独立发现的 texture-unit 修正（07:20 UTC）

这是有意修正旧行为，不是成功指令完全不变的提取。
[Qt 6.8 的 QOpenGLTexture 契约](https://doc.qt.io/qt-6.8/qopengltexture.html#release)
规定：无参 release 解除当前 active unit；带 unit 的重载解除指定 unit。
startup 先 bind current 到0，再 bind previous 到1；旧清理连续两次无参 release，
两次都解除 unit1，留下 unit0 的 current 绑定。

`GlDrawBindings` 现在记录每个已尝试绑定的 `(texture, unit)`，并按原逆序调用
`release(unit)`。仍在 native bind 调用前登记，以覆盖副作用后抛错；各 texture、
program 等清理独立执行。成功的双纹理路径最终解除 unit1 和 unit0，并停留在 unit0。
其它三个 renderer 没有调用这个 texture-admission 方法。

`tests/test_specialized_texture_units.py` 使用遵循上述 API 的状态机，执行真实
StartupGpuCompositor.render。5条测试中4条在修正前失败，修正后全部通过，覆盖成功、
单纹理、draw错误、previous部分绑定、previous清理错误仍解除current。
原 `specialized_gl_trace.json` 不改；对照测试显式只纠正 previous.release(1) 和
current.release(0) 两条参数，其余指令、uniform、几何payload、shader字节继续严格比较。
未生成新的成功基线，也没有声称 fake 状态机证明桌面GL视觉效果。

独立验证：33条专用方法及包含它们的99条相邻方法通过，命令为：

```sh
CI=true QT_QPA_PLATFORM=offscreen POLAR_DISABLE_LOCAL_CAPTURE=1 \
POLAR_DISABLE_MEDIA_PRELOAD=1 PYTHONPATH=.:tests python -m unittest \
  tests.test_specialized_texture_units tests.test_specialized_gl_owners \
  tests.test_specialized_gl_initialization tests.test_startup_texture_ownership \
  tests.test_render_owner_boundaries tests.test_compositor_fault_diagnostics \
  tests.test_framebuffer_generation tests.test_window_normal_frame -v
```
