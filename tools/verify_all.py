"""一条命令验证「客户端侧」是否全部就绪，给出 GO / NO-GO。

为什么需要它：这个项目现在有 9 套 Python 测试、2 个 C 编译/行为校验、1 个固件
准备自检。它们散在不同文件里，而最终要判断的其实只有一句话：

    客户端这边还能不能挑出问题？能不能放心去 Keil 编译？

本脚本把它们串成一次运行，任何一项红就 NO-GO 并列出是哪项。

跑法：
    python tools/verify_all.py
    python tools/verify_all.py --firmware <StandardRobotpp 路径>   # 附带固件准备自检

注意：这里全部是**离线**检查。它证明不了"车真的会动" —— 那需要硬件。

并行跑两个 verify_all 会出现"假红"：固件自检共享同一棵工作树，
其中一个会 git checkout 还原别人刚装的桥。已加文件锁拒绝第二个实例
（实测：同一份代码一次报 3/51 红、一次 51 全绿，红的那次不是代码坏）。
因此这份清单的红色结果只有在"只有一个实例在跑"时才可作为证据。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHON_SUITES = (
    "polar_spec_test",        # 协议规格：加载/校验/改 JSON 真能改行为/新增段零 Python
    "robot_roles_test",       # 兵种对齐：限幅、禁驾、不一致告警、键位分层
    "polar_link_test",        # 帧编解码、CRC 三方对拍、时间戳单调、fire 恒 0
    "polar_bridge_test",      # 段→HUD 映射、驾驶通道门控、改绑、云台出口互斥
    "polar_integration_test", # 端到端回环：官方场景开车、无 0x1E 的 sim2real、急停竞态
    "polar_selftest_test",    # 无头自检本身
    "polar_startup_test",     # 真实窗口：链路 tick、HUD 渲染、改绑持久化
    "gimbal_host_request_test", # 云台直控请求位：电平语义、急停撤回、三条件闸门
    "autoaim_settings_test",  # 自瞄 Fov/Priority 接线：换算精确、未标定不加门、降级不谎报
    "hud_telemetry_settings_test",  # HUD 遥测面板 Show Bar/Warn/Show Limit 接线与两处一致
    "hud_status_settings_test",     # 战况面板 12 个开关：三个构造器行为必须一致
    "hud_batch16_killfx_test",        # 击杀特效：锁定目标加分开关→标签→提示音贯通
    "hud_batch17_crosshair_test",     # 准星样式：同名洗白的隐藏孤儿；默认档逐图元不变
    "hud_batch18_powerguard_test",   # 功率保护两个告警阈值：模块关着逐值不变、开了只能加严
    "hud_batch19_bufftimeline_test", # 增益时间轴：从“整个模块没消费者”到有面板（含真窗口像素）
    "hud_batch20_robotoverlay_color_test", # 装甲盒配色 Color：默认档三个消费点逐值不变；Threat 降级不静默
    "hud_batch21_radaroverlay_map_test", # 雷达叠加 Map：默认 Both=接线前两张图都画；Mini/Full 按展开态分档
    "settings_doc_consistency_test", # 孤儿数的 7 处出处（文档/闸门/实测）必须一致
    "hud_batch14_settings_test",       # 状态卡片：三处消费点共用判据 + GimbalPower 三项
    "hud_batch13_settings_test",       # 地图标记：默认字节不变 + 编解码往返
    "hud_batch10_settings_test",      # 缓冲上限写入状态（静态分支）+ RM 指示灯开关
    "hud_batch9_settings_test",       # 电容单一数据源 + 装甲盒深度边开关
    "hud_batch8_settings_test",       # 第 8 批：启动二级页/锁定框/弹药告警/缓冲能量
    "hud_overlay_settings_test",    # RobotOverlay Robot ID / Crosshair Heat Color（第 4 批）
    "backend_protocol_test",  # 官方裁判协议（回归护栏）
    "api_freeze_test",        # 窗口成员冻结（防误删公开面）
    # ---- 2026-09 补登记 ----
    # 下面这几个 suite 之前**存在但没登记**在这里，等于它们的红没人看得见。
    # 是 tests/test_suite_selfcheck.py 报出来的（它专门查"关键 suite 有没有
    # 被闸门覆盖"）。教训：写完新测试不等于它被跑了。
    "protocol_crc_test",             # CRC-8/MAXIM 与 CRC-16/X-25 黄金向量
    "polar_link_failclosed_test",    # 解码器 fail-closed（含畸形输入模糊测试）
    "overlay_write_guard_test",      # overlay 只能写白名单字段
    "tongji_postprocess_parity_test",  # AI 后处理与 Python 逐位一致
    "settings_access_test",
    "dead_callers_test",
    "settings_access_call_sites_test",          # 设置项读取：6 处手写收敛后的逐位等价
    "spin_readback_test",            # 自转 0x1F 回读闭环（AGENT.md 不变量 9）
    "safety_input_clear_hook_test",
    # 第 68 轮新增：一帧出错不许拆掉整个推理线程。
    "tongji_frame_fault_test",  # 第 53 轮：松键钩子抛异常时失败要看得见
    "hotpath_cache_test",            # 热路径缓存不返回同一份可变对象
    "robot_health_field_test",       # 敌方基地血量不得当成机器人血量
    "polar_link_snapshot_test",      # polar_link 快照是单一来源
    "udp_transport_test",            # UDP 传输：丢包/乱序/大帧
    "combat_events_test",            # 击杀判定的累计增量语义
    "economy_safety_test",           # 经济准入与操作员开关
    "config_store_io_test",          # 配置落盘与并发写
    "vision_pipeline_hardening_test",  # 视觉管线的空帧/坏帧处理
    "field_log_async_test",          # 现场日志异步写盘
    "control_loop_test",
    "control_idle_zero_direction_test",             # 控制回路限频与闸门
    "test_suite_selfcheck",          # 套件自检：无零断言测试、关键 suite 都被登记
    "encode_invariant_attack_test",  # 攻编码路径：fire 恒 0、禁位不上线（不只查常量）
    "zero_frame_and_diagnostics_test",
    "rust_port_coverage_test",
    "mock_output_gate_fragility_test",
    "mqtt_link_bad_port_test",
    "mqtt_backend_branches_audit_test",  # 75 个决策点，审计前 32 条零覆盖（自回声分账/通用解码）
    "polar_spec_bad_input_test",
    "mqtt_dispatch_liveness_test",  # 把 6 条"挂死型"检出变成带标签的活性断言（不是靠 sleep）  # 69 个决策点，坏规格输入；规格坏了会静默回退内置表  # MQTT 端口写坏时 sync_mqtt_link 不该抛 —— 已修的缺陷
    "field_log_branches_audit_test",  # field_log 74 个决策点里 31 条改坏了没人发现
    "manager_safety_branch_audit_test",  # manager.py tick 之外的部分 + safety.py
    "polar_link_bridge_branches_test",  # polar_link 52 + polar_bridge 83 个决策点，260 变异 118 存活  # 守住"写入端收敛成 bool"这个前提 —— 闸门朝 fail-open 方向坏
    "tick_polar_link_branches_test",
    "control_untested_branches_test",  # control.py 144 个决策点里 27 个从未走到（含 DT7 粘键误报那条）
    "model_state_branches_test",  # model.py RobotState 45 个决策点里 32 个从未走到（71.1%）
    "config_store_branch_audit_test",  # config_store + serial_ports 128 个分支型决策点里 39 个从未走到
    "spin_calib_matchmode_branches_test",  # auto_spin + calibration + match_modes 同款审计  # 60Hz 主泵 111 个决策点；补 54 个零覆盖分支（变异验证挖出来的）  # 48 个 Python 模块逐个有交代：新模块忘了登记就红  # 断流归零（stop/下降沿全零）+ s[0] 诊断提示（变异验证挖出来的三处零覆盖）
    "apply_one_dispatch_test",  # _apply_one 分发表契约 + 变异验证挖出来的 15 个零覆盖分支
    # ── 第 107 轮：把三个此前**从未在门禁里跑过**的套件登记进来 ──────────────
    #
    # 一次变异审计量出：**4553 条断言里有 1114 条（24%）从不执行**，
    # 因为下面这三个文件不在 PYTHON_SUITES 里。
    #
    # 最要命的是 `docs_and_tests_test`：**它正是钉住"新模块忘了登记就红"的那个守卫**，
    # 而它自己没被登记 ⇒ **那个守卫从不运行**。
    # 现在它跑了，77 项断言。
    #
    # `smoke_test` 是全仓最大的单个套件（1114 条断言，真窗口渲染 / HUD / 后端联动）。
    # 它的退出码之前对门禁完全不可见。
    #
    # ⚠ 登记前逐个实测过（不是照抄名字）：三个都 exit=0 才登进来。
    #    `docs_and_tests_test` 一开始是**红的** —— MODULE_BACKEND_MAP.md 过期，
    #    因为第 98–101 轮改了 manager.py / polar_transport.py，文档里的行号锚点全漂了。
    #    **先跑 `python tools/module_backend_map.py` 重新生成，再登记。**
    "docs_and_tests_test",   # 文档与代码一致性 + 「每个模块都有交代」的登记守卫（77 项）
    "smoke_test",            # 全仓最大的单套件：真窗口渲染、HUD、后端联动（1114 条断言）
    "gpu_compositor_test",   # GPU 合成器路径（无 OpenGL 上下文时也会如实报告）
)

C_CHECKS = (
    ("tools/firmware/test_patch_real_headers.py", "补丁代码用固件真实头文件编译"),
    ("tools/firmware/test_patch_logic.py", "补丁代码行为断言（含 tick 回绕、竞态）"),
    # 上位机<->底盘 桥接（上行 0x04 / 下行 0x1F）：把小陀螺从“借 SHIFT
    # 边沿翻转”改成“显式设定 + 回读闭环”。这一项同时含编译与 9 组行为断言。
    #
    # 与上面两项的相互干扰已处理：三个脚本都会先判断“目标改动是不是已经在
    # 树里”，在就不动 git。否则 prepare_hero_build.py 刚准备好的工作树
    # 会被后一个脚本的 finally 里 git checkout 静默撤销，造成“报 GO 但改动已没了”。
    # 2026-09-05 补严：光"已在树里就不动"还不够——那几个脚本收尾用的是
    # `git checkout -- .`（想把**自己**装的桥撤掉，实际把**整棵树**所有改动一起冲掉，
    # 包括 prepare 写好的兵种 include 与 MOUSE_SENSITIVITY；本轮真的复现过一次：
    # 英雄参数被冲回 100000.0f，紧接着新加的体检就 NO-GO）。
    # 现在统一走 tools/firmware/bridge_fixture.py：装之前按字节快照，收尾原样放回。
    ("tools/firmware/host_bridge/test_host_bridge.py",
     "自转桥接（0x04/0x1F）编译 + 行为断言"),
    ("tools/firmware/motion_feedback/test_motion_feedback.py",
     "速度回读桥（0x1E）编译 + 运动学往返断言"),
    ("tools/firmware/gimbal_host/test_gimbal_host.py",
     "云台直控桥（0x04 param0）结构 + 编译 + 真值表断言"),
    # 驾驶键位三方写入者（DT7 解码 / 无键盘遥控器置零宏 / usb_task 合并）。
    # 这一项必须是**可执行**的行为校验：粘键和整帧覆盖都是运行时语义，
    # 编译器一句都不会报。带 --negative 还能证明这些断言抓得住旧写法。
    ("tools/firmware/host_bridge/test_rc_key_ledger.py",
     "驾驶键位账本（DT7 不被抹 / 松键能归零）8 组行为断言"),
    # DT7/DR16 官方 18 字节帧的位序：把固件里真实的 sbus_to_rc() 编成 exe，
    # 用 Python 打的帧喂进去逐字段比对。这一项防的是"手册/固件/客户端三方
    # 位序悄悄错开"——键位错一位就是车上按 Shift 变按 Q 那类现场怪事。
    ("tools/dt7_link/test_dbus_frame_vs_firmware.py",
     "DT7 官方 18 字节帧位序 == 固件真实解码（逐字段 + 常量守卫）"),
    # 图传键鼠链路。两项都要，因为它们查的是不同的东西：
    # layout 查"客户端打的字节"和"C 结构体"是否还对得上（改了任何一端、
    # 或 __packed__ 没生效都会红）；行为查五档仲裁与三条底线。
    ("tools/firmware/km_link/test_km_payload_layout.py",
     "PRM1 键鼠载荷：C 结构体偏移 == 客户端 struct 打包（逐字段）"),
    ("tools/firmware/km_link/test_km_link.py",
     "图传键鼠五档仲裁 + 摇杆底线 + 开火权限（喂客户端真字节）"),
    # 客户端这一半：默认关、关着不多发、松键能清零。
    ("tools/dt7_link/test_radio_km_client.py",
     "无线键鼠出口客户端侧（默认关 / 不粘键 / 载荷 26B / UI 开关三道闸）"),
    # DT7 旁听（只读）：分帧与键位位序判读的离线断言。现场那条
    # "Q 到底在 bit4 还是 bit6" 的死结靠它判，所以它必须一直绿。
    ("tools/dt7_link/test_vcp_listen.py",
     "DT7 VCP 旁听：空闲线分帧 + 键位 bit 跳变双位序判读"),
    # 抓包差分：现场只抓一次官方选手端<->DT7，位序与键位字节靠它算，
    # 所以它的判据必须一直可靠（含"不许硬判位序"与"不许乱认键位"两条）。
    ("tools/dt7_link/test_frame_diff.py",
     "DT7 抓包差分：键位字节/位序 判定（合成样本，含两类不许乱判）"),
    # 自定义控制器出口：帧必须被**固件里那份官方 CRC 代码**验收通过，
    # 否则选手端整帧丢弃，现象是"发了但没反应"，最难查。
    ("tools/dt7_link/test_cc_link.py",
     "自定义控制器 0x0306 出口（帧被固件 CRC 验收 + 三道闸 + 不静默丢键）"),
    # DT7 直连：帧/位域/CRC/默认关 都可离线证；命令号仍是候选值，
    # 所以断言名字里写着"未确认"，防止后人当成已定论。
    ("tools/dt7_link/test_dt7_link.py",
     "DT7 直连出口（5A/22B 帧对厂商反汇编 + 两族 CRC 互不认 + 真发闸/互斥/松手）"),
    # 挑口：认错口的代价不是"连不上"，而是产出假结论 ——
    # 把遥控器帧写进 C 板的 CDC，再拿同一块板的 km_key 判读，永远得到 NO-CHANGE，
    # 于是我们以为"DT7 不认这一帧"，白换一整轮帧型。
    ("tools/test_settings_are_read.py",
     "设置项接线审计（每个 RobotLink 项都被取用 + Port/Baud 优先级规则）"),
    ("tools/test_port_picking.py",
     "Auto 挑口（DT7 只认 DJI VCOM / CC 只认 FTDI / 车的口一律判负）"),
    # C 路闭环自检工具的判据本身也要有证据：说 PASS 必须真是"我们发的位到了车上合并键位"，
    # 而且三种失败形态（DT7 不理帧 / 载荷错位 / 读不到回读）都不许误判成通。
    # D 路（车端 USB<->WiFi 桥）两条：桥本身是裸管道（不改字节、断链不再写串口），
    # 以及接到客户端传输层之后"提交意图 -> 字节原样落到 C 板、上行帧能拼回来"。
    ("tools/test_car_bridge_loopback.py",
     "车端桥裸管道（分片到达不改字节 / 上行拼回帧 / 断开后不再写串口）"),
    ("tools/test_car_bridge_transport.py",
     "D 路接进传输层（tcp 模式不被 Stub 端口挡住 / 意图落到串口 / 连不上报错清楚）"),
    # 端到端（离线）：闸门 -> 控制层 -> 运行期 DT7 出口 -> 假 DT7 -> "车上那本键位账"。
    # 分层各自绿不等于接起来能用，所以这条链要单独钉住。
    ("tools/dt7_link/test_end_to_end_c_link.py",
     "C 路端到端离线（按键到车上键位 / 松手 / 急停交还 / 互斥 / 鼠标截断）"),
    # 扫帧型工具：必须"只认出那一种"。它自己就被两条断言抓出过 bug
    # （假车攒陈旧帧 => 六种全 PASS；循环里发的是运行期那一种 => 六种其实是同一种）
    ("tools/dt7_link/test_dt7_sweep.py",
     "C 路扫帧型（只认得出那一种 / 清零帧 / 无回读不结论 / 未定案不写字节）"),
    ("tools/firmware/test_prepare_hero_options.py",
     "prepare_hero_build 新选项（--mouse-sens 改得动且幂等 / 注释吞 define 能被体检抓住）"),
    # 界面上那个"Run DT7 Link Selftest"按钮的可测部分：判据、两个把手，
    # 以及"设置项/分派/处理函数"三处必须成对（防按钮变成空壳）。
    ("tools/dt7_link/test_dt7_ui_selftest.py",
     "C 路界面自检（X 探针无消费者 / 把手作用到发送 / 按钮不是空壳）"),
    ("tools/dt7_link/test_wireless_selftest.py",
     "C 路闭环自检（假车真回环 / NO-CHANGE 与 PARTIAL 不误判 / 假阳性防线）"),
    # broker 地址：正式赛的键鼠要发到赛事引擎那台 192.168.12.1，
    # 界面上那个地址以前是摆设（改了没人读），现在必须有断言钉住。
    # 三个子代理复查抓出来的坑（reserved 落在 ch[4]、限流吃掉鼠标增量、
    # MOUSE_SENSITIVITY 量级）：钉成断言，防止下一轮又把默认值改回去
    ("tools/dt7_link/test_dt7_review_fixes.py",
     "C 路复查三个坑（reserved=通道中值 / 限流不吃鼠标 / 镜头灵敏度量级）"),
    # 松手帧必须落地：DT7/DR16 没有过期自动清零，被 max_hz 挡下的中立帧就是
    # "手松了车还在走"。这一坑是假时钟 24 个相位扫出来的（旧代码 24/24 全被挡，
    # 最好情况 253ms 才补上），所以断言也必须按相位扫，不能只测一个 lucky case。
    ("tools/dt7_link/test_dt7_release_flush.py",
     "C 路松手帧（限流不许吃掉零键 / 写失败要补发 / 暂停与关出口不许空转 / 水印说实话）"),
    ("tools/test_mqtt_link_settings.py",
     "MQTT broker 地址接线（改设置就换地址 / 已连接会重连 / 非法值回写不骗人）"),
)

#: 前后端对齐审计（防倒退闸门）。
#: 孤儿基线当前为 **22**（排除翻译表后的口径），只许减不许增。
#: 演进：71（初始真实值）→ 68（瞄准+遥测）→ 56（战况面板 12 项）
#: → 53（顶栏 3 项）→ 51（覆盖层与准星）→ 50（渲染后端 Renderer）→ 48（超限脉冲与发光）→ 47（审计修正：常量别名不再被误判）
#: → 43（启动二级页、锁定框、弹药告警、缓冲能量）
#: → 42（装甲盒深度边；并修正电容读 mock 字段的既有 bug）
#: → 40（RM 指示灯开关；并修正缓冲上限写错分支的死代码）
#: → 38（雷达照射高亮、锁定脉冲）→ 36（两颗增益灯）
#: → 34（地图标记类型与广播开关）→ 31（云台电压三项）→ 30（装甲盒开关）
#: → 29（锁定目标加分；上一轮判成“需改绘制层”，实际只差一行供值）
#: → 27（功率保护的两个告警阈值；该模块默认关着，所以接线零画面风险）
#: → 24（增益时间轴三项；模块本来连面板都没有，位置是量出来而不是猜的）
#: → 23（装甲盒配色 Color；默认值改成等于接线前画面的 White，三个消费点逐值不变）
#: → 22（雷达叠加 Map；默认值改成等于接线前画面的 Both，两条面板路径共用一个门）。
#: 其中两项查证后故意不接（MQTTLink/Auto Connect 与 UDPVideo/Reassemble），
#: 理由见 tools/SETTINGS_GAPS.md。
#: 早期版本没排除 `jello_locale.py`（中英翻译表），把“有中文名”当成“有人读”，
#: 曾错报 36/33。假数字比不知道数字更坑。
#: 另有 51 个同名设置项被判 AMBIGUOUS，**不计入也不计减**基线。
AUDIT_CHECKS = (
    ("tools/audit_settings.py", "二级界面设置项孤儿数不超基线",
     ["--max-orphans", "22"]),
)


# 第 102 轮：门禁必须自己给子进程一个**能写、且在工作树外**的临时目录。
#
# 为什么必须（子代理实测的结论）：
#   * 本机的整个用户目录**对 Python 子进程不可写**：
#     `%TEMP%` / `%LOCALAPPDATA%` / `%APPDATA%` / `%USERPROFILE%` 全部 `PermissionError`。
#   * 所以 `tempfile.gettempdir()` 把候选位置一个一个试完、全部拒绝，
#     **回退到 `os.getcwd()`**，而门禁把 cwd 设为 ROOT —— **临时目录落在 git 工作树里**。
#   * 后果：每个套件的 `mkdtemp()` 都写进仓库，zig 也拿到不可写的缓存目录。
#   * 部分套件还直接读 `os.environ["TEMP"]`，根本不经 `tempfile`。
#
# 工作树**父目录**是可写的（已实测），且**不在 git 工作树里**。
_SCRATCH = None


def scratch_dir() -> str:
    """返回一个可写、且**在 git 工作树之外**的临时目录。"""
    global _SCRATCH
    if _SCRATCH is None:
        base = ROOT.parent / ".rm_verify_scratch"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".w"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            _SCRATCH = str(base)
        except OSError:
            # 父目录不可写时退回原行为（不把门禁变红，只是不修它）。
            _SCRATCH = tempfile.gettempdir()
    return _SCRATCH


def scratch_env() -> dict:
    """给子进程的环境：把临时目录统一指到 scratch_dir()。

    同时设 `TEMP` / `TMP`（部分套件直接读它，不经过 `tempfile`）和
    `ZIG_GLOBAL_CACHE_DIR`（zig 的临时目录另设一处，不看这个环境变量）。
    """
    env = dict(os.environ)
    scratch = scratch_dir()
    env["TMPDIR"] = scratch
    env["TEMP"] = scratch
    env["TMP"] = scratch
    env["ZIG_GLOBAL_CACHE_DIR"] = str(Path(scratch) / "zig-cache")
    return env


def run(label: str, argv: list[str]) -> tuple[bool, str]:
    env = scratch_env()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 隔离用户数据：这些测试会构造真实窗口并自动保存配置，不隔离会污染开发机存档
    env.setdefault("POLAR_USER_DATA_ROOT", tempfile.mkdtemp(prefix="verify_all_"))
    proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    passed = proc.returncode == 0
    if not passed:
        bad = [l.strip() for l in out.splitlines()
               if l.strip().startswith("[FAIL]") or "AssertionError" in l
               or "error:" in l or "FAILED" in l]
        return False, " | ".join(bad[:3])[:400] or f"exit={proc.returncode}"
    count = sum(1 for l in out.splitlines() if l.strip().startswith("[PASS]"))
    return True, f"{count} 项通过" if count else "通过"


_LOCK = None


def acquire_lock() -> bool:
    """同一时刻只允许一个 verify_all：固件自检共享同一棵工作树，并行跑会互相还原。

    用文件锁而不是 pid 文件：进程被杀也不会留下假的占用者（锁随句柄关闭自动释放）。
    """
    global _LOCK
    path = Path(scratch_dir()) / "rm_verify_all.lock"
    try:
        handle = open(path, "a+", encoding="utf-8")
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _LOCK = handle
    return True


def main() -> int:
    py = sys.executable
    firmware = None
    if "--firmware" in sys.argv:
        i = sys.argv.index("--firmware")
        if i + 1 < len(sys.argv):
            firmware = sys.argv[i + 1]

    failures: list[str] = []
    total_checks = 0

    if not acquire_lock():
        print("[NO-GO] 已经有一个 verify_all 在跑：固件自检共享同一棵工作树，\n"
              "        并行会互相 git checkout，结果不可信。等它跑完再来。",
              file=sys.stderr)
        return 2
    print("=" * 66)
    print("客户端侧就绪验证（离线）")
    print("=" * 66)

    print("\n[Python 测试套件]")
    for name in PYTHON_SUITES:
        path = ROOT / "tests" / f"{name}.py"
        if not path.exists():
            print(f"  [SKIP] {name:24} 文件不存在")
            continue
        ok, detail = run(name, [py, "-u", str(path)])
        total_checks += 1
        print(f"  [{'OK' if ok else 'XX'}] {name:24} {detail}")
        if not ok:
            failures.append(f"Python: {name} -> {detail}")

    print("\n[视觉红线]")
    # 第 85 轮补：**这条门本来根本不被任何东西跑到。**
    #
    # 红队审计（子代理）实测：`Select-String tools\verify_all.py -Pattern
    # 'visual_freeze|freeze_guard'` 命中 **0 次**。本文件注册了 64 个套件，
    # **红线门不在其中** —— 也就是说，一次改掉所有冻结文件的提交，
    # 只要没人手动跑 `--check`，**就能通过本项目自己的全部门禁**。
    #
    # 它必须**在这里**跑，而且要**跑在最前面**：红线被碰了就该立刻停，
    # 而不是把 64 个套件跑完再报告。
    freeze = ROOT / "tools" / "visual_freeze_guard.py"
    ok, detail = run("visual_freeze", [py, str(freeze), "--check"])
    total_checks += 1
    print(f"  [{'OK' if ok else 'XX'}] {'视觉红线（400 个受保护文件）':38} {detail}")
    if not ok:
        failures.append(f"视觉红线: {detail}")

    print("\n[固件补丁编译与行为校验]")
    for rel, label in C_CHECKS:
        path = ROOT / rel
        if not path.exists():
            print(f"  [SKIP] {label} 文件不存在")
            continue
        ok, detail = run(label, [py, "-u", str(path)])
        total_checks += 1
        print(f"  [{'OK' if ok else 'XX'}] {label:38} {detail}")
        if not ok:
            failures.append(f"C: {label} -> {detail}")

    print("\n[前后端对齐审计]")
    for rel, label, extra in AUDIT_CHECKS:
        path = ROOT / rel
        if not path.exists():
            print(f"  [SKIP] {label} 文件不存在")
            continue
        ok, detail = run(label, [py, "-u", str(path), *extra])
        total_checks += 1
        print(f"  [{'OK' if ok else 'XX'}] {label:38} {detail}")
        if not ok:
            failures.append(f"审计: {label} -> {detail}")

    if firmware:
        print("\n[固件工程准备自检]")
        path = ROOT / "tools" / "firmware" / "prepare_hero_build.py"
        ok, detail = run("prepare", [py, "-u", str(path), firmware])
        total_checks += 1
        print(f"  [{'OK' if ok else 'XX'}] 英雄工程准备（切兵种+套补丁+9 项自检）")
        if not ok:
            failures.append(f"固件准备 -> {detail}")
        # 准备脚本会留下已套补丁的工作树，这里只报告不擅自还原别人的仓库

    print("\n" + "=" * 66)
    if failures:
        print(f"结论：NO-GO —— {len(failures)} / {total_checks} 项未通过")
        for line in failures:
            print(f"  - {line[:180]}")
        print("\n先解决上面这些，再去 Keil 编译。")
        return 1

    print(f"结论：GO —— 客户端侧 {total_checks} 组检查全部通过")
    print("""
下一步（需要你做，客户端无法代验）：
  1. 提交这些文件，否则发布包里根本没有队内链路
  2. python tools/firmware/prepare_hero_build.py <固件路径>   -> 看到 GO
  3. Keil 编译烧录（工程是"警告即错误"）
  4. 确认 __RC_TYPE 与车上装的遥控器是同一家（默认 --rc dt7 = DT7 + DR16）。
     注意别夸大：DT7 的 18 字节帧按帧长分派、跟这个宏无关，键鼠开车本来就能用；
     配错真正致命的是**车上是 25 字节遥控器而宏选错那一家**（整帧不解析、车不动）。
     详见 tools/firmware/host_bridge/RC_LINKS.md 第 2 节。
  5. 上电后把遥控器三位拨杆 s[0] 拨到【中间】
     停在下方时固件走 CHASSIS_LOCK，不读驾驶键位图，车绝对不会动。
     上方也能开车，但那是固件**无条件强制常转**（chassis_mecanum.c:102），
     与 spin_flag 无关——此时客户端的小陀螺开关看上去"没反应"，
     排查时极易误判成补丁没打上。**要验小陀螺就必须拨中间。**
  6. 客户端：开 RobotLink 模块 + Allow Drive + Client Chassis Drive
     并把 Drive Channel 设为 virtual_rc

注意：以上全部是离线验证。它证明不了"车真的会动"。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
