# 键盘文本编辑边界与剩余路由债务

日期：2026-10-16。起点：`8cb9667cf2c156d7e6634551f691b2b487ba855e`。
范围仅为 `InputDispatchMixin.keyPressEvent` 中的聊天/搜索编辑策略及其证据。

## 改动及边界

- `rm_client/ui/polar/text_editing.py` 只接收 `TextKey` 和字符串，产生不可变
  `ChatEdit` / `SearchEdit`。模块不导入 Qt，也不能访问窗口、注册表、后端、
  叠层协调器或机器人控制入口。
- `ChatEdit.apply()` 只取得已有聊天控制器的六个编辑方法，以及一个明确的
  close 回调。`send_input()` 返回 `False` 时仍按原顺序关闭聊天；抛异常时
  不执行 close，也不执行后续 refresh/accept。没有新增发送权限或更换 transport。
- 搜索策略先决定退出或编辑。退出和无操作不读取/改写 `search_query`；
  只有实际编辑才将当前字符串交给纯更新函数。没有复制或长期保存窗口状态。
- Qt 键值转换仍在输入适配器。特殊聊天键不读取 `event.text()`，搜索的
  Backspace/Enter/Escape 也不读取文本。普通文本只复制当前事件字符串。
- 事件归属、帧刷新和 `accept()` 留在原适配器原位置。自动重复输入继续按原
  行为处理，没有把编辑器的重复输入规则套到 E-stop 或控制键。

没有改构造、公开签名、历史字段、鼠标/指针、摄像头/视频或 runtime API。
`keyReleaseEvent` 没有修改。新增 Qt 适配函数只接收 `QKeyEvent`，不接收窗口。

## 不把局部提取说成整个键盘路由解耦

按现有 `tools.quality.metrics.function_metrics`，`keyPressEvent` 的源码跨度
244→226 行、语句数189→171、决策数112→100，最大控制深度仍为4。
这些数字只是原方法内的变化；决策仍存在于新编辑器策略，生产代码总量增加。
本阶段不宣称整体复杂度减少，也不宣称窗口共享状态已经消除。

剩余顺序仍直接写在 Qt 方法中：

1. 取消背景点击；维护 ESC 菜单持有的 key-up；F12 急停先于生命周期拒绝。
2. pending/closing 拒绝、ESC 菜单、绑定录制、启动页、Hero、学生界面。
3. 聊天、轮盘、聊天/轮盘快捷键、经济面板、搜索。
4. ClickGUI、阻塞叠层、ESC、Minecraft 功能栏、战术地图、控制与模块绑定。

这条链仍依赖大量窗口状态，保留为明确债务。将其整体移到
`helper(window)` 或形状相同的巨型端口对象，只会隐藏耦合。

经济域尤其不能直接做一次性全量 snapshot：`economy_selected_offer()` 是
**会写回选择的查询**。原行为包括切类别后先写类别/清空选择，再查询选择，
成功后才归零 scroll/刷新；重复 Enter 仍运行该查询但不激活商品。
后续若提取，需要先明确经济选择/滚动/确认状态的唯一所有者及窄命令接口，
同时保留原公开适配方法和异常前已发生的写入。本阶段没有做这项扩展。

## 冻结参考与差分证据

`tests/fixtures/keyboard_routing_baseline.py` 是上述起点的
`keyPressEvent` / `keyReleaseEvent` 两个方法的原始源码切片，未由新实现重录。

- 整个 fixture SHA-256：`cb71cca8ba3e5cab8cde3d965c06e325aa00652bdbc1ee0f1f53590e621f4dc9`
- 两个拼接方法切片 SHA-256：`ab92e24d23e64f51ea1acecae3bda8e052d2e9f7817078aac68d007f6616606b`
- 参考随仓库携带，测试不依赖完整 Git 历史、不联网、不更新参考。
- 现有 immutable baseline、API manifest 和 quality ratchet 全部未修改。

`keyboard_routing_harness.py` 比较**有序调用、状态、显式 accept 与异常结果**，
不只比较最后返回值。覆盖包括2,640个单状态键值/重复/press-release组合、
2,100个双归属优先级组合、1,200组固定种子的三事件序列，以及逐调用故障注入。
此外有明确的 held-key/轮盘/地图中断序列、特殊键文本 getter 故障、搜索退出
不读 query、经济类别变更后的 selected-query 故障与部分写入断言。
将聊天和轮盘顺序互换的进程内变异会被差分门禁检出。

范围限制：Harness 的大多数外部动作是记录器，不是实设备实现；真实
PointerInteraction、生命周期判断、地图和模块绑定 helper 参与部分执行。
这不是所有可能 Qt 对象状态的形式化等价证明。

## 本阶段本地验证

环境：Linux、Python 3.12.14、PySide6 6.8.2。

```sh
export CI=true PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen
export POLAR_DISABLE_LOCAL_CAPTURE=1 POLAR_DISABLE_MEDIA_PRELOAD=1
python -m unittest test_keyboard_routing_characterization test_text_editing_policy test_window_text_editing
python tools/quality_gate.py
```

新增27条测试通过：

- `test_keyboard_routing_characterization.py`：17条，冻结差分、故障、优先级变异与安全契约。
- `test_text_editing_policy.py`：7条，纯策略、窄端口、False/异常差异及真实 MatchChatController。
- `test_window_text_editing.py`：3条，真实 PolarRMWindow + QKeyEvent 的聊天/搜索交替、
  pending/closing 拒绝与经济域优先级。每次使用临时运行目录、惰性捕获/媒体，
  native backend start 为 inert；窗口未展示。

既有127条相关测试通过：ESC菜单13、启动/全屏19、扩展全屏12、Jello键盘15、
背景点击14、PointerInteraction9、场景配置门禁4、窗口API契约41。
静态质量门禁通过，0个初始化 import cycle；没有放宽预算。
`python tests/api_freeze_test.py --mutation-selftest` 通过：删除
`draw_watermark` 会被兼容门禁检出，恢复后重新一致。

这些都是局部离线证据。完整聚合由主集成任务执行；本任务未运行
`tools/verify_all.py`，没有将局部通过冒充统一验收。
Offscreen 的 OpenGL/音频不可用提示是环境限制，不是 GPU/物理呈现、
聊天远端交付、真车操作或正式比赛资格证据。
