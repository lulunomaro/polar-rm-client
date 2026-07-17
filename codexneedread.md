# POLAR-RM Custom Client — 完整变更说明（给 Codex / 后续 Agent / 接手人）

> 本文汇总本会话**全部**改动：动机、根因、改了什么、怎么改、如何验证与回退。  
> 路径：`rm_custom_client/codexneedread.md`  
> 代码主目录：`rm_custom_client/`

---

## 0. 项目极简地图

| 项 | 说明 |
|----|------|
| 启动 | `main.py` → `rm_client.ui.polar_app.main()` |
| 主窗口 | `PolarRMWindow(QOpenGLWidget)`，标题 **POLAR-RM Custom Client** |
| 启动一级 | `startup_step == "scenario"` 场景选择 |
| 启动二级 | `startup_step == "match"` Match Mode（Step 2/2） |
| 进主界面 | 选 RMUC/RMUL 后 `home_visible=False`，`startup_step="done"` |
| 启动背景 | OpenCV 线程解码视频 + `StartupGpuCompositor` 全屏 GL 纹理 + QPainter 玻璃 UI |
| 依赖 | PySide6 / OpenCV 等，见 `requirements.txt` |

启动页 paint 大致顺序：

```text
on_tick → update() → paintGL
  ├─ glClear(主题深色)
  ├─ beginNativePainting → GPU 视频背景 → endNativePainting
  └─ QPainter 画一级/二级菜单（liquid glass）
```

---

## 1. Bug：启动立刻弹 `setUniformValue` 长错误框

### 1.1 现象

窗口标题 **POLAR-RM Custom Client**，弹窗正文类似一长串：

```text
PySide6.QtOpenGL.QOpenGLShaderProgram.setUniformValue(location:int, value: QMatrix3x3 / QVector2D / QPoint / ...)
```

**不是**矩阵类型写错，而是 PySide6 **TypeError 重载列表 dump**（弹窗换行/字体乱时更像乱码）。

### 1.2 根因

文件：`rm_client/ui/startup_gl.py` → `StartupGpuCompositor.render()`

旧代码：

```python
self.program.setUniformValue("blend", blend)   # str + float
self.program.setUniformValue("dim", self.dim)  # str + float
```

本地 **PySide6 6.11.1** 实测：

| 调用 | 结果 |
|------|------|
| `setUniformValue("name", int)` | OK |
| `setUniformValue("name", QVector4D)` | OK |
| `setUniformValue("name", float)` | **FAIL TypeError** |
| `setUniformValue(int_location, float)` | OK |
| `setUniformValue1f(location, float)` | OK |

链路：`initializeGL` 成功 → `paintGL` → `render()` → `setUniformValue(str, float)` 抛错未捕获 → 弹窗。

### 1.3 改法

**`rm_client/ui/startup_gl.py`**

1. link 后缓存 uniform location：`currentTex/previousTex/currentUv/previousUv/blend/dim/hasPrevious`
2. 使用：
   - sampler/int → `setUniformValue1i(loc, int)`
   - float → `setUniformValue1f(loc, float)`
   - vec4 → `setUniformValue(loc, QVector4D(...))`
3. `render()` 包 `try/except`：失败则 `available=False`、写 `last_error`、返回 `False`，走 QPainter 回退

**`rm_client/ui/polar_app.py`**

- `draw_startup_gpu_background()` 再包一层 `try/except` 双保险

### 1.4 思路

- 不依赖 PySide 是否绑定 `name:str + float` 重载（版本易踩坑）
- 与 Qt 官方示例一致：先 `uniformLocation` 再设值
- 失败降级，不崩 UI

---

## 2. 体验：启动页「卡卡的」流畅度优化

### 2.1 卡顿热路径（优化前）

```text
解码线程：decode → resize → 双 GaussianBlur → 3× QImage.copy
主线程 paint：new QOpenGLTexture + setData（每新帧）
            + liquid_glass.update
            + 整页玻璃按钮 QPainter
FrameScheduler：启动页强制 ~60Hz（源视频常 24/30fps）
默认 Motion Quality = Smooth60（像素预算偏大）
```

| 瓶颈 | 位置 |
|------|------|
| 高清源每帧 resize + 双 blur | `startup_wallpaper.VideoWallpaperPlayer._prepare_frames` |
| 三份 `QImage.copy()` | `_to_qimage` |
| 纹理不复用 | 旧 `startup_gl._new_texture` 每帧 create |
| 60Hz paint | `startup_needs_high_rate` + `FrameScheduler` |
| 默认 Smooth60 | `model.py` / `configs/default.json` |

### 2.2 改了什么

#### A. `rm_client/startup_wallpaper.py`（解码降载）

1. **像素上限**
   - Smooth60 → `1280×720`
   - Balanced30 → `960×540`
   - Source → `1600×900`
   - 再与窗口 `target_size` 取 min

2. **缩放插值**：大幅降采样 `INTER_AREA`；小幅 `INTER_LINEAR`

3. **玻璃图更小 + 隔帧 blur**（Source 每帧；其它隔一帧）

4. **发布帧率封顶**：Balanced≈30；落后时 `capture.grab()` 跳帧

5. 默认 quality 回落字符串改为 `Balanced30`

#### B. `rm_client/ui/startup_gl.py`（纹理）

1. 上传前 `Format_RGBA8888`
2. 交叉淡入：旧 current → previous；更旧 previous 先 destroy；**新帧始终 create + 一次 setData(QImage)**  
   （曾尝试同对象二次 `setData(QImage)` 复用 → 见 **§3b**，已回退）
3. 淡入完销毁 previous
4. 仍保留 location + `1i/1f` 修复

#### C. `rm_client/ui/polar_app.py`（刷新）

1. `FrameScheduler.should_request(..., target_fps=...)`
2. `startup_target_fps()`：Balanced→30，Smooth→48，Source→36 等
3. 启动页按 `target_fps` 请求重画
4. `liquid_glass.update` 仅 `sequence` 变化时刷新；视差只改 `draw_rect`

#### D. 默认档位

| 文件 | 改动 |
|------|------|
| `rm_client/model.py` | Motion Quality 默认 **Balanced30** |
| `configs/default.json` | 同步 Balanced30 |
| `tests/smoke_test.py` | 断言默认 Balanced30 |

用户仍可在设置切回 Smooth60。

### 2.3 思路

先砍像素与 blur → 对齐解码/UI 帧率 → 复用 GL 纹理 → 默认给「能跑顺」档。

未做：硬解、液体玻璃 GLSL 化、战斗 HUD 重构。

---

## 3. Bug：进入二级界面瞬间花屏

### 3.1 现象

一级场景选择 → 点场景 / 按键进入 **Match Mode（二级）** 时，**花屏 / 闪一下**（脏色、闪黑或撕裂感）。

### 3.2 根因（叠加）

1. **QOpenGLWidget 默认清屏**成未控颜色；当帧 GPU 背景晚到则闪  
2. **裸 GL + QPainter 混用**无 `beginNativePainting`：页面切换时 GL 状态（纹理绑定、blend）脏，QPainter 接住一帧花屏  
3. **动画硬 reset 到 0**：二级 UI（含 vignette）整层 `opacity=0`，再淡入；若背景当帧没铺满 → 像空闪  
4. **无上一帧保底**：`prepare_gpu_video` 返回 None 时直接失败，露出清屏  
5. **离开 home** 时曾用 `CompositionMode_Clear` 透明清屏，也易闪黑  

### 3.3 改法

#### `rm_client/ui/polar_app.py`

1. **`paintGL`**
   - `glClearColor` 主题深色（home `#060910` / 主界面 `#0b121b`）
   - `glClear(COLOR_BUFFER_BIT)`
   - home 时：`QPainter.beginNativePainting()` → GPU 背景 → `endNativePainting()` → 再 `paint_overlay`

2. **`draw_startup_gpu_background` 保底**
   - `snapshot is None` 但 `startup_gpu.has_frame`：用 `touch_rect_dim` 更新视差矩形/dim，**继续 render 上一帧纹理**

3. **页面切换**
   - `reset_startup_page_animation`：open 动画从 **0.18** 起（不是 0）
   - `request_startup_repaint()`：`frame_scheduler.force()` + `update()`
   - 绑定到：`select_scenario` / `startup_back` / 键盘进出二级 / 选 match 进主界面 等

4. **离开启动页**
   - 去掉透明 `CompositionMode_Clear`，改为实色 `#0b121b` 铺底再画图传

5. **清理重复定义**
   - 删除重复的 `startup_*_meta` / `reset_startup_page_animation`，保留中文 meta + 新 reset

#### `rm_client/ui/startup_gl.py`

1. `has_frame` / `touch_rect_dim`  
2. `_reset_gl_state(functions)`：解绑纹理、`glUseProgram(0)`、关 depth/stencil/scissor/blend（交给 QPainter 重建）  
3. `render()` 成功与失败路径末尾都尽量复位 GL 状态  

### 3.4 思路

花屏本质是 **「清屏色 + 脏 GL 状态 + 缺帧」** 在页面切换帧叠在一起。  
修法：稳定清屏色、NativePainting 边界、状态复位、上一帧保底、切换立刻重画、动画不要从全 0 开始。

---

## 3b. Bug：全屏背景全黑 + `storage has been allocated` 刷屏

### 3b.1 现象

- 启动页**只有液态玻璃按钮上**能看到壁纸，其它区域全黑  
- 日志大量：

```text
Cannot resize a texture that already has storage allocated.
Cannot set mip levels on a texture that already has storage allocated.
QOpenGLTexture::setFormat(): Cannot change format once storage has been allocated
```

### 3b.2 根因

流畅度优化里错误实现了「纹理对象复用」：

```python
# 错误：对已分配 storage 的纹理再次 setData(QImage)
texture.setData(prepared)
```

`setData(QImage)` 会设置 size / format / mip 并**分配 storage**。  
已分配后再调用 → Qt 拒绝 → 像素上传失败 → GPU 全屏采样到黑。  
同时 `startup_background_ready=True` 会**跳过** CPU 全屏壁纸回退。  
玻璃仍正常：用的是 CPU 侧 soft/lens QImage，不走坏掉的 GL 纹理。

### 3b.3 改法

**`startup_gl.py`**

- 删除「同对象二次 setData(QImage)」路径  
- 新帧：`destroy(旧 previous)` → **新建** `QOpenGLTexture` → **仅一次** `setData(QImage)`  
- 旧 current 挪到 previous 做交叉淡入（对象不复写像素）  

**`polar_app.py`**

- GPU `render` 失败时返回 False，本帧走 CPU `draw_startup_background`，避免「只剩玻璃有图」  
- 纹理错误不永久 `available=False`（下一帧可恢复）

### 3b.4 思路

Qt 文档：storage 分配后不能改 size/format/mip。  
若以后要复用纹理，必须用 **PixelFormat + bits 的 setData**（只写像素），或 destroy 后 recreate。  
**禁止**对存活纹理反复 `setData(QImage)`。

---

## 3c. Bug：修完 storage 后仍全黑（自跑截图确认）

### 3c.1 自测方法

用 `tmp_selfcheck_*.py` 启动 `PolarRMWindow`，`grabFramebuffer()` + 采样中心像素：

| 路径 | 中心 RGB | 结论 |
|------|----------|------|
| 视频 CPU 帧 / `draw_startup_background` | ~140–220（有色） | 解码正常 |
| GPU FBO（修前） | ~6–26（清屏色） | GL 全屏没画上 |
| 无 VAO 的最小 tex 测试 | 0,0,0 | Core Profile 绘制无效 |
| **有 VAO** 最小 tex 测试 | L 红 / R 绿 | **根因确认** |

### 3c.2 根因

`main()` 设置 **OpenGL 3.3 Core Profile**。  
Core Profile 下 **必须绑定 VAO** 才能 `glDrawArrays`；`StartupGpuCompositor` 原先只有 VBO、无 VAO → draw 被忽略 → 只剩 `glClear` 深色 + 液态玻璃 CPU 采样（所以「只有玻璃有壁纸」）。

另：高 DPI 下应 `glViewport(0,0,width*dpr,height*dpr)`。

### 3c.3 改法

**`startup_gl.py`**

- `QOpenGLVertexArrayObject`：initialize 时 create/bind，配置 attribute，release  
- `render` 时 `vao.bind()` → draw → `vao.release()`  
- `render(..., pixel_width, pixel_height)` 设置物理像素 viewport  
- 仍禁止二次 `setData(QImage)`  

**`polar_app.py`**

- 调用 render 时传入 `devicePixelRatioF()` 换算的物理尺寸  

### 3c.4 文档文件名

`codexneedread.me` → **`codexneedread.md`**

### 3c.5 壁纸上下颠倒

OpenGL 纹理 V 轴与 QImage 相反。顶点着色器改为：

```glsl
uv = vec2(position.x * 0.5 + 0.5, 0.5 - position.y * 0.5);
```

自测：CPU 帧与 GPU FBO 上下亮度带符号一致 → 方向对齐。

---

## 4. 涉及文件总表（全会话）

| 文件 | 改动摘要 |
|------|----------|
| `rm_client/ui/startup_gl.py` | setUniform 修复；**Core VAO**；viewport(dpr)；安全 create+setData；淡入 previous；GL 状态复位；has_frame/touch_rect_dim |
| `codexneedread.md` | 完整变更说明（原 `.me` 已改名为 `.md`） |
| `rm_client/ui/polar_app.py` | GPU 背景 try；帧率调度；玻璃节流；paintGL 清屏+NativePainting；保底帧；切换 repaint；动画软起点；离开 home 实色底；GPU 失败回退 CPU；去重复方法 |
| `rm_client/startup_wallpaper.py` | 解码分辨率/blur/隔帧/跳帧/默认 Balanced |
| `rm_client/model.py` | Motion Quality 默认 Balanced30 |
| `configs/default.json` | 持久化默认 Balanced30 |
| `tests/smoke_test.py` | 默认值断言 |
| `codexneedread.md` | **本文档**（完整变更日志） |

---

## 5. 如何验证

### 5.1 setUniform 修复

- 启动客户端：无 TypeError 弹窗  
- 启动页背景可见（GPU 或 CPU 回退均可）

### 5.2 流畅度

- 默认 Balanced30：视差/菜单更跟手，CPU 更低  
- 可手动切 Smooth60 对比清晰度  

### 5.3 二级界面花屏

1. 启动 → 一级场景选择  
2. 点击任一场景 / 按 1–3 → **二级 Match Mode**  
   - 期望：**无瞬间花屏、脏色、闪黑**  
3. Backspace 回一级：同样干净  
4. 选 RMUC/RMUL 进主 HUD：过渡可接受、无长闪  

### 5.3b 全屏壁纸（非仅玻璃）

1. 启动后**整屏**应看到视频/壁纸，不是只有按钮上有图  
2. 控制台**无** `storage has been allocated` 刷屏  
3. 一级/二级切换后背景仍完整  

### 5.4 快速 import

```bash
cd rm_custom_client
py -3 -c "from rm_client.ui.startup_gl import StartupGpuCompositor; from rm_client.ui.polar_app import PolarRMWindow; print('ok')"
```

### 5.5 默认值

```bash
py -3 -c "import sys; sys.path.insert(0,'.'); from rm_client.model import build_default_registry; print(build_default_registry().get('StartupVisual').get_setting('Motion Quality').value)"
# 期望: Balanced30
```

---

## 6. 回退建议

| 目标 | 做法 |
|------|------|
| 只要更清晰背景 | UI 里 Motion Quality → Smooth60 |
| 回退默认 60 档 | `model.py` + `configs/default.json` 改回 Smooth60 |
| 回退性能改动 | 还原 wallpaper / scheduler / 默认档相关 diff；**建议保留 setUniform 与花屏修复** |
| 回退花屏修复 | 还原 `paintGL` NativePainting / 保底帧 / 实色底；可能再次出现切换闪一下 |
| 再踩 storage 黑屏 | 检查是否又对已分配纹理 `setData(QImage)` |
| 完全回退 | git 还原上表文件 |

---

## 7. 已知限制 / 后续可选

1. 硬解（QtMultimedia / ffmpeg）进一步卸 OpenCV CPU  
2. 玻璃 blur 改 GPU shader  
3. 正确的纹理复用：同尺寸时用 `setData(PixelFormat, PixelType, bits)`，**不要** `setData(QImage)`  
4. PBO 异步纹理上传  
5. 静止 UI 脏矩形减少重绘  
6. 按显示器刷新率自适应 `startup_target_fps`  

---

## 8. 会话时间线

1. 诊断并修复 **`setUniformValue(str, float)` TypeError**  
2. 分析并优化 **启动页卡顿**（解码/纹理/帧率/默认档）  
3. 写出初版 `codexneedread.me`  
4. 诊断并修复 **二级界面瞬间花屏**（清屏 + NativePainting + 保底帧 + 切换 repaint）  
5. **扩充本文**为全量变更说明  
6. 修复 **storage allocated** 误用 setData(QImage)  
7. **自跑截图**确认仍黑：根因 Core Profile **缺 VAO**；补 VAO + dpr viewport  
8. `codexneedread.me` 重命名为 **`codexneedread.md`**  

---

## 9. 模块开关音效（Polar 中性音）

### 调研

| 来源 | 模块开关专用音？ | 素材真实用途 |
|------|------------------|--------------|
| LiquidBounce | 否 | `click-*` 等属 **HitFX**；开关只 Notification |
| OpenZen | 否 | `truth/lie.wav` 属 **LieDetector** |
| 旧 client 默认 Zen | 误用 | 听感「抽象」；ogg 本机常解码失败 |

### 改动

- 新增 `assets/ui_sounds/polar/{enable,disable,invalid}.wav`（`tools/gen_ui_sounds.py`）
- `UiAudioManager` 主题默认 **Polar**；Zen / LiquidBounce 保留为 Legacy
- `model` / `default.json` / smoke 断言同步

### 验证

- AudioFeedback → Preview Enable/Disable/Invalid  
- 切换模块：开高关低可区分，无 ogg 解码刷屏（Polar）

---

## 10. 启动页视觉 / 动效 token（落地）

按此前建议已实现高性价比项（非全量重设计）：

| 项 | 实现 |
|----|------|
| 动效 token | `MotionTokens.HOVER/OPEN/PAGE/PARALLAX` + `soft_back_out` |
| 玻璃 idle/hover | 静止几乎无 lens 位移；hover 才加强 |
| 表面分档 | main / sub / **tool**（底栏、PROFILE） |
| 主 CTA vs 底栏 | 底栏更矮更淡 ToolGlass；主按钮入场位移收短 |
| 页面交叉入场 | `PAGE_ENTER_FROM=0.22` + 更短 stagger |
| 交互提帧 | `mark_ui_interactive`：hover/转场时抬到 ~56fps |
| 层级 | 冷白 text、muted 略暗；glow 减弱；步骤点 ●○ |

---

## 11. 战斗 UI 性能优化

战斗页卡顿主因：无脑高帧 + 每帧全屏假图传/相机刷新 + **每个 HUD 面板 liquid glass** + 玻璃源每相机帧 update。

| 项 | 做法 |
|----|------|
| 帧率 | `combat_target_fps`：闲 30 / 忙 52–58 |
| lite | `hud_perf_lite`：CombatCompact/Balanced 默认 lite；1080p 也触发 high_res 阈值（1.4e6） |
| 图传 | 合成视频默认缓存缩放绘制；缓存间隔 0.12–0.18s |
| 玻璃源 | `update_hud_liquid_source` 降采样+节流 90–140ms |
| 玻璃 draw | HUD 面板 SubGlass/lite；status **仅外壳玻璃**，条目级玻璃 lite 关闭 |
| 绘制 | lite 关 AA / SmoothPixmap；能量 mock 仅启用时 process |

自测：进入 `home_visible=False` 后 `paint_ms_avg` 与 `combat_target_fps` 正常。

---

## 12. 液态玻璃恢复（liquid-glass-react，非 ReGlass）

用户明确：**不要 ReGlass**，用 **liquid-glass-react-master**。

### 对齐 props

| react | 本端 |
|-------|------|
| displacementScale | 边缘 soft/lens 弯折 |
| blurAmount / saturation | 霜面 soft + SoftLight 叠色 |
| aberrationIntensity | 边缘 RGB 分离 Screen 叠 |
| elasticity | hover/press 形变 |
| mode standard/polar/prominent | `_MODE_PRESETS` |
| overLight | 亮度采样抬霜感 |

### 纠错

- 不再用 `hud_perf_lite` **强制关掉** 玻璃 lens  
- 默认 **Glass Quality = Full**（UIScale 可切 Balanced/Lite）  
- theme 改为 iOS Dark system 色（systemBlue 等）  

参考路径：`liquid-glass-react-master/liquid-glass-react-master/src/index.tsx`

---

## 13. 液态玻璃全 GPU（OpenGL）

用户要求：**全部 GPU / OpenGL 提帧**。

| 组件 | 路径 |
|------|------|
| `glass_gl.GlassGpuCompositor` | 圆角玻璃 fragment：多采样 blur + 边缘折射/色散 |
| 启动壁纸 | 仍 `startup_gl`，再 `glCopyTexImage2D` 拷屏作玻璃源 |
| 战斗底图 | `upload_scene` + `blit_scene_fullscreen` |
| `LiquidGlassRenderer.draw` | prefer_gpu 时 `beginNativePainting` → GPU 面板，失败回退 CPU |
| 文字/图标 | 仍 QPainter 叠在玻璃上 |

Glass Quality=Lite 时关闭 GPU 玻璃（回退轻量 CPU）。

---

## 14. 给后续 Agent 的注意点

- 改 `paintGL` 时：**GL 绘制必须包在 `beginNativePainting` / `endNativePainting` 之间**，再交给 QPainter  
- 改 uniform：优先 **location + setUniformValue1i/1f**，不要假设 `str+float` 绑定存在  
- **`QOpenGLTexture.setData(QImage)` 只能对新建纹理调用一次**；更新像素用 PixelFormat 重载或 destroy+create  
- **OpenGL 3.3 Core 必须 VAO**；改启动页 GL 后务必 `grabFramebuffer` 自测中心像素不是清屏色  
- 模块开关音默认用 **Polar WAV**；不要把 LieDetector / HitFX 素材当正式开关音  
- 启动页性能：优先动 `VideoWallpaperPlayer._prepare_frames` 和 `startup_target_fps`，不要只加 timer  
- 改 `polar_app.py` 前先 **搜是否已有同名方法**（本文件曾有重复定义覆盖问题）  
- 改完请同步更新本文件对应章节  

---

*文档与代码应同步维护；再改启动页时优先更新第 4、5、8、9 节。*
