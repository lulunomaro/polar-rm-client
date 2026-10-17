# Clean Code 原则验收映射（2026-10-17）

## 范围与结论

本次按用户指定的 [Clean Code 中文](https://aeryzhao.github.io/cleancode-book/) 原文逐章核对，
不是以文件拆分数、函数行数或数值评分代替验收。生产源码检查点为
`97228a73b90e7db73533af7e07a8f5a7e8fb0716`；本文件仅新增文档，不修改生产代码或测试。
下列“证据”区分可定位的实现与测试入口，不表示本次文档审阅重新执行了所有测试。

**结论：已有可保留的职责分离和错误恢复实现，但不足以宣布全项目符合整洁代码。**
[全客户端清单](whole_client_debt_inventory_20261016.md) 是历史范围索引；当前缺口应结合
[早期窗口资源审计](window_early_resource_audit_20261017.md) 与
[状态权威审计](robot_state_authority_audit_20261017.md) 阅读，不能把旧清单中已修复事项继续当作未修复缺陷。

## 后续证据更新（2026-10-17 20:37 UTC）

以上源码身份及下文原始检查点保留为历史记录，不将旧结论外推至新源码。
此前完成的音频聚合为公开 `d8a90c52d3a52b390c1a014750191dc5b760a3b7` /
本地 `bd7115ffdcab11c5771fac362aabe97f960b22bd`：**345 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL，整体 NO-GO**。详见
[音频准入修复](kill_audio_admission_repair_20261017.md)。后来字段日志候选
公开 `a44cf8d` / 本地 `197f4284e59efc432750e1cf6051c5b8f0e5ed18` 的全量 offline
聚合现已完成：**346 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL，整体 NO-GO**
（`passed=false`）。直接读取 `../client-field-log-full/results.json`，运行
`b9108a31ebd1461c93affd616b821afc`，开始于 2026-10-17 20:29:29 UTC；源码树
`daebc6193463f7bc74e24d4f84c106e50959c4ec`。695 个声明验证输入起止指纹一致：
`b448096787d185cda7ad5ccf3df23227997c19a350112502d7daf7d1d23d2625`，稳定性 PASS。
此指纹不包含文档、资产、外部 fixture 或安装包，也不检测两次采样间改动后还原。
本次更新不再有待完成的全回归；它仍不等于全部验收或原生设备通过。

- 错误处理与并发：音频构造/提交失败已有有界回退、已准入工作保留及重入停止测试，
  不证明实体扬声器重复播放。字段日志候选补齐普通线程准入异常的计数/所有权处理；
  排空不等于无损或 fsync 持久化。
- 状态职责：242 个声明字段已有有界生产者/默认值清单；正常裁判标量路径由 decoded
  adapter 发布，raw 消费保留接收新鲜度与事件。兼容接口、动态扩展及全部运行时语义
  仍不属于全局单写入者证明。
- 系统构造：指定 serial、MQTT/Paho、decoder 构造矩阵已补证；早期资源账本已枚举，
  feedback、空闲 wallpaper 及部分真实窗口失败前缀已有有界验证，不能称所有前缀安全。
  最小下一项是实际窗口启动锁清理 `_clear_control_inputs_for_lock("startup locked")`
  抛错时的原始错误及已获取所有者退休；该前缀未验证，不是已确认泄漏。

以下章节仍说明具体职责与限制；“最终聚合待完成”须按各自历史源码读取。
原生 Windows/GPU/音视频、外部协议/固件与物理设备验收仍独立开放。

## 第 3 章：函数

- **原则：** [3.2–3.3](https://aeryzhao.github.io/cleancode-book/ch3.html) 要求一个明确目的、同一抽象层级；
  提取应表达概念，而非给原语句换个函数名。
- **实际证据：** [preferences.py](../../rm_client/configuration/preferences.py) 的
  `prepare_preference_draft` 负责完整草稿预检，返回值对象；
  [settings_application.py](../../rm_client/ui/settings_application.py) 承担窗口副作用。
  本轮 `_validate_new_control_binds` 补齐新驾驶键与模块键的反向冲突检查，
  允许同一草稿交换键位，保留历史重叠。
  [七项回归](../../tests/test_preference_binding_conflicts.py) 覆盖拒绝与合法边界。
- **剩余差距：** 设置适配器仍经由整个 `host` 访问多项服务；输入路由、配置恢复和绘制路径仍混合多个窗口职责。
  下一次提取需明确输入、输出和有序副作用，并保留事件优先级；仅移动方法不能证明抽象层改善。

## 第 7 章：错误处理

- **原则：** [7.4–7.5](https://aeryzhao.github.io/cleancode-book/ch7.html) 强调失败上下文及调用者可处理的异常边界；
  正常流程和恢复流程应可辨认。
- **实际证据：** [transactions.py](../../rm_client/configuration/transactions.py) 的
  `ConfigurationApplyError` 分别保留原始原因、恢复错误、安全清理错误和异步恢复状态；
  `rollback_configuration_transaction` 独立尝试恢复步骤。
  本轮 [control.py](../../rm_client/control.py) 的 `release_all` 不再因发布失败跳过独立归零出口；
  [失败测试](../../tests/test_control_release_all_failures.py) 断言后续清理及错误可见性。
- **剩余差距：** 这些证据覆盖命名边界，不证明所有异常路径均完整。
  [早期构造审计](window_early_resource_audit_20261017.md) 指出既有 abort 边界之前的资源清理覆盖尚不完整；
  需逐个失败前缀验证，不能把捕获异常或 UI 提示等同于资源已经释放、物理输出已经归零。

## 第 9 章：单元测试

- **原则：** [9.5 F.I.R.S.T.](https://aeryzhao.github.io/cleancode-book/ch9.html) 要求快速、独立、可重复、自动判定、及时；
  测试应让行为约束清晰，支持安全修改。
- **实际证据：** 本轮先复现快捷键冲突，再验证修复与合法交换；
  [重入测试](../../tests/test_input_cleanup_reentrancy.py) 和
  [角度线程准入测试](../../tests/test_controller_angle_exception_admission.py) 覆盖失败而非只有正常完成。
  [verify_all.py](../../tools/verify_all.py) 与 [test_registry.py](../../tools/test_registry.py)
  区分 PASS、FAIL、SKIP、INCOMPLETE，并检测测试期间源码变化。
- **剩余差距：** 原始文档检查点的组合全回归当时仍在运行；后续完成结果见上方更新，不能将局部成功或旧源码结果外推。
  历史 [325 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL](input_cleanup_failure_isolation_20261017.md)
  属于此前精确源码，整体仍是 NO-GO，不认证本轮修改。
  共享 fixture 不等于独立预期值；原生窗口、设备和固件前提须单独补证。

## 第 10 章：类

- **原则：** [10.2 单一职责与内聚性](https://aeryzhao.github.io/cleancode-book/ch10.html) 按变化原因和共同状态组织类，
  不是按固定长度切碎类。
- **实际证据：** [ProfileSaveState](../../rm_client/configuration/profile_saves.py) 拥有已接受身份、草稿和延迟保存状态；
  [PendingConfigurationChange](../../rm_client/configuration/pending.py) 拥有 generation 与阶段判定；
  [tick_controller.py](../../rm_client/ui/polar/tick_controller.py) 将时钟策略与 Qt 窗口隔离。
- **剩余差距：** `PolarRMWindow` 及其 mixin 仍共享广泛可变状态，分文件不代表独立对象边界。
  [RobotState 审计](robot_state_authority_audit_20261017.md) 已记录声明字段归属；正常裁判标量路径后续收敛至 decoded adapter，但兼容写入者及所有局部消息的语义一致性未获全局证明。
  应按状态所有权、写入顺序和具体变化原因验收后续提取；保留兼容导出与行为，不做整类机械替换。

## 第 11 章：系统

- **原则：** [11.2](https://aeryzhao.github.io/cleancode-book/ch11.html) 将对象构造、依赖装配与运行时使用分离。
- **实际证据：** [app_entry.py](../../rm_client/ui/polar/app_entry.py) 和
  [window_assembly.py](../../rm_client/ui/polar/window_assembly.py) 提供装配入口；
  [backend/lifecycle.py](../../rm_client/backend/lifecycle.py) 接收脱离窗口的配置或资源所有者，
  阻塞清理不通过调用窗口观察者执行。
  [构造准入独立审阅](constructor_matrix_root_review_20261017.md) 对指定 serial、decoder、Paho 单元有正向所有权及拒绝测试。
- **剩余差距：** 窗口仍是重要组合与运行协调点；已覆盖构造单元不代表所有早期构造前缀安全。
  后续应以 [早期资源账本](window_early_resource_audit_20261017.md) 指定的获取、发布、释放边界为对象，
  证明失败后无重复所有者、可重试、保留原始错误，不能以模块数量证明系统解耦。

## 第 13 章：并发编程

- **原则：** [13.3 与结论](https://aeryzhao.github.io/cleancode-book/ch13.html) 将并发管理作为独立职责，
  限制共享数据并专门验证关闭等边界。
- **实际证据：** [lifecycle.py](../../rm_client/lifecycle.py) 与
  [backend/lifecycle_controller.py](../../rm_client/backend/lifecycle_controller.py) 分离资源生命周期和管理端协调；
  配置完成以 generation 过滤迟到结果。
  本轮 [ControllerAngleRelay](../../rm_client/backend/controller_angles.py) 在准入异常后保留真实已启动线程所有权，
  对从未启动的线程采用不同恢复路径；
  [专项测试](../../tests/test_controller_angle_exception_admission.py) 检查拒绝、已准入和清理重试。
- **剩余差距：** 确定性故障注入不证明所有线程调度或原生驱动阻塞均有界；
  不把终止请求、入队或 join 返回等同于实际设备完成。
  保持资源唯一所有者、代际失效、停止后不重放旧输入，并分别记录真实驱动与系统调度证据。

## 本检查点验收边界

可以保留上述具体实现与回归；不能据此给全仓库评分或宣布全面通过。
原始检查点待聚合状态由上方后续证据更新取代；字段日志候选已有自己的固定源码聚合，346 PASS 仍伴随 12 SKIP 与 4 INCOMPLETE，不能视作整体通过。设备、Windows/Qt/OpenGL、音视频和固件验证另行记录。
结构性缺口是后续审阅入口，只有可复现的行为失败或明确的职责冲突才支持新增修正，不能自动变成大规模重写任务。
