# UI 动画时钟与调度优化（2026-10-11）

基线：`3bf4091`。此补丁与 [字体/图集缓存优化](RENDER_PERFORMANCE_20261011.md) 独立；保留该补丁及其结果。

## 实际修复

1. **岛内内容与容器使用相同的漏帧时间。** 主 UI 已将真实 elapsed（上限 1 秒）传入动画，但 `ReversibleExpoTimeline` 内部仍截为 50 ms，容器 spring 却使用完整时间。120 ms 停顿后，0.22 秒内容时间线的 alpha 是 0.79306168，而同样 120 ms 分成 12 个正常帧是 0.97719562，几何与内容不同步。现在两者都消费完整 UI 时间，保留 1 秒 suspend guard、原指数公式、duration、反向行为与完成阈值。
2. **跳过暂停期间的调度积压。** `FrameScheduler` 原先用 while 为每个错过的显示帧增加 deadline。144 Hz 暂停 8 小时会循环约 414 万次。正常单步路径不变；积压用整数倍 interval 一次跳过，并处理浮点边界。没有追补渲染，没有改 FPS 目标、idle cadence、early allowance 或 `force()`。
3. **精确静止弹簧的快速路径。** 只有位移与速度都精确为零时返回固定点，避免 sqrt/exp/三角计算。不会把接近目标或仍有动量的状态提前吸附，不改弹簧参数与收敛阈值。

没有改渲染分辨率、blur、材质、透明度公式、运动曲线、正常帧的动画时长、机器人控制或后端 tick。

## 验证

- 新增 `tests/test_animation_pacing_regressions.py`：15/15 PASS，无 Qt/GL/设备/网络依赖。
  - 覆盖 30/60/90/120/144/165/240 Hz、开/关/中途反向、80–450 ms 延迟、零/负时间、长 suspend、岛容器与地图内容同步。
  - 调度器覆盖 early allowance、同回调去重、idle、force，以及 0.2 秒/1 分钟/8 小时暂停。通过计数型 deadline 验证最多 3 次加法，不以机器速度或时间阈值判断成功。
  - 同一测试工具指向基线源码时，7 个测试方法的合计 65 个断言/子测试失败，直接复现修复点；其他不变量仍通过。
- 前后真实源码对照：10,000 组 spring 输入的数值结果完全相等；7,000 个正常节奏的指数时间线采样完全相等；140,000 个调度决策完全相等。该采样矩阵内调度 deadline 最大差值 4.55e-13 秒（浮点加法顺序）。这不保证所有边界的 bit-for-bit 等价；在精确浮点边界上，批量跳过与重复累加可能落在相邻 deadline，仍保持既有 early allowance 与未来 deadline 合约。
- 延迟 120 ms 的 alpha 修复后为 0.9771956233487274，与 12×10 ms 结果相等。
- 既有 UI/state 回归：30/30 PASS。
- 既有缓存回归：默认 Python 8 PASS / 7 SKIP（无 PySide6），系统 PyQt5 显式离屏模式 15/15 PASS。
- 仓库要求的离线安全验证复跑：`polar_link_test.py` 79 PASS（原三方 CRC 参考缺失项 SKIP），`polar_bridge_test.py` 185 PASS，`polar_integration_test.py` 51 PASS，`test_transport_safety_regressions.py` 36/36 PASS。集成串口为内存 PipeSerial；运行时审计拒绝 socket、真实串口设备和非只读 git 子进程，unexpected attempts = []。
- 修改的 Python 文件编译与 `git diff --check` 通过。

## 同条件 CPU 微基准

Python 3.12.14，Linux x86_64，9 轮交错顺序，暖身后测量；GC 在计时期间暂时禁用并恢复。时间为每个所列 batch/call 的中位耗时。完整样本范围、工作量与源码 SHA-256 见 [原始结果](animation-pacing-benchmark-20261011.json)。云主机存在噪声，不能把微秒差值外推为整帧收益。

| 工作量 | before | after | 说明 |
|---|---:|---:|---|
| 64 个精确静止 spring | 70.14 µs | 50.84 µs | -27.5%，只省去固定点数学计算 |
| 实际静止 IslandContainerState.update（5 spring + content） | 10.26 µs | 7.83 µs | -23.7%，普通 idle 热点 |
| 64 个运动 spring | 64.44 µs | 67.82 µs | +5.2%，新精确检查的代价，数值不变 |
| 144 个正常 scheduler callback | 52.58 µs | 51.51 µs | 小差值，不能当作稳定性能提升 |
| 60 秒后单次 scheduler 恢复 | 318.72 µs | 0.83 µs | 积压跳过由线性变常数工作 |
| 8 小时后单次 scheduler 恢复 | 188.08 ms | 0.015 ms | 极端暂停场景，不代表正常帧或启动 |

运动 spring 的小额成本已公开；64 次 synthetic batch 不是实际一帧的调用次数。长暂停基准后续单次调用受计时与冷缓存噪声影响，所以“最多 3 次 deadline 加法”是比局部倍率更可靠的复杂度证据。

## 复现

只需本地基线源码，不下载依赖，不开应用或连接硬件：

历史可用性说明（2026-10-16 审计）：`3bf4091` 是当时记录的提交标识，
尚未确认其公开可达等价提交；当前审计环境也没有该 Git 对象。以下 `git show`
步骤仅适用于已持有它的仓库，不能据此承诺新克隆能够完整重跑 before 数据。
缺失时应明确报告基线不可用，不改用当前源码、不自动下载，也不刷新原始成绩。
若另行取得源文件，先核对原始 JSON 的 `source_sha256.before` 两项：
animation.py 为 `9349151fbdab0fcaff91431f77cf9592309eb50b02c2b82f00cb9605ae23a036`，
primitives.py 为 `73ed001a422d3521bc40ce261d08e9091528769fd4dd31bee3d0395ed3d0e344`。
当前实现的回归测试仍可独立执行；历史基线缺失不是这些测试的通过证据。

```sh
git cat-file -e '3bf4091^{commit}' || exit 2
mkdir -p /tmp/polar-motion-baseline/rm_client/ui/polar
git show 3bf4091:rm_client/ui/animation.py > /tmp/polar-motion-baseline/rm_client/ui/animation.py
git show 3bf4091:rm_client/ui/polar/primitives.py > /tmp/polar-motion-baseline/rm_client/ui/polar/primitives.py
python tests/test_animation_pacing_regressions.py
# 预期失败：验证旧实现确实能重现问题
python tests/test_animation_pacing_regressions.py --source-root /tmp/polar-motion-baseline
python tools/benchmark_animation_pacing.py --baseline-root /tmp/polar-motion-baseline --output benchmark.json
```

## 尚未验证

未启动完整 PySide6 应用，未测真实 Qt timer、Windows、QOpenGLWidget/GPU present 或用户观感；不宣称全应用 FPS 提升。真实 GPU 验收沿用缓存报告的清单，并补充窗口暂停/恢复，以及地图/动态岛在 80–200 ms 单帧延迟后的内容和几何同步。

Jello 按构建推进的 hover/scrollbar 辅助状态、telemetry 平滑和机器人 60/75 Hz tick 不在此补丁范围，避免顺手改动存在独立时序/控制语义的路径。
