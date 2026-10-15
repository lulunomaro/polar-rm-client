# 连续击杀音效修复（2026-10-15）

基线：`9599a1c5fd8742fd5dd53c1b57fec1effd4827bd`，已包含 PR #9。

## 已复现的原因

`Test Elimination` 每次使用相同的 `victim_id=101`。默认开启 Lock Target
Bonus 时，5 秒内再次出现同一目标会附加 `locked_target` 标签。旧的
`KillFxRenderer._play_sound()` 随即只播放 `addscore_df.ogg` 并返回，跳过
用户选择的击杀/连杀音效。

使用真实的 preview → accept_combat_event → renderer 方法，在
100、101、102、108 秒触发：

- 修复前：普通击杀、仅加分音、仅加分音、普通击杀。
- 修复后：普通击杀、普通击杀 + 加分音、普通击杀 + 加分音、普通击杀。
- Combo 的六连杀预览在 4 秒后重播，旧代码的第二轮全部变成加分音；
  新代码保留 1–6 阶段各自的击杀音效。

原始 OGG 解码为浮点 PCM 后，`addscore_df.ogg` 的 RMS 为约 −58.68 dBFS，
而 `killsound_df.ogg` 为约 −17.79 dBFS，相差约 40.88 dB。
因此这里的“后续无声”有可复现的选音原因，并非已证明 Qt 无法重播文件。
测试使用原始字节，Git blob 分别为
`1a6a034e04cde9082f34dacdf624c6a2525234b0` 和
`8700efcfa78d2c289a5443908d57270ddeed4c6f`。

另外发现媒体备用池扩容时仍使用旧游标：第二个请求创建新播放器，却再次
选中第一个播放器。首次八个请求只用到七个播放器，第一条音效会被提前
停止。加入加分音叠层后尤其需要修正这一点。

## 改动

- 主击杀音始终保留；锁定目标加分音作为额外一层播放，不再替换主音效。
- 已成功播放的 CS2 Native 音效路径保持原有行为。
- 媒体池扩容时使用刚创建的播放器，满池后保持有界轮转。
- 不修改音频素材、音量增益、击杀判定、去重窗口、奖励标签或设置。
- 原有音频测试改用 QGuiApplication，匹配音频池的焦点状态信号订阅。

## 自动验证

```bash
python tests/test_audio_replay_regressions.py
QT_QPA_PLATFORM=offscreen python tests/test_kill_audio_regressions.py
python -m py_compile rm_client/ui_audio.py rm_client/kill_fx.py tests/test_audio_replay_regressions.py tests/test_kill_audio_regressions.py
git diff --check
```

- 新增 17 项测试全部通过：真实预览调用链、36 个预设各 12 次重复击杀、
  连杀级别、关闭奖励、不同目标、过期窗口、静音、死亡、CS2 Native、
  加分文件缺失、异步加载中的播放器、满池轮转及完成后的重复播放。
  这些使用生产方法与明确的测试替身，不声称验证扬声器输出。
- 现有音频测试 15/15 通过，包含 PCM 转换、线程回调、Ready 等待、
  失败回退、缺设备提示和 2,160 项预设/类型/连杀资源映射。
- 原始 OGG 通过真实 Qt 6.8.2 / FFmpeg 解码：单播放器、八播放器池、
  预加载池各 12/12 次播放得到完整的 1.368 秒 PCM，共 36/36。
- 16 次、间隔 70 ms 的突发播放：首次八次使用八个独立播放器；旧代码
  只有七个。第一条解码长度由约 0.069 秒恢复到约 0.577 秒，随后因
  八播放器容量被正常轮换；末轮八条均完整解码。
- 真实生产选音方法加 Qt 媒体解码：10 次间隔击杀和 10 次快速击杀分别
  得到 10 条主音效 + 9 条加分音，每组 19/19 条均有非零 PCM。
- Default → Combo 1 → Combo 2 → Card → Default 的原始素材切换 5/5 通过。
- 静音/恢复、主音量静音/恢复、缺文件后正常文件、QSoundEffect Error
  回退到媒体播放也通过。

## 验证边界与人工验收

本次环境没有物理音频输出设备；真实 Qt 解码通过不等于用户 Windows
扬声器已验收。PR #9 的正常击杀路径为 OGG → 缓存 PCM → QSoundEffect，
本次验证了转换/队列/选音以及媒体回退解码，未宣称物理 QSoundEffect
输出通过。未运行整个客户端的完整硬件/渲染/机器人回归。

Windows 人工验收：开启 Sound Enabled 和 Lock Target Bonus，选择
Default Scrolling，每隔约 1 秒点击三次 Test Elimination，确认三次均
保留主击杀音；再选择 Combo，连续执行两轮 Normal 1–6 预览，确认两轮
都有对应级别音效。关闭声音后应全部静音。

另有独立的既存限制：媒体播放器进入 InvalidMedia 后，同路径文件即使
修复，也不会靠普通重播重新加载；本次没有扩大范围修改该恢复策略。
