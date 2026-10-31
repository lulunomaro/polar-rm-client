# Endfield UI 功能封装

2026-10-25：终端 Wiki、原生 Esc、启动展示和可选语音统一收拢到 `rm_client/ui/endfield/`。既有终端模块、设置对象和客户端动作路由继续复用；通用窗口、GPU 合成、磨砂和后端保留各自职责。

2026-10-26：右侧 UI 切换已接通，确认后自动关闭并拉起，支持 Endfield / Jello 双向切换；详见 [UI 切换](../escape_menu.md#ui-切换2026-10-26)。当前原生版本为 `build_e50d62220b6001bb`，只交付 CURRENT 指向的一代 DLL。下文资源迁移数量与旧构建记录为当时快照。

## 目录与边界

| 目录或文件 | 职责 |
| --- | --- |
| `rm_client/ui/endfield/lifecycle.py` | 展示对象装配、每帧反馈更新；窗口通过此接口接入 |
| `rm_client/ui/endfield/wiki/` | 六大类终端、路径、搜索、设置展示、输入和动画；资源位于内部 `assets/` |
| `rm_client/ui/endfield/wiki/ime.py` | 搜索输入法提交、预编辑与候选框查询；窗口只转发事件 |
| `rm_client/ui/endfield/menu/` | Esc 原生桥接、客户端动作适配与动作标识；资源位于内部 `assets/` |
| `rm_client/ui/endfield/changelog/` | 原版游戏公告页的本地更新日志、字体、静态资源与独立模态边界；见 [公告适配](changelog/README.md) |
| `rm_client/ui/endfield/boot.py`、`feedback.py`、`dialogs.py` | 启动展示、可选语音策略、独立对话框 |
| `native/endfield/` | D3D 桥接与原生目录扩展源码 |
| `tools/endfield/` | 离线准备、编译、导入和验收工具；Wiki、Esc 专用工具分子目录 |
| `tests/endfield/` | Endfield 专项回归；Wiki 专项在 `wiki/` |
| `docs/endfield/` | 接入文档、来源和按日期保存的验证证据 |
| 仓库外本机归档 | 解包中间件、编译依赖与临时报告，不是运行依赖；Git规则不再隐藏本地文件 |

窗口装配与通用输入等位置仅保留接入点，运行路径由 `RuntimePaths` 统一提供。`JelloSigma` 等既有配置 ID、状态字段和兼容接口保留，避免旧配置失效。跨主题的图传交付报告保留原位置，入口见[跨电脑交付与图传恢复](../endfield_delivery_video_reconnect_20261022.md)。历史证据保持原始字节，其中记录的旧目录表示当时的验证环境。

## 资源交付

| 运行方式 | Wiki 资源 | Esc 资源 |
| --- | --- | --- |
| 源码 | `rm_client/ui/endfield/wiki/assets` | `rm_client/ui/endfield/menu/assets` |
| 便携版 | `resources/assets/endfield/wiki` | `resources/assets/endfield/menu` |

发布脚本复制并校验两个功能包。Wiki 共 250 个文件；Esc 完整性清单记录 60 个文件，加上清单自身共 61 个。当前全部 311 个资源文件均未被 Git 忽略。306 个迁移前原始资源文件的 SHA-256 保持一致；菜单顶层清单和 `native_renderer/CURRENT.json` 更新，并新增一代原生构建。既有版本保留，由 CURRENT 选择生效版本。

显式准备的 `user_data/endfield_pack` 仍可覆盖 Esc 随附包；可选启动画面和语音也仍使用用户目录。普通终端和 Esc 不依赖本机游戏、解包目录、临时编译器或个人配置。迁移验收还按发布目录装配了一份资源包，两个资源树的哈希与源码包一致；本轮未构建完整 EXE。

## Esc 的 UI切换占位项

右侧功能栏末尾新增独立卡片“UI切换”（英文 `UI Switch`），不替换现有条目。卡片沿用现有原生图标样式、布局、悬停、点击及滚动反馈。客户端动作编号为 21：原生侧只播放反馈而不发送功能动作；Python 接入层也阻止该编号关闭菜单或进入其他页面。本轮未实现界面选择、配置保存或实际切换。

扩展在构建时应用到固定上游源码的派生编译单元，不修改上游检出文件。原生身份摘要同时覆盖 `bridge.cpp` 与 `catalog_extension.inc`。当前构建为 `build_5207dce9f08940f9`，DLL SHA-256：

```text
cb2523924a387dd2f3316a439ceac490baf96dd4f9fac49975506e75ba091142
```

运行中的客户端需要重启，才能加载新的原生 DLL。

## 验证与维护

21 组相关回归检查全部通过；静态质量检查通过，无初始化依赖环。实际 Windows Qt/OpenGL/D3D 检查覆盖 Wiki 全部分组、搜索、设置、拖动与返回，以及 Esc 原有动作、惯性、全屏和新占位卡片。1280×720 中文与 1920×1080 英文的独立 D3D 检查均确认：点击占位项不分发动作、菜单保持打开。证据见 [20261025-encapsulation](evidence/20261025-encapsulation/README.md)。

从仓库根目录执行：

```powershell
python -m tools.endfield.wiki.check_assets
python -m tools.endfield.prepare_pack check --pack rm_client/ui/endfield/menu/assets
python -m tools.endfield.menu.verify_placeholder --pack rm_client/ui/endfield/menu/assets --out tmp/endfield/placeholder
python -m tools.endfield.verify_desktop --help
python -m tools.endfield.wiki.verify_desktop --help
```

更多细节见 [Wiki 终端](wiki/README.md)、[Esc 惯性](menu/README.md)、[原生接入](SOURCE_PORT.md)、[离线资源准备](PACK_SETUP.md)。
