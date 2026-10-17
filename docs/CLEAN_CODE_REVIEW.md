# Clean Code 重构：独立验收清单

本清单以公开基线 `d64f1528e2e1864ef369537c026b6d3f7127f590` 审查
2026-10-16 的客户端重构；本地审查别名 `da91fc5` 与其具有相同的 Git tree
`46333e99db86988f503b4ff31cd651d0e07eed86`。它记录能够复现的
验收条件，不把文件变短、目录变多或新增类的数量当成完成标准。第1–3节的未勾选项
是面向全项目的持续审查问题，不表示对应局部测试失败，也不表示本轮没有建立该类边界；
已验证的具体范围、提交和反例以随后记录及收敛矩阵为准。未作全库证明的通用命题不勾选。
本文件不替代最终聚合报告，也不代表真车或桌面 GPU 验收。

## 采用的原则

- [第 2 章：有意义的命名](https://aeryzhao.github.io/cleancode-book/ch2.html)：
  名称应表达动作、单位、来源和所有权。兼容名字可以保留，但新实现不能靠含糊名字隐藏副作用。
- [第 7 章：错误处理](https://aeryzhao.github.io/cleancode-book/ch7.html)：
  正常流程与失败边界分开；故障要保留上下文，用异常注入验证清理和恢复。
- [第 17 章：味道与启发](https://aeryzhao.github.io/cleancode-book/ch17.html)：
  评审关注实际重复、隐藏耦合、失效注释和边界遗漏；度量用于发现风险，不能代替行为证据。

## 1. 结构变化必须真的缩小职责

- [ ] 新组件的输入、输出、状态和生命周期可以独立描述，并可在不构造完整窗口的情况下测试。
- [ ] 不以 `helper(window)`、转发任意属性的代理或新的全状态 Mixin 伪装解耦。
- [ ] 非 UI 领域层没有反向依赖窗口、绘制代码或 QApplication。延迟 import 不能用来掩盖新循环依赖。
- [ ] 重复的安全判定或配置解析只有一个权威实现；调用方没有各自修剪规则的副本。
- [ ] 兼容入口只转发明确接口；新逻辑不再依赖历史类、历史行号或已经失效的拆分计划。

反例：把 200 行窗口方法逐字搬到新文件，仍任意读写同一个 `self`，不能单独算作
职责拆分完成。保留公开入口并把实现委托给窄接口组件则可以，但需要说明两者的边界。

## 2. 时间、线程和设备所有权

- [ ] 普通 tick / paint / 输入回调没有新增设备枚举、open/write/close、网络等待、join 或磁盘全扫描。
- [ ] 连接/解码器/队列有唯一清理所有者；旧 worker 未释放句柄时不会启动竞争者。
- [ ] 停止、切场景、重配置保留先锁控制和归零、再释放资源的顺序。
- [ ] 排队、写出、归零确认、关闭完成是不同状态；超时或异常不会误报成功。
- [ ] stale generation、过期输入、断连/重连和重复关闭均有可控 fake 回归。
- [ ] UI 的控制时钟、逻辑时钟与展示时钟保留各自节奏；暂停恢复不会补发积压控制。

真串口/GPU/操作系统调度的上界不能从 fake 的耗时推导。现有同步边界需要如实保留，
不能因为局部异步化就宣称整个退出流程无阻塞。

## 3. 错误语义与用户意图

- [ ] 错误报告包含失败阶段与原始异常；恢复成功不抹除需要保留的累计故障证据。
- [ ] 缺少可选依赖与真正的程序错误有不同语义；不以宽泛 `except` 静默选择另一套生产实现。
- [ ] 临时渲染失败仅影响当前运行决策，不改写持久化的 `Render Backend` 用户选择。
- [ ] 一次组件清理失败不阻断后续安全清理；失败不会留下可重放的控制意图。
- [ ] 回退/重试有明确预算与恢复条件，测试覆盖连续失败、恢复后成功、重复进入。
- [ ] 配置保存失败保留原文件；非法输入不能以部分成功状态落盘。

已确认的基线反例：`paint_overlay` 中 GPU 重试失败会写
`ClickGui / Render Backend = Compatibility Painter`。这会把瞬态错误变成可能在退出时
保存的用户设置变更。修复应验证“失败 → 恢复”序列，而不是只验证回退帧能够画出。

## 4. 启动与兼容入口

- [x] 导入 `rm_client.app` 不加载 Tk、窗口或设备实现。
- [x] `app.main()` 与 `python -m rm_client.app` 都调用 Qt 入口一次。
- [x] Qt 启动异常与事件循环退出状态原样可见；失败不隐式启动旧 Tk 客户端。
- [x] 历史 `RMClientApp`、颜色常量、`clamp` / `lerp` 仍可显式导入；历史实现未被删除。
- [x] 已存在 QApplication 时，入口创建的窗口在函数返回后仍然存活；关闭后释放引用。
- [x] 多次启动创建的窗口拥有独立寿命；关闭其中一个不会释放另一个。
- [x] 当前架构说明和模块后端映射不再把历史 Tk 原型标记为生产实现。

基线中可复现的两个问题：

1. `rm_client/app.py` 有兼容入口函数，却没有 `__main__` 分支；模块执行只导入 Tk 后退出。
2. `app_entry.main()` 复用 QApplication 时不会进入自己的事件循环，也没有窗口所有者；
   局部 Python wrapper 立即释放，`QApplication.topLevelWidgets()` 返回空列表。

`tests/test_app_entry_contract.py` 在独立子进程中验证上述边界。窗口寿命用真实 Qt
`QWidget` + offscreen 平台验证；它不证明生产 `QOpenGLWidget` 的桌面呈现质量。
历史 Tk 实现与基线移除入口后的 AST 相同，隔离后的文件为 `rm_client/legacy_tk_app.py`。

## 5. 回归不能为了迁移而变弱

- [ ] 先保留基线行为用例，再运行变更后的实现；失败用例要能指出实际行为差异。
- [ ] 不通过刷新 API / 像素 / 性能基线、删除断言或扩大 skip 让迁移“通过”。
- [ ] 每个服务至少覆盖一个边界或故障序列，不能只有 happy path 与 import smoke。
- [ ] 测试调用真实生产接口；fake 只替代外部设备/时钟/昂贵依赖，不复制被测算法。
- [ ] 新用例被统一注册表发现；新增入口不会绕过原有聚合或发布检查。
- [ ] 验证报告区分 PASS、FAIL、SKIP、INCOMPLETE，记录精确提交、环境与未验证范围。

## 6. 评审记录规则

每次发现记录：受影响符号、触发条件、可观察后果、最小反例、对应测试与修复提交。
意见不能只写“复杂”“不整洁”。没有反例但值得改进的结构问题，应列为后续风险，
不能混同已确认的行为缺陷。独立阶段可单独提交，但最终还需在合并后的树上重跑检查。

### 已完成的启动阶段

- `9f59ad8`：兼容入口隔离历史 Tk 依赖，6 个独立边界测试通过。
- `e2b9263` Qt 生命周期修复：同一测试文件增加真实 QWidget 的拥有/释放与事件循环退出码验证，
  当前共 8 个用例；以对应提交和聚合 JSON 记录为准。
- 验证解释器：`/tmp/client-quality-venv/bin/python`；Qt/PySide6 6.8.2。
- 入口测试命令：`python tools/verify_all.py --test tests/test_app_entry_contract.py`。
- 本节证据仅涵盖启动边界；跨边界复验另列，不宣称全客户端重构验收通过。

### 跨分支审查记录（截至 2026-10-16 08:28 UTC）

下面记录的是被审查的阶段提交，整合时可能生成不同提交号。代码审查与阶段用例
不能替代整合树测试；已经完成的整合范围见末尾固定检查点。通用清单也不因局部通过
而变成全库保证。

| 边界 | 可复现反例或审查依据 | 修正与当前状态 |
| --- | --- | --- |
| GPU 回退与用户设置 | 初始化、GPU 重试、缓存构建三条错误路径都会修改保存的 Render Backend 选择 | `bcea0cd` 移除三处隐式写入，保留显式设置操作；失败/恢复和真实 Qt 缓存测试覆盖，代码审查通过 |
| GPU 异常可观察性 | 初版 presenter 仅保存字符串；进一步用真实 `ClickGuiGpuRenderer.render` 注入 TypeError，内部捕获后返回 False，外层故障计数仍为 0 | `b18d901` 在真实 renderer 的 initialize/resources.prepare/commands.render 边界记录类型、阶段、cause、累计次数与节流后的完整 trace；6 个新增用例调用真实 render，代码审查通过 |
| 失败帧的独立清理 | 原批量 try 中 background release 抛异常后，其余五层全部跳过 | `ef22dab` 在资源所有者中分别清理六层，保留原始与每个次级故障；测试验证不 present、不完成 restore generation、失效缓存元数据 |
| UI 时间与展示职责 | 必须区分控制、逻辑、显示时钟，且不把窗口传入新的全状态代理 | `0d90450` 使用纯时间计划和显式动画 Protocol；窗口保留自己的阶段适配，基线调用顺序用例未删除，结构审查通过 |
| 无钩子的配置恢复 | 初版 RegistrySnapshot.restore 调用 set_enabled，进而执行 on_enable/on_disable；只检查 registry toggle listener 的测试抓不到此差异 | `6bd6bda` 直接恢复 enabled 并保留生产 revision 更新，新增 recording-hook 测试 |
| 迁移后的配置预检 | HUD 已变化后，旧 EliminationFX.Volume 字符串迁移到数字字段 Sound Volume，float 转换抛异常并留下部分写入 | `6bd6bda` 对原始及迁移后文档分别完成验证，再开始 live 写入；验证和应用均有反例回归 |
| 缺省与空模块的语义 | 原实现对缺少 modules 的元数据文档直接返回；提取初版却同步了旧 Alliance 字段 | `6bd6bda` 保留缺少 modules 的 no-op，显式空 modules 仍按基线同步；两个情况分别测试 |
| 诊断失败前撤销控制授权 | radio feedback 提取初版先解析读回再写 radio_km_enabled；注入解析异常会保留上一帧 true | `92144a0` 先发布当前授权，再解码诊断；保持 inf 计数原有 OverflowError 中断语义，不以吞异常继续提交意图；97 个冻结基线案例未刷新 |
| 关闭完成与句柄所有权 | 真实 close_serial 调用真实 serial.stop，fake stream.close 抛 OSError；适配器正常返回，is_open 仍 true，但 _serial 已被清空且 last_error 为空 | `c545d7b` 保留串口、POLAR、角度接收器失败句柄并拒绝替代；该早期阶段不单独构成完整生命周期验收；后续闭合及整合结果见下文 |
| 工作线程与嵌套句柄的清理 | 后续审查发现 POLAR._loop finally 与 TcpByteStream.close 仍吞掉 close 错误并丢弃所有者；直接 stop 测试无法覆盖这两条路径 | `66524c3`、`733cfa7` 覆盖 worker-final、迟到连接、TCP setup 失败与嵌套 socket；独立 close 锁防止 stop 与 worker 并发关闭同一句柄，代码审查通过 |
| 异常中断的差分证据 | 初版随机重放对异常只比较类型，即使副作用顺序变化也可能相等 | `409f5d0` 同时比较异常前的 control/state/config/events；发现并修复 policy 文本发布顺序与无效角色编号下缓存清空顺序；未刷新普通案例 fixture |
| 调用契约中的 Enum 默认值 | Enum.SAFE 默认值被序列化为字符串，改成普通字符串仍比较相等，但调用 .name 已抛 AttributeError | `b1eeb1f` 保留 Enum 的完整类型及值，新增可执行反例测试；原历史 API 快照保持不变 |
| 轮盘配置的生产接线 | `4286851` 新 helper 可以收敛 Dead Zone，但旧窗口方法仍使半径72时运行值51.84、registry值96 | `94f73bb` 接通两个真实窗口方法；4 个直接方法回归覆盖原反例、自定义标签/图标与缺省模块，代码审查通过 |
| 异步场景与配置事务 | UI 配置成功/恢复仍调用同步 apply_scenario；直接换成异步 request_scenario 会在运行时激活完成前错误宣布成功 | `ec25cb3` 建立 generation 绑定的 pending/completion，`5e427e0` 修复关闭取消的视图投影恢复；`c6a4af8` 改用真实窗口唯一 poller/延迟 close 验证，后续纳入第11阶段离线整合回归 |
| DT7 / CC 写出器关闭 | native close 抛错、返回 False 或仍报告打开时，不得丢弃句柄、重新打开或宣布 closed | `97a6bdc` 保留句柄，明确关闭重试在旧线程退出后由单一 finalizer 执行；7 个 fake-driver 方法与代码审查覆盖失败写入、阻塞重试和无期限等待的真实结果 |
| UDP / 解码器的嵌套所有权 | socket、迟到 Popen、进程管道或 reader 未退出时，外层 stop 返回不构成关闭证据 | `19c3db5`、`365b38f` 保留 session/process/pipe，严格 wait 序列化未发布的启动和现有 worker；close_video 包含 HEVC 关闭结果，代码审查通过 |
| 壁纸替换与迟到清理失败 | 旧 decode worker 在 load 两次检查之间 release 失败并退出；真实方法与线程反例得到 load=True、一个失败句柄、一个新线程 | `be34021` 在替换决策锁内再次检查失败句柄；`7220c25`、`665d20e` 接入实际 owner 的显式重试，camera 失败句柄在第二次真实 close 之前一直保留 |
| 云台授权测试的来源形状依赖 | 旧 regex 只寻找 manager 方法内的变量文本，策略提取后误报，且无法验证线上请求位 | `03a6d72` 保留编码和 E-stop 用例，使用真实 manager、bridge 和 transport 检查全部32种三门条件/底盘开关组合及迟到过期；删除任一安全条件的变异均被拒绝 |
| 模块目录与环境探索 | 单纯搬动长声明不能隔离运行时依赖，也不能解决多个 registry 共享 options | `606d37d` 冻结74模块/470字段及探索顺序，`0667342` 引入显式 CatalogChoices 与复制装配边界；独立禁止 Qt/model/backend/device/UI 导入测试通过，11个迁出类型/helper AST相同，21处跨 registry 共享 options 消除 |
| 音频文件与运行时事务 | registry 已提交后音频文件或 pool setter 失败，会出现声明值、文件、cache、播放设备不同步 | `5ca2aaa` 把精确文件快照和 pool refresh 作为事务参与者；持久回滚错误保留到异步恢复完成，`f5b476d` 补真实 pool setter 和普通 backend 切换中的 settings-page 提交拒绝 |
| MQTT 准备失败丢失所有者 | clear_received_epoch 抛错后 client 已被清空，但清理未启动；真实 manager 曾给 resources_released=True、may_exit=True | `eb00f37` 在可失败通知前转移所有权并 finally 准入清理；阻塞的真实 cleanup 完成前没有 final completion，准备错误仍保留 |
| Paho 嵌套 socket 与线程 | Paho _sock_close 在 native close 前清空 _sock；loop_stop 返回不单独证明 socket 关闭 | `55434e9` 捕获公共 socket callback 参数、网络线程及 wake sockets，原生关闭失败仍保留到显式重试；实际已安装 Paho 与真实 manager 组合回归通过 |
| 最终退出与审计完整性 | 日志永久丢失和仍未排空是不同状态，重复失败不能被改写成原始成功 | `c6be53f` 分别记录 resources_released、audit.drained/intact、may_exit、attempt 与原始 completion；`665d20e`/`8aef4e2` 的真实窗口等待 owner、显式重试，并在退出前展示日志损失和其他关闭错误 |
| compositor 内部异常与 GL unwind | actual present 内部吞 TypeError；补报告后又发现 draw 抛错未释放 VAO/VBO/attribute | `74508d4` 记录六个真实 pass 的类型/阶段/cause/trace，`032d118` 独立 unwind 并保留原始与清理错误；`4b92fa3` 进一步拒绝 shader/VBO bind=False 和 FBO release=False，正常 void/None 不误判 |
| 失败的 input-clear mailbox 消费 | `6a0ea8d` 先弹出请求后清输入，真实 reset_inputs 持续抛错时第二次资格申请返回 token，旧 W 和 last_command 仍在 | `5ede175`（重放 `b4ea1ef`，整合 `5227882`）保留失败请求，失败后撤销刚授予的权限，持续阻止 egress；11条 owner 用例和6条既有 safety diagnostics 独立通过 |
| CC 端口冲突的旧 writer | 旧 CC writer 已发送 W，候选端口被角度链路占用，早返只清 enabled 而没有 neutral | `f29d077` 在冲突早返前请求旧 writer neutral；3条真实异步 writer/fake sink 回归冻结 watchdog 时钟，确认失败保留所有者且不自动补充重试预算；这是明确的行为修正 |
| 操作员急停的串口写锁 | 真实 serial 写锁被持有时，trigger_estop 阻塞，serial latch 仍未置位且 POLAR 尚未收到急停请求 | `1604d7c` 改用既有无锁 request_estop；`3cda8c6`/`65421ab` 还使 tick 立即撤权、延后允许态恢复，以 metadata generation 拒绝覆盖新急停的旧安全快照 |
| 普通场景切换期间的交互 | UDP teardown 被阻塞、角度 teardown 已完成后，直接设置回调可重启真实角度 worker，虽然 lifecycle_pending=True | `59ffd60` 在事件/dispatch 修改前门控；`165c1f8` 阻止 backend 入口在 pending/final/failed 时重开资源；`b280373`/`672b591` 加匹配 generation 的 registry 恢复与 effects 拒绝，`4244f13` 接入四个普通场景请求入口 |
| UI 急停的输入适配失败 | 实际控制桥 reset_inputs 抛 TypeError 时，设置动作先 reset 再 trigger，使 estop=False 且仍为 QUALIFICATION | `346d151` 窄 emergency action 服务先撤权，独立尝试 fallback lock 和输入清理并保留失败；主窗口 `a455ce4` 和 settings-dialog F12、设置按钮都有实际窗口故障证据 |
| teardown 准备和完成阶段的 cache reset | prepare 失败使真实 serial 假句柄仍 open、close_calls=0；资源完成后的 reset 失败又使 completion 被消费但 audit 从未开始 | `52e62dd` 分别记录 preparation/stopped-runtime 失败并继续资源关闭与审计；独立原生句柄反例确认 close_calls=1，resources_released/drained/may_exit 为真而 succeeded 为假，失败原因保留 |
| input-clear 与最终状态机的交互 | 在 `52e62dd` 的实际 manager 持续注入 reset_inputs TypeError，资源线程结束后每次 poll 仍先抛错，pending=True、无 final completion、audit 未开始 | `a25a514` 保留一个 observer failure，继续资源/audit 推进但仍阻止授权；`4a44b86` 又拒绝仅在 activation 首次失败的 clear，避免错误返回 succeeded=True 或启动 transport，精确提交独立复验通过 |
| 生命周期责任所有者 | 单纯转发 manager 的全部状态无法独立证明 latest-request、final seal 和 audit 顺序 | `26c98f2` 的 BackendLifecycleController 独占两阶段状态与结果，TeardownResources 捕获真实 owner/已准入线程，manager 只传三个命名动作；真实 serial 迟到句柄和失败关闭重试用例通过 |
| E-stop 解除与串口 driver 锁 | 解除动作调用同步 serial.release_estop，实际写锁阻塞 UI；旧允许态快照还可能与新 E-stop 交错 | `374b68e` 先回到 LOCKED，再调用 try-lock tick；忙时保持本地撤权、后续 tick 恢复，新 generation 的 E-stop 仍胜出，5条独立测试通过 |
| DT7 与保留的角度端口 | 角度 close 被阻塞或失败后仍占端口，DT7 新 owner 不能凭 requested close 替代它 | `d00b7e7` 对真实输出保守拒绝 Auto 或规范化后冲突端口，归零旧 writer、保留 owner 与已耗尽的重试预算；6条真实 owner/writer 测试独立通过 |
| 指针状态与资源装配 | 仅改字段名字或把窗口传给工厂不构成职责变化 | `cc0eb0e` 把 capture/cancel/matching-release/binding 行为交给单一 owner；`a2236b8` 仅接收 paths、显式服务参数和 QObject parent/callback，装配不启动 transport；独立场景/构造用例通过，复杂 drag 分支仍留在适配器 |
| 可选受击计数的首次观测 | 合法小弹计数20后首次大弹计数10，被当作新+10伤害，真实既有 spin consumer 返回 enable | `3926f7a` 分别跟踪两路 known，首次值只建基线；任一倒退/wrap 使整组重建 epoch，缺席路恢复未知；10条真实 codec/consumer 测试独立通过，这是有意行为修正 |
| 证据中的源码身份 | 两次读取 HEAD 可能组合旧 commit 和新 tree，错误描述复现来源 | `cbebb1d` 增加只读环境/源码元数据，`63a4500` 用已捕获的 commit 查询 tree；16条独立测试通过，dirty 不是工作树内容摘要，也不是依赖锁或硬件资格 |
| 窗口层 FBO release 拒绝 | 实际 release_layer 忽略 False，真实 cached frame 仍 accepted=True、present一次；失败恢复的 cleanup_faults 也为0 | `e67f298` 将普通拒绝与异常分别记录，窗口拒绝提交 cache/present；独立69条相关用例通过，未把 False 虚构为异常 |
| 失败另存的文档身份 | 另存B失败后 Reload 不存在的B退回已接受的A，下一次自动保存覆盖A；修正后又发现显式保存回A成功却仍禁用自动保存 | `bb72320` 保留未接受草稿及 accepted name/generation，旧 pending 在切换或同名导入前写回旧文件；`940a97a` 只在明确保存成功后清除草稿保护，两个实际窗口反例通过 |
| profile/startup 两文件保存 | profile 已替换后 startup 写入失败，内存显示失败但磁盘已部分提交；已有错误标记又可能抑制新的恢复失败告警 | `26bc3c4` 捕获原字节或缺失状态，普通异常后独立逆序恢复；保留原 cause 与所有恢复错误，成功后仍留历史恢复诊断。单写者范围明确，不宣称跨文件 crash 原子 |
| 窗口绘制与清理的根因 | startup draw TypeError 被 finally 中 release OSError 替代；priority draw 失败留下 active QPainter；present 成功后的 commit 抛错却保留可复用 cache | `e68901d` 使用局部 LayerPaintSession 独立尝试 painter/state/FBO 清理，保留 primary/cleanup；区分 physical present 与 committed frame，三个原反例和76条相关测试独立通过 |
| referee 投影的值与副作用 | 存在性过滤、可选计数基线不应依赖 Qt/manager，也不能因提取而提前转换或修改异常前副作用 | `a254a68` 冻结210例，`5fa08c9` 提取纯 presence/baseline/sample/decision；独立新种子10,000例含4,394次中断，0差异，原 fixture 不变 |
| GPU 经济面板不可见命中区域 | reveal/scroll 裁剪仅作用于绘制，原始矩形仍注册热点 | `2a14529` 使用同一可见交集；64绘制trace不变，真实 add_hotspot 的188个交集与24个空交集检查通过 |
| 视频 source stop 准入失败 | `d3f24fc` 中 UDP stop worker 启动抛错后已标 synthetic，但 native decoder 仍 available、未 retired；同名重试因早返不再清理 | `1c31097` 独立撤销三类 owner，分别记录 requested/active/pending/failures；同名自动 refresh 不重复准入、显式 retry 可重试，失败期间禁止消费旧帧；35条精确提交测试独立通过，后续 profile/recovery 准入边界另列 |
| 失败保存后的关闭准入 | 已接受 profile 的普通 pending autosave 失败仍可能最终关闭；另存失败草稿也不能退回源文件隐式写入 | `ab124fb`/`93b5dd5` 和 UI `2cc85c4`/`61da0cf` 在 teardown 前先锁控制、尝试正确 owner 的保存；失败保留窗口并暂停自动重试，只有明确 save/close 才重试；实际窗口34条精确提交测试通过 |
| staged profile 与 dirty close | 同名 reload 期间 registry 是候选值，旧 dirty owner 仍在；关闭不能把候选投影写入原文件或丢掉旧恢复点 | `b8e66ae` 增加5条实际 window.close 组合回归：APPLYING/RESTORING/普通切换拒绝 dirty close，activation失败后只允许保存原值，无 dirty 的 pending load 仍可安全取消 |
| ESP / TargetHUD 绘制所有者 | 真实 vision TypeError 被 release OSError 掩盖，QPicture 和直接 QImage 画图异常后 painter 仍 active | `70622df` 使用 compositor 的 render_vision_layer/paint_raster 边界独立 unwind，原始与清理故障分开、失败不上传；74条相邻真实 raster/故障方法独立通过，像素和 bounded-tile 断言保留 |
| writer 到角度链路的端口移交 | 已确认 neutral、native handle 清空且 worker 退出的历史 writer 仍永久占用端口 | `1a4e102` 只在 port_handoff_ready 为字面 True 时解除历史保留；仍持句柄、失败 neutral、失败 close、活线程或未知状态继续阻止新 owner；20条实际 writer/relay 方法独立通过，重试预算未放宽 |
| 经济确认与几何 | 两种渲染适配需要共享确认状态和布局，但不能把窗口或 registry 传入新全状态 helper | `1d91c68` 提取无 Qt 的确认值策略；`0995a50` 的几何模块仅使用 Qt 值对象，保留84例绘制轨迹；`7616dd2`/`063ee4c`/`7ac792a` 独立恢复 clip/painter 并保留当前绘制根因，26条独立测试通过 |
| 手工源码证据与自动报告 | 生成器重跑仍会覆盖人工第4节，重新写入“无 DT7 实现”等已撤回结论 | `08cd723` 原样保留现有手工段、重复生成幂等，缺段明确待维护、不可读原文不覆盖；18条独立测试通过 |
| 质量预算只向下收紧 | 服务提取不能靠提高阈值或丢掉迁出工作获得通过 | `19b16c1` 仅降低8项旧函数预算；固定 `f784da9` 测量满足新预算，24项 limit+1 变异全部拒绝；全树总量增长和未完成热点保留 |
| tick 诊断的时间窗所有者 | 字段迁出若没有累计、消费和持续故障语义，仍只是状态袋 | `004dcb5` 的 TickDiagnostics 独占 record/run/finish 与5秒窗口；报告消费不清终生错误或每阶段 throttle；48条独立方法通过，9个窄 descriptor 保留旧入口 |
| completed-frame 统计 | 提取不得改变clock采样点、FPS/EMA/p95或失败sink对窗口的消费 | `482b683` 的 FrameTelemetry 独占90/720窗口、报告与同步ack；59条独立方法通过，另与 `4f5b910` 原paintGL重放10,000帧、65次sink中断，完整样本/报告/异常副作用0差异 |
| referee-profile 部分提交 | native退休准入失败后manager/profile已变但frontend未变，同名setter早返；后续步骤还可能使MQTT与本地不同 | `39763e6` 先准入native退休、显式保留requested/manager/MQTT/frontend身份与失败；专门生命周期review的14条source/profile用例验证普通tick不重试、明确同名请求可恢复 |
| Thread.start 两类失败 | 未启动thread被永久join；已启动后wrapper抛错又可能丢live owner或允许第二closer并发 | 独立 [11点准入审查](reviews/thread_admission_audit_20261016.md) 保留标准拒绝与late-wrapper两个模型；`ecd58d3` 21套144方法通过，具体修正和未审范围见该报告，不等于全库线程资格 |
| 专用 GPU renderer 内部状态 | 实际startup/wheel/island/vision的program.bind=False仍returnTrue并draw；draw异常跳过内部unwind；首destroy失败跳过后续owner且available仍True | `0af4552`/`390a8ae` 建立独立render/release/initialize所有权，`b09abe6` 补副作用后抛错的partial-bind unwind；原成功命令、几何与shader字节保留，精确快照90条相邻方法独立通过 |
| Startup 纹理上传所有权 | resize destroy、复用纹理setData或新纹理allocate/filter/upload失败时，候选句柄曾从所有字段消失 | `d8599b4` 从实际构造后即登记唯一pending候选，失败destroy保留、下次更新先清旧候选，旧current/key/rect不被半提交；`51fd911` 的28条专用方法独立通过，包含原shader字节冻结 |
| 视频source故障的tick隔离 | retained admission error从refresh_vision_runtime逃逸，跳过后续backend/apply/polar/repaint | `18e634c` 将该调用放入已有TickStageRunner；35条精确快照方法独立通过，重复tick保留类型/节流且不自动准入重试 |
| 恢复按钮的stale鼠标释放 | `5e7fd85` 旧热点在source A→B→A后拒绝retry，但release未被消费，可调用control.set_mouse_button(1, False) | `f094a3d` 由左键action在任何拒绝前消费release；13条独立方法及原实际鼠标反例通过，明确打开combat eligibility时仍retry=0、control调用=0 |
| Startup texture-unit 清理 | 按Qt契约模拟实际render，current绑定unit0、previous绑定unit1后两次无参release只清unit1，返回True却保留unit0绑定 | `4b5ed51` 的5条先行测试4FAIL，`d4e47ca` 显式保存并release对应unit；33条专用及99条相邻方法通过，旧trace fixture不改，仅明确纠正两条release参数 |
| 私有API证据的有界演进 | FrameTelemetry迁移使4条旧caller路径失效，但其中2条仍有真实独立消费者 | `300ed64` 只退役2个owner-only storage的mandatory保护、保留alias与历史证据；其余2条换真实caller；53条方法及真实mutation独立通过，公共1036项和540签名未变 |
| 数据reader与cleanup worker不能混同 | `e5fc526` 将存活pipe reader当inflight；stopper准入失败后，新closer被永远禁止，但只有closer terminate才能解除os.read | `d9a61f1` 只把实际closer/close锁/启动或恢复临界区算busy；原审查结论已[明确撤回并更正](reviews/video_source_data_owner_correction_20261016.md)，本reviewer另用实际窗口+真实os.pipe验证修前不可点、修后一次点击开始closer并保持Qt心跳 |
| 可见灰态恢复按钮的输入归属 | retry开始后灰色等待按钮不注册热点，真实combat eligibility为True时点击得到press=True/release=False | `54f19a0` 只给可见waiting button增加左键no-op shield并消费release；精确UI+backend组合13方法和原真实os.pipe点击反例通过，cleanup结束删除热点 |
| UI拖动结束后的combat anchor | 恢复按钮接管拖动但保留旧_control_mouse_point，释放后第一次自由move回放191px旧delta | `8965ea2` 只在两个恢复handler接管左键时清anchor；27条独立方法与retry异常路径通过，accepted/stale/busy首次自由move不发motion、第二次仅发布新delta |
| UI视觉worker的停止准入 | 同步vision/Tongji stop在owner tick累积等待；并发close在失败前排队还可能隐式重试未展示的错误 | `ba4575a`/`3a05029` 与caller `5df6104` 经[独立审查](UI_VISION_STOP_REVIEW_20261016.md)验证54方法和4个额外Qt心跳场景；camera worker/caller后续已合入 `8cb9667`；其专门native审查与caller验收分开，不把三worker结果泛化 |
| 聊天/搜索编辑策略 | 原keyPressEvent同时承担编辑器局部规则和全窗口路由；一次性读query或event.text会改变异常时点 | `fd00614` 固定原入口源码，`a38e16a` 只提取Qt-free不可变文本决策、六方法ChatEditor及明确close回调；27方法独立通过，路由顺序/economy/keyRelease保持原位；已纳入第12阶段离线整合 |
| 设置页面与键位编辑器循环 | jello_keybinds在validate/apply/build中反向导入设置页面，共享应用适配与Qt急停过滤器绑在页面模块 | `2ef40aa`/`9cc587a`/`febac3a` 把6声明原样移入两个共享adapter；12新增方法、前段75相邻方法独立通过，恢复旧page import的两个负控精确失败；无Qt验证可独立运行，apply仍host-backed；已纳入第13阶段，未称全状态解耦 |

配置事务 `6bd6bda` 的回滚顺序另经审查：精确值恢复、停止态运行时恢复、再次恢复精确值、
最终安全清理分别执行；运行时恢复失败不跳过 E-stop/lock。真实 offscreen 窗口中的
release_all 异常注入确认 held keys 被安全钩子清空、E-stop 保持锁定。该证据不表示硬件
已收到归零帧，也不把同步配置恢复宣称为非阻塞。

### 独立复验记录

- UI：4 个统一注册表套件、46 个用例 PASS，包括 presenter10、生产 renderer 故障6、
  compositor policy18、framebuffer generation12。它们覆盖 `b18d901` / `80b9909`，
  不是新增 `ef22dab` 的独立复验，也不是真实 GL driver 验收。
- 配置早期阶段：configuration boundaries16、真实 offscreen transactions4，共20个用例 PASS，
  覆盖 `6bd6bda` 的原始反例及回滚边界。
- 后端：policy boundaries6、characterization3、pure policy13，共22个测试方法 PASS；
  characterization 包含97个冻结基线案例。
- 额外差分重放：`409f5d0`，使用独立 seed20261017，10,131个案例，0差异、16个中断案例、
  0个无法观察的异常；原始 fixture 不变。复现：
  `python tools/compare_polar_policy.py --cases 10000 --seed 20261017 --json /tmp/policy-review.json`。
- 云台请求位：`03a6d72` 直接离线执行原脚本，74 个断言通过；额外变异分别删除请求、
  在线和非 E-stop 条件、错误增加底盘授权依赖、跳过迟到链路检查，五种变异均失败。
  仅串口 byte sink 和时钟为 fake，生产 manager/bridge/encoder/transport 均参与。
- GPU 首次复验与生命周期复验曾遇到统一验证器共享锁，均保留 INCOMPLETE 而不绕过。
  GPU 后续已通过；2026-10-16 05:08 UTC 在整合提交 `0a9947a` 完成资源关闭独立复验：
  6 个套件 PASS，0 SKIP、0 INCOMPLETE、0 FAIL。包括 backend async lifecycle17、
  resource ownership12、UI worker21、video ownership8、writer ownership7，共65个 unittest
  方法，另有云台请求位74个明确断言。该提交包含通用 final-retry 原语，不表示最终 UI 重试接线已完成。
- 后续阶段的独立直接运行：catalog13；音频事务8与真实窗口配置9；MQTT lifecycle21、
  backend final retry4、backend async22；Paho native ownership7与新增实际 manager retry5；
  window lifecycle controller17与实际窗口6；compositor fault16。每项以对应阶段提交为范围，
  有重复回归，不能相加冒充最终聚合总数。
- native stream `c40ef66`/`4f48e69`：5个 characterization 方法覆盖96个冻结有序状态/异常
  部分副作用案例，7个 pure policy 方法包含65,536个序号对；fixture 未刷新，Qt/decoder 独立导入通过。
- 05:34–05:40 UTC 的后续独立直接运行：failed-clear owner11 与 safety diagnostics6；
  CC 端口冲突3；compositor Qt False-return/异常22；真实 operator E-stop 写锁1；
  配置命令表征26与纯资格观测10。各自对应上表阶段，不并入此前整合提交的聚合成绩。
- device endpoint `043e556`：18个方法（含102个冻结有序副作用案例）独立通过；
  独立 seed20261018 的 digest-pinned 重放10,000例，646例中断，0差异、0无法观察。
  端点仍在原有位置惰性转换，阻塞的 CC 替换不会先解析坏频率而漏掉旧 writer neutral。
- 后续独立阶段：serial latch/旧快照交错3；emergency action7；实际窗口 lifecycle13；
  angle lifecycle3和原直接设置回调反例；runtime callback failure3和原 native-handle 反例；
  普通场景 configuration guard4。angle/callback 用例从相应 exact commit 导出隔离树运行，
  避免把作者工作树尚未提交的 controller 提取算进该阶段结果。
- 05:57–06:07 UTC 的独立阶段：最终 input observer4、含 activation 的精确提交5；
  controller/manager/retry/input/MQTT/真实窗口9套89；pointer及相邻窗口5套59；
  resource factories与相邻窗口5套40；解除急停5；DT7/角度端口6；可选受击计数10；
  provenance16。各套可能重复，不合计为单一整合成绩。controller 首次复验命令误写一个
  不存在的 MQTT 模块名，修正命令后89条完整通过；该导入错误未被改成 skip。
- 06:10–06:26 UTC 的独立阶段：profile identity/transaction30；P2 paired files与真实窗口29；
  decoder/UDP stop seam及既有所有权30；窗口 layer拒绝69、后续 painter/commit76；
  referee projection63（含210冻结案例）；动态 settings evidence6与DT7命令35个明确断言；
  build telemetry opt-out2。重复用例不求和；发布测试未执行真实 Windows freeze。
  economy4方法包含64冻结trace，额外使用真实窗口 add_hotspot 检查188个可见区域和24个空交集。
- 06:27–06:44 UTC 的独立阶段：视频 source `1c31097` 精确快照35；
  dirty-close `61da0cf` 精确快照34；pending-profile close `b8e66ae` 精确快照5；
  renderer owner `70622df` 及相邻方法74；writer/angle handoff20；经济布局/确认/绘制展开26；
  手工 map 保留18。source 结果不包含后发现的 recovery/Paho/serial starter 准入失败。
- 06:45–06:58 UTC：tick diagnostics48、frame telemetry59分别在精确提交独立通过；
  frame新seed20261016重放10,000帧，65次sink中断、0差异。
  另一位独立reviewer的有限线程准入审查在 `ecd58d3` 运行21套144方法、0skip、exit0，
  不并入本reviewer的阶段总数。其源码矩阵、原反例和保留日志见链接报告。
- 07:00–07:17 UTC：专用GL owner精确 `d8599b4` 的90条相邻方法、`51fd911` 的28条专用方法、
  source故障tick隔离35条和恢复HUD13条独立通过；重复用例不求和。恢复HUD随后发现
  stale点击释放泄漏，已由 `f094a3d` 独立闭合。GL状态证据仅为inert handles/Qt图像，不等于桌面driver资格。
- 07:18–07:22 UTC：texture-unit5条先行用例旧树4FAIL，修正后33专用/99相邻方法通过；
  API retirement精确 `300ed64` 的53方法和实际draw_watermark删除/恢复mutation通过。
- 2026-10-16 的生成模块映射已把历史 Tk 命中单列；人工第4节在 `2aa3fae` 撤回无 DT7 实现、
  停发等于车辆无残留状态的旧断言，改用当前函数入口和明确的硬件验证限制。

阶段作者测试、独立复验与最终整合结果分别记录，不能把多个分支上的成功直接相加为
整合树上的完整成功。

### 输入清理观察者的失败证据（2026-10-16 05:25 UTC）

`OfficialSafety._notify_input_clear` 原先把真实输入 hook 的所有异常直接丢弃。
本阶段保留既有“先改变安全状态、观察者不能向外抛异常”的契约，仅增加可查询的
`input_clear_failure_count` 和 `input_clear_failures`。历史最多8条，每条最多12个栈位置，
包含控制原因、异常类型及截断标记；不保存异常对象、locals、异常消息或源码文本。
记录过程不调用日志 handler、Qt 或文件读取，不引入安全路径上的 I/O；不可变记录也不被
DT7 的浅拷贝策略快照交叉修改。尚未新增 UI 告警展示，不能把可查询记录称为操作员已看见。

六个实际 hook 故障回归在修改前全部失败，修改后全部通过；另有36个 transport safety、
15个 DT7 worker 测试和198个 backend protocol 断言通过，静态 ratchet 通过。
此阶段没有修改 qualification、arming、E-stop 或发送授权判定。

### 结构判定与明确保留的未完成项

本轮存在真实的职责边界：catalog 的环境探索输入与复制装配、后端纯策略值、
生命周期 controller 的资源/audit 状态所有权、配置事务的显式参与者、渲染 pass 的
painter/FBO 清理所有权，都可以脱离完整窗口单独组合测试。兼容 façade、窄命名
forwarding 和 QObject parent 不单独构成耦合消除；是否缩小职责仍取决于具体操作和状态归属。
既有巨大 Mixin、窗口装配和输入分派没有被这些提取全部消除。

2026-10-16 06:42 UTC 的固定提交 `f784da9`、07:43 UTC 的 `b93a57f`，以及
08:04 UTC 的键盘整合头 `d3c0321`，以及08:21 UTC的最终生产整合头 `89842ab`，
只读 AST 报告如下。原基线取未改动的 `architecture_before.md`；这是源码结构快照，
不是运行验收。最终生产整合头的静态源码摘要为
`b3780a24711a1529d57a37b8f09f9903bed6e7c1bf69f5eb25a2bead2eeff577`。

| 信号 | 原始基线 | `f784da9` | `b93a57f` | `d3c0321` | `89842ab` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 生产 Python 模块 | 141 | 182 | 187 | 188 | 190 |
| 函数 | 2619 | 3110 | 3193 | 3211 | 3211 |
| 可执行 AST 语句 | 34124 | 35985 | 36562 | 36671 | 36668 |
| 决策点 | 11995 | 12598 | 12861 | 12901 | 12901 |
| 本地 import 边 | 423 | 539 | 552 | 553 | 562 |
| 初始化循环组件 | 0 | 0 | 0 | 0 | 0 |
| 包含延迟 import 的循环组件 | 3 | 3 | 3 | 3 | 2 |
| 历史 UI 函数（已计入总量） | 50 | 50 | 50 | 50 | 50 |

总量增加，不能宣称全项目复杂度下降。迁出的函数、保留的 Tk 实现和安全失败处理
仍在这些总量中。入口变短只是审查线索，不是完成评分。

- `PolarRMWindow.__init__`：`89842ab`、`d3c0321` 与 `b93a57f` 均有277个语句、261个直接 `self` 名称，
  早前 `f784da9` 为287/273。后者包含协作者和方法调用，不能冒充可变字段数；
  构造与装配责任仍集中。`polar_app` 仍有85条本地fan-out依赖。
- `EconomyMixin.build_gpu_economy_workspace`：198个语句、77个决策点；
  `draw_economy_overlay_compat`：179个语句、58个决策点。共享确认/几何以后仍是明显热点。
- `InputDispatchMixin.keyPressEvent`：文本编辑提取后为171个语句、100个决策点，
  原为189/112；全窗口优先级链和经济编辑仍保留，不能用入口局部下降代表全树下降。
  `_paint_gpu_frame_cached`：167个语句、69个决策点，最大控制嵌套仍为5。
- `d3c0321` 的三个延迟 import 循环组件逐边核对如下；后续 `febac3a` 闭合第二组，
  仍保留第一、第三组。初始化循环为0不能写成“没有循环依赖”。
  - referee/model/economy/scenarios：`model → scenarios → backend.referee.registry`，
    model也直接读取registry的profile默认值/列表；registry的`create_state_adapter`
    延迟导入`rm2026.state_adapter → model`，student分支则为
    `registry → student_legacy.state_adapter → rm2026.state_adapter → model`。
    另一闭环为`registry.available_actions → rm2026.economy_rules → economy → registry`。
    保留原因是profile声明/工厂与年度适配的运行时装配，以及economy值类型与默认年度兼容
    编码入口仍在同一模块。这是实际结构债务，不仅是兼容别名；可后续先隔离值类型及
    profile描述/装配边界，再固定年度派发和PyInstaller发现行为。state_adapter中的
    RobotState导入仅用于注解，调整它可能缩小组件，但不能消除economy闭环或证明职责解耦。
  - 设置页面/键位编辑器：`ui.cs2_settings_center → ui.jello_keybinds` 在capture/open
    入口延迟打开键位窗口；反向调用为`BindingSession._validate/apply →
    cs2_settings_center.validate_draft/apply_registry_draft`，另有
    `build_keyboard_manager → cs2_settings_center.install_estop_shortcut`。
    `febac3a` 已把PAGES/校验/提交移入`settings_application`，Qt过滤器移入
    `emergency_shortcut`；两者不导入任一dialog，键位编辑器在阻断page导入时仍能
    构造、校验、应用和处理F12。该循环已闭合，draft/live与失败顺序有真实dialog回归。
    原page保留同一函数/常量aliases；PAGES就地修改仍共享，但任意重绑定旧page名称
    不再改变迁出函数globals，不承诺旧模块热补丁行为。apply的host耦合仍明确保留。
  - 兼容启动装配：`ui.polar_app → ui.polar.app_entry.main → ui.polar_app.PolarRMWindow`。
    前一边是已有`polar_app.main`兼容re-export，后一边只在main执行时加载窗口。
    已有入口导入/异常/QApplication寿命测试；此循环有明确兼容与组合根理由，本阶段
    保留，不为清零静态数字改公开入口。
- 配置 pending/completion、窗口唯一 poller、最终关闭重试必须在同一整合树验证实际接线；
  真实窗口阶段用例已纳入第11阶段离线回归。settings-page 与 UI 事件的 pending guard 不代表
  所有程序调用都具有旧值恢复语义；只有匹配 generation 的 accepted snapshot 可以恢复旧值，
  未捕获的程序请求只拒绝 effects。已确认的角度 owner 重开反例有独立 backend 门控。
- input-clear 重授权、UI 急停前置 reset、teardown cache callback、持续清理失败阻塞
  teardown/audit 和 activation 迟到清理失败均已有独立修正，并纳入第11阶段离线整合。
- 窗口 layer False、绘制清理根因、post-present commit、失败另存/两文件普通 I/O、
  dirty-close 与 staged-close 反例已有上表独立修正、组合验证和第11阶段离线整合。
  profile/startup 不提供跨进程或 crash 原子性，GPU fake/offscreen 不证明 native GL owner 行为。
- source stop 三 owner 准入失败在 `1c31097` 已独立闭合；referee-profile 半提交和
  recovery/Paho/serial/UDP/coordinator准入故障也已有专门review的固定矩阵与修正。
  这11点范围不覆盖UI worker、POLAR/角度接收器、DT7/CC或日志writer的所有准入交错，
  不能泛化为“全部线程创建失败都安全”。这些修正已纳入第11阶段离线整合，
  UI视觉/camera另有独立范围报告，不能把它们拼成全库证明。
- 四类专用 GPU renderer 的bind拒绝、partial-native异常、unwind/destroy、纹理上传所有权
  和texture-unit清理已有上表独立修正；显式release(unit)是有证据的旧行为修正。
  [Qt 6.8 API契约](https://doc.qt.io/qt-6.8/qopengltexture.html#release)与状态机支持此修正，
  仍没有桌面视觉资格；旧成功trace只对明确列出的两条release参数作纠正，shader/几何未改。
- 第八阶段固定 `702f340` 的private API caller gate失败保留在历史报告；`300ed64` 对后续
  源码显式更新2条真实caller并退役2条内部storage保护，不能回写成原检查点通过。
- 原生 Windows/桌面 GPU、实际摄像头/串口/网络对端、固件归零确认均没有由本轮 fake 或 offscreen
  测试替代。缺失的环境证据继续单独列出。

静态质量 gate 在 `f784da9`、`b93a57f`、`d3c0321`、`febac3a` 与 `89842ab` PASS，仅证明没有超出已审查的 ratchet 和依赖边界。
`9cc587a` 初版搬入的新模块触发2项分号lint失败，`febac3a` 仅拆行消除，没有迁移或放宽预算。
`19b16c1` 的8项更小预算也经此固定报告独立验证，24项越界变异全部拒绝。
聚合结果及修正后的最终源码身份由整合报告给出；作者分支、独立快照、历史聚合成绩不互相替代。

### 已保存的整合检查点

[第七阶段完整离线报告](evidence/clean-code-20261016/wave7/README.md) 测试固定源码
`f784da9`，于06:44 UTC完成：186 PASS、12 SKIP、4 INCOMPLETE、0 FAIL。
零FAIL不等于release通过：缺失固件/参考资料仍被明确分类，runner保持非零退出。
本检查点不包含后续视频source/profile、线程准入修正、tick/frame诊断owner或更晚提交；
后续focused通过不能改写这个快照的覆盖范围。

历史[第九阶段报告](evidence/clean-code-20261016/wave9/README.md)：
07:42 UTC完成，干净源码 `b93a57f4a434bddd43c7d37b940811724e3c6952`，Git tree
`ba10d46fc7ba5d7ff54d37d6e4233e504e2ddff4`；212 PASS、12 SKIP、4 INCOMPLETE、0 FAIL。
该范围包含API证据修正、专用GL与unit清理、三vision worker no-wait、data/closer区分及
灰态按钮消费输入。camera worker/caller和随后真实pipe UI组合用例不在本检查点。
第八阶段private-caller失败仍属原树历史证据；第九阶段通过只说明修正后新树结果。

[第十阶段报告](evidence/clean-code-20261016/wave10/README.md) 保留固定源码
`6bd95ba` 的213 PASS、12 SKIP、4 INCOMPLETE、1 FAIL。唯一失败来自smoke中的
NativeOnlyBackend fixture缺少生产诊断入口需要的field_log。`c37aa8d` 让fixture使用
实际manager.field_log；独立比较确认原20个Assert AST未改，未以生产默认值掩盖缺项。

历史[第十一阶段报告](evidence/clean-code-20261016/wave11/README.md)：
07:59 UTC完成，干净源码 `016248546961d456b64b06fa46a10e964f383fdc`，Git tree
`92b44ebe2adda189c9f63174a8eb1f9c49477696`；215 PASS、12 SKIP、4 INCOMPLETE、0 FAIL。
包含camera worker/caller、独立Qt心跳、真实pipe窗口恢复、anchor修正与smoke fixture修复。
后续聊天/搜索提取不在该快照；固定 `a38e16a` 的27方法独立通过，随后进入第12阶段。
计数是检查条目而非unittest方法数。12 SKIP及4 INCOMPLETE全部来自所选offline检查的
固件树/队内CRC参考/官方dump/安装型patch前置条件。native Windows、桌面GPU与真实设备
资格未由该profile执行，是另一个验收缺口，不属于这16项状态。报告仍 `passed=false`，
不能改称release通过。

历史[第十二阶段报告](evidence/clean-code-20261016/wave12/README.md)：
08:10 UTC完成，干净源码 `d3c03213ef213daa9ab3b64c4779c8454d9010bf`，Git tree
`83d1ceb08ff0e81ca9f0378cd18e1173f77f7889`；218 PASS、12 SKIP、4 INCOMPLETE、0 FAIL。
包括键盘文本策略与真实窗口用例，不包括后续settings-page解环。

最终生产整合检查点为[第十三阶段报告](evidence/clean-code-20261016/wave13/README.md)：
08:26 UTC完成，干净源码 `89842abb642f4dc7d3c5815faf804b922b0a708b`，Git tree
`f7d7a997e0950012fa04be271c79826dfd130e4b`；220 PASS、12 SKIP、4 INCOMPLETE、0 FAIL。
该检查点包含settings-page解环及全部先前整合阶段，与本报告最终AST结构度量同源。
新增emergency_shortcut中的两项分号lint曾失败，修正仅拆行；历史契约、fixture与门禁预算没有为此放宽。
后续纯文档提交不改写已测试源码身份；若再修改生产/测试源码，应生成新的整合证据。

### 收敛验收矩阵（2026-10-16 08:28 UTC）

最终生产整合及静态度量均以 `89842ab` 为准。
下表列出有限范围内已经验证的结果以及尚未提供的能力或资格，不是全项目完成声明。

| 范围 | 已具备的可复现证据 | 仍需的验收或明确限制 |
| --- | --- | --- |
| 启动/兼容入口 | 无Tk隐式导入、Qt异常原样传播、已有QApplication窗口寿命；8例 | 实际桌面QOpenGLWidget启动与发行环境资格 |
| 后端纯策略/catalog | 冻结字段/顺序、JSON类型、完整与中断副作用差分；原fixture未刷新 | 真串口/网络/车端协议和归零确认不由fake重放证明 |
| 配置/文件/音频 | 精确registry/file/cache/pool恢复、身份绑定autosave、显式dirty-close、失败恢复可观察 | 单写者普通异常恢复；跨进程竞争和跨文件crash原子性未提供 |
| 生命周期/退出 | 保留native owner、失败句柄明确retry、audit drained/intact分开；实际Paho/pipe等组合故障；已纳入第11阶段 | 有限11点线程准入报告不是全库线程证明；native平台关闭行为仍需资格 |
| 视频停止/恢复 | source独立撤销、失败同target不自动重试、data owner与closer区分、实际UI generation/token；camera与真实pipe组合已纳入第11阶段 | [camera独立报告](CAMERA_STOP_REVIEW_20261016.md)另有实际window+worker+Qt心跳49方法；仍不等于真实cv2设备资格或永久卡住的native调用可强制终止 |
| 恢复HUD输入 | enabled、stale、visible busy实际mouse回归；灰按钮消费左键，拖动后首个自由move只重建anchor；已纳入第11阶段 | 只消费可见按钮范围，不代表整个输入路由已解耦 |
| 键盘文本编辑 | 原始源码固定、ordered/partial-state差分、真实MatchChatController和QKeyEvent；27方法独立通过并纳入第12阶段 | keyPress仍171语句/100决策，经济选择查询仍有副作用 |
| settings-page依赖方向 | 独立application/Qt shortcut adapter、保留aliases、阻断page导入可完成真实键位窗口流程；12新增独立通过并纳入第13阶段 | apply仍依赖明确host回调，旧模块任意名称重绑定不是兼容契约 |
| 绘制/GL资源 | 原始故障与独立cleanup保留、False拒绝、painter/FBO寿命、专用纹理候选及unit unwind | offscreen raster/inert GL状态不证明桌面driver像素、帧率或所有GL状态 |
| 契约/质量工具 | 原历史API字节不变，1036公共/78活动私有/540签名；retirement有记录；下调预算及负控 | 静态caller/依赖分析有反射盲区；既有大函数、Mixin和延迟循环仍保留 |
| 全树整合 | 第13阶段干净 `89842ab` 为220 PASS/12 SKIP/4 INCOMPLETE/0 FAIL；第10阶段fixture失败在后续树修复 | 16项所选检查前置条件未齐，仍为NO-GO；另有profile外native Windows/GPU/device与打包资格未执行 |

其余开放项按风险而非文件长度排序：

1. 只有在明确选定并具备固件、队内CRC、官方dump及安装型patch前置条件后，才可能闭合剩余16项所选检查；当前不把它们算作PASS。
2. native Windows/桌面GPU、物理音频/串口/摄像头/车端与发行打包仍需另行资格验证；它们是profile外缺口，不把零FAIL写成release-qualified。
3. constructor、全窗口输入优先级链、经济绘制、cached frame与referee适配仍是结构热点；迁出工作与保留Tk源码均计入全树总量。
4. profile/startup的普通异常恢复不等于跨文件crash原子；native永久阻塞无法由timeout或fake成功证明已释放，必须保持真实所有权和退出证据。

本轮可确认的结论是：列明的职责边界与已复现故障完成有界离线验证，最终冻结树
没有失败检查；同时全树结构总量增加、主要共享状态热点和两个延迟循环仍存在。
这支持保留本轮修正与证据，不支持“全项目已经完全Clean Code”或“可直接正式上车”的结论。
