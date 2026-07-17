# 音频资源来源

## Polar 中性 UI 开关音（默认）

`polar/enable.wav`、`disable.wav`、`invalid.wav` 由本客户端脚本
`tools/gen_ui_sounds.py` 合成（短促正弦+谐波），用于模块开启/关闭/无效操作反馈。
**非** LiquidBounce / OpenZen 上游资源；可重新运行该脚本复现生成。

## LiquidBounce 点击音效（Legacy 主题）

`liquidbounce/click-1.ogg`、`click-2.ogg`、`click-3.ogg` 来自 LiquidBounce Nextgen
项目的 `assets/liquidbounce/sounds/click/` 目录。

**上游用途是 HitFX（命中特效）音效池，不是模块开关音。**  
本客户端仅在 Sound Theme = LiquidBounce 时作为可选彩蛋映射。

LiquidBounce 以 GNU General Public License v3.0 发布；完整许可证见
`LICENSE-LiquidBounce-GPL-3.0.txt`。

## OpenZen 提示音（Legacy 主题）

`openzen/truth.wav` 与 `openzen/lie.wav` 来自 OpenZen 的
`assets/zen/cloud_assets/`。

**上游用于 LieDetector（测谎仪 HUD）的真/假提示，并非模块切换音效。**  
本客户端仅在 Sound Theme = Zen 时作为可选彩蛋映射。

## GD656 击杀音效

击杀音效位于 `../kill_fx/gd656/sounds/`，来自本地 GD656 Kill Icon 项目资源。
客户端保留原始文件名和预设映射，不对音频内容进行二次处理。
上游 README 声明项目采用 MIT 许可证；原始说明随资源保存在
`../kill_fx/gd656/SOURCE-GD656-README.md`。
