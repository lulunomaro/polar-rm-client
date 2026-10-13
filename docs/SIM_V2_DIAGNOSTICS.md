# v2 客户端故障诊断（CPU 阶段）

`runtime.transport.diagnostics()` 返回 `polar.sim.client.diagnostics.v1`。它不发网络请求，也不产生控制命令。适用于 `--sim-platform-v2` 的客户端侧观测；v1和真实设备出口不变。

## 记录内容与精度

- SDK：每次已尝试请求的固定path/method、thread、request_id、local monotonic开始/返回/失败时刻、session/robot、lease/binding/seq/window身份、canonical拒绝原因或未知原因。
- Transport：窗口请求/ACK安装、等新输入、adapter pulse、撤权前与清空后阶段；最后已收到的state episode/sim_time、其local接收时刻和age、输入/heartbeat龄、connection/control/watch/plan generation。
- SDK与transport各一个默认256项有界ring，并各保留对象存活期间第一次failure的独立冻结快照。新成功请求、stop或重连不覆盖它。手动创建新transport实例是新记录域。
- SDK的 `response_received` 只代表通用schema解析成功。具体ACK可能随后失败，并另记 `acknowledgement_invalid`；它不等于已执行/实际消费。
- 日期/事件均来自本地monotonic。server时钟不与本地直接相减；sample_sim_time是最后已知采样，不是错误发生时的推测sim时间。没有可用state时相应字段null。网络/actor/真实物理消费仍须独立server receipt。

字段级清洗：lease_epochs只接受motion/gimbal/fire三个exact非负安全整数；身份字段不接受bool/float冒充integer；身份字符串只保留保守ASCII标识符形态，URL/凭据/路径形态置null；字符串有界；不递归接受任意dict。记录中不含Authorization、bearer、token、URL、完整请求体、完整响应体、图像或任意异常文本。未识别server错误只存unclassified_error。

记录是best-effort旁路。记录器/诊断clock失败绝不能阻断原neutral/_failed清空；`dropped_events` / `clock_failures` 显示诊断缺失。诊断时钟损坏时保留已知身份，时间/age未知则null；记录器自身完全损坏时可能缺少first_failure，只能报告dropped_events。失败后state和lease照常撤销。`stop_close_attempt_finished`仅说明原关闭尝试返回，不能推断线程一定停止；未退出线程有单独计数。

## 资格与限制

本阶段仅CPU mock/假时钟，不新跑MuJoCo、相机、native或Qt窗口。不会把此前缺失的原因回填到旧失败记录。

预取确定性反例使用原client加实际ControlPlane纯Python，server clock人为偏移10000秒：新窗请求后30ms ACK、下一20ms控制节拍会使原activation先过期。服务端正确撤销binding，旧输入不会借新窗口续命。这个机制反例不是对历史实跑故障的唯一归因。

本次诊断增量**没有修改**原150ms服务端窗口、50ms预取阈值、HTTP重试策略、算法、物理参数或最终消费门。任何后续节拍优化应独立封件并保持旧动作原deadline，迟到仍要求显式重新接管。

## CPU测试

从Polar客户端checkout根运行（现有测试依赖需已安装）：

```sh
PYTHONPATH=.:tests python -m unittest test_platform_diagnostics test_platform_control_windows test_platform_adapters.CatalogTests test_platform_adapters.WorkerTests test_sim_platform_v2.TransportTests
```

以上选择纯逻辑类，不创建QApplication。不能把整模块中其他原Qt测试的显示环境问题当作本套CPU失败。

SDK与transport是两个独立故障流，其first_failure可能属于不同并发request。只能按request_id、session和代际证据关联，不能因wall时间接近强行拼接；ID未知/不一致或记录已淘汰时保持unknown。
