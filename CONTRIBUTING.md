# 贡献与验证约定

本项目包含实时界面、设备线程与机器人控制出口。修改目标是可追踪、可复现、
可安全回退；代码通过离线检查不代表获得真车输出或正式比赛资格。

先读 [当前架构](docs/architecture.md)。涉及队内 `0x5A` 协议时还必须读
[AGENT.md](AGENT.md)；涉及视觉/玻璃/动画时遵循
[液态玻璃与灵动动画规范](README_液态玻璃与灵动动画规范.md)。
历史审计和蓝图提供背景，当前接口以代码与对应回归测试为准。

## 开发环境与改动范围

- 在隔离的 Python 环境安装 `requirements-core.txt`；同济 Local 推理另需
  `requirements.txt`。发布/便携 C 验证的依赖见 `requirements-build.txt`。
  提交验证结果时记录实际 Python / PySide6 / 相关依赖版本，不能用宽泛依赖范围
  代替本次运行环境。
- 正常启动用仓库根目录的 `main.py`。测试优先使用临时运行目录和 fake 设备，
  不覆盖 `configs/default.json`、开发者配置、标定或现场证据。
- 一次修改解决一个可说明的问题。先确认工作树里哪些改动属于别人，再编辑明确
  归属的文件；不要顺手整理无关格式、资源或生成物。
- 新增逻辑放到已有职责最接近的模块。不要只为缩短文件把逻辑拆成仍任意访问
  全窗口状态的辅助层；需要说明输入、输出、共享状态和生命周期。
- 保持 `rm_client.ui.polar_app` 的兼容导出、持久化设置 ID、协议字段和默认安全
  行为。修改 API/视觉基线必须解释预期差异并检查真实行为，不能只刷新基线让测试变绿。
- 模块/后端映射改变后运行 `python tools/module_backend_map.py` 并审查生成文档的
  差异；更新相关入口说明，不把历史报告当成当前架构清单。

## 实时路径和所有权

1. 不在 paint、输入事件或普通帧 tick 中新增串口枚举、设备 open/write/close、
   阻塞网络请求、进程等待、线程 join 或全量磁盘扫描。预加载与恢复任务要有预算，
   昂贵工作交给明确的 worker；Qt / OpenGL 对象仍按其线程和上下文规则操作。
2. 每条连接、串口句柄、输入队列和 GL 资源都要有唯一所有者。写明谁创建、谁消费、
   谁停止、谁释放；后台只发布快照/结果，不直接修改 QWidget 或共享可变 UI 状态。
3. 队列必须有上限；定义最新值覆盖、相对增量合并或丢弃策略。恢复时校验 generation、
   新鲜度和当前门禁，不重放上一连接的运动指令。队列接收成功不等于实际写出成功。
4. 停止/切场景/关闭窗口必须保持先锁控制和归零，再释放设备的次序。归零失败或仍在
   排队时不能报告完成；仍占有旧句柄的 worker 没退出前不得启动竞争者。
5. UI 展示层不能抢先 drain 后端队列，也不能根据“屏幕看起来正常”替代权威遥测。
   新字段要明确来源、单位、容器类型、过期行为和 overlay 顺序。
6. 保留 fail-closed、人工重新 Arm、物理遥控优先、急停及单一云台出口，不为跑通
   测试绕过门禁。队内 `fire=0`、驾驶键位限制等不变量详见 `AGENT.md`。
7. 不靠关闭特效、降分辨率或省略断言掩盖性能/正确性问题。性能结论必须给出平台、
   场景、样本和测量方法，区分 UI 工作时间、绘制时间与真实呈现节奏。
8. 现场日志使用 `RotatingJsonlLogger.emit()` 的有界接收接口，不把文件写入、轮转
   或 `flush()` / `close()` 等待放回帧/网络回调。需要可复现日志的离线测试应显式
   flush/close 后再读取，并检查返回值和 `snapshot()` 的丢弃/错误，不把入队成功
   当成证据已完整写入。logger 在场景重启中复用，只在最终关闭时封口。
9. 修改 MQTT 交互重启时保留立即失效/锁控制、单一清理所有者与最新配置合并。
   不让旧 Paho client 的清理阻塞普通帧，也不在旧 owner 未退出时启动替代者。
   backend 的 `stop(wait=False)` 不意味着整个场景切换或最终关闭都无等待。

这些是新改动的约束，不是“所有历史路径均已异步化”的声明。现有同步关闭与
其他限制见 [架构说明的已知边界](docs/architecture.md#已知边界与后续拆分原则)；
日志/MQTT 生命周期的本次范围与验证记录见
[第二阶段报告](docs/client_lifecycle_20261016.md)，历史第一阶段报告保持原快照。

## 验证与证据

统一执行入口为 `tools/verify_all.py`，测试登记/分类在 `tools/test_registry.py`。
单个测试脚本仍可直接运行以便定位问题，但不能拿局部成功替代聚合结果。

```sh
# 不执行设备动作：先检查本次选择范围与前提
python tools/verify_all.py --list --profile all

# 日常离线回归；报告放在被忽略的临时目录
python tools/verify_all.py --profile offline --json tmp_quality/verification.json --log-dir tmp_quality/logs

# 缩小到相关回归，--test 可重复
python tools/verify_all.py --test tests/backend_protocol_test.py
python tools/verify_all.py --test tests/test_transport_safety_regressions.py

# 有可用桌面 / OpenGL 时才显式启用桌面类检查
python tools/verify_all.py --category desktop --desktop --json tmp_quality/desktop.json

# 发布范围；缺少桌面等必要条件时保留不完整结论
python tools/verify_all.py --profile release --desktop --json tmp_quality/release.json

git diff --check
```

- 分类为 `offline`、`desktop`、`firmware`、`external`、`visual`。
  `--profile offline` 选择 offline + firmware；`release` 选择 offline + desktop；
  `all` 选择全部。`--test` / `--category` 覆盖 profile 的默认选择，
  `--desktop` 只允许执行需要桌面的选中项，不会自动扩充选择范围。
  external / visual 需要单独检查其条件，不能把发布 profile 说成所有用例都已运行。
- 用 `--list` 核对实际测试选择。新增回归采用现有命名方式
  （如 `tests/test_<feature>.py` 或 `tests/<feature>_test.py`），并验证注册表确实
  发现了它；需要桌面、可选依赖、编译器或外部参考树的用例必须登记真实前提。
  不要再维护一份独立的发布测试名单。
- `PASS` 表示本次选中检查完整通过；`FAIL` 是失败；`SKIP` / `INCOMPLETE`
  表示覆盖没有完成。读每项结果和汇总，不能仅把子进程退出码 0 当成通过。
  选中项有跳过、没有执行到测试、缺依赖或条件不足时不能宣称完整回归绿色。
- 无桌面的 Qt offscreen 构造/像素测试与真实 OpenGL 路径是不同证据。
  `--desktop` 是对可用桌面的显式选择，不能给 offscreen 测试补上硬件验证结论。
  真实窗口取证遵循 [AGENT.md](AGENT.md) 的 framebuffer 说明。
- 需要外部固件树时传 `--firmware <path>`，记录精确提交与准备状态。离线 C
  行为测试不等于 ARM 固件编译，更不等于烧录后的板级、无线或整车验收。
  不能为补齐测试自动改写开发者固件树或下载/安装未约定的依赖。
- 图形变更至少覆盖首次/重复打开、关闭后重进、Esc/Back、模态输入、分辨率/DPR
  变化及缓存失效；设备变更至少覆盖缺失/慢设备、断连、重连、过期输入、急停、
  配置切换和关闭竞争。应先用可控 fake 复现，再安排单独的现场验证。
- `python main.py --polar-selftest` 会访问真实设备，属于独立的台架/现场流程，
  不放进日常离线回归。允许运动的参数需要明确现场授权和机械安全条件。

PR / 交接至少记录：

1. 改了什么、为什么，相关问题或复现步骤。
2. 被测提交（或提交加未提交 diff）、操作系统、解释器和关键依赖/设备条件。
3. 精确命令、退出码、通过/失败/跳过/不完整结果，日志或报告位置。
4. 后续修改后是否重新验证；未运行项的原因和现场验收剩余范围。
5. 若变更协议/默认值/API/基线，给出兼容性和回退说明。

## 生成物、保留证据与私有文件

- `user_data/`、`field_evidence/`、`logs/`、虚拟环境、构建目录、临时截图和缓存
  属于本机运行数据，按 `.gitignore` 管理。测试输出优先放 `tmp_quality/` 或系统
  临时目录；不要把用户的实际配置、日志、设备信息或资格签字作为 fixture。
- `tests/fixtures/`、`tests/baselines/`、`tests/api_freeze_baseline.json` 是有意保留的
  测试输入/基线。`docs/benchmarks/` 与 `reports/<issue>/` 中经过审查的结果可作为
  历史证据保留；文件像日志、截图或 JSON 并不自动意味着可以删除。
- 新保留证据需提供来源、被测提交、环境、命令、结论/限制，去除私人路径、账户、
  凭据及无关遥测。保留最小的可复现合成样本，避免把整次运行目录搬进仓库。
  原始现场证据先留在受控位置，只有获准分享的去敏副本才进入 PR。
- `.gitignore` 不会取消已跟踪文件，也不授权删除真实数据。清理时先区分可再生输出、
  回归基线、授权素材和人工证据；不要用 `git clean -fdx` 或全目录清空作常规步骤。
- 默认配置与发布模板视为只读输入。发布包从明确 Git ref 构建，不复制开发机
  `user_data`；授权素材与许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 保存与评审

每个可复现阶段单独保存，提交信息说明实际变化。提交前检查 `git diff --check`、
`git diff --stat` 和暂存区内容，按文件暂存，不把其他人的改动或私有文件一起带走。
同一仓库的聚合/固件验证按 runner 的锁约束串行运行；并行协作先分清文件所有权。
推送只在授权仓库与分支范围内进行，修复或文档更新不隐含合并主分支、发布或真车操作权限。

如果缺少条件，提交可复现的问题与未完成清单。不要更新历史报告让它看起来代表今天，
也不要把“构建成功”“离线通过”改写成“可以实战”。
