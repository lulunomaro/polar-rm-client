#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 界面上那个"Run DT7 Link Selftest"按钮的可测部分：判据、两个把手、以及"按钮不许是空壳"。
from __future__ import annotations

import sys
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import dt7_dbus_frame as df                        # noqa: E402
from rm_client import control as C                 # noqa: E402
from rm_client.control import ChassisCommand, KeyboardControlBridge  # noqa: E402

FAILED = 0
SKIPPED = []


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    FAILED += 0 if ok else 1


def cmd(keys=0, seq=1):
    return ChassisCommand(seq=seq, timestamp=0.0, vx=0.0, vy=0.0, wz=0.0,
                          spin_mode=False, speed_limit=0, power_limit=0,
                          mock_output=False, keyboard_value=keys)


class Spy:
    def __init__(self):
        self.calls = []
        self.released = 0

    def send(self, **kw):
        self.calls.append(kw)
        self.sent = len(self.calls)
        return True, "ok"

    def release(self, now=None):
        self.released += 1
        return True, "ok"

    def close(self):
        pass


def dispatched_config_calls(action_name):
    """Exercise real parsing and both adapters with inert diagnostic endpoints."""
    from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin

    class DispatchProbe(ConfigActionsMixin):
        def __init__(self):
            self.calls = []

        def handle_student_config_action(self, name):
            return False

        def handle_calibration_action(self, name):
            self.calls.append(("calibration", name))

        def run_dt7_link_selftest(self):
            self.calls.append("dt7")

        def rescan_serial_ports(self):
            self.calls.append("rescan")

        def run_radio_km_selftest(self):
            self.calls.append("radio")

    probe = DispatchProbe()
    probe.handle_config_action(action_name)
    return probe.calls


def main() -> int:
    print("\n--- 1) C 路自检判据（纯函数）---")
    x = int(1 << C.KEYBOARD_BITS["X"])
    check("探针是 X 键、位序按厂商表在 bit12", x == 4096 and df.KEY_VENDOR_2026_BITS["X"] == x,
          str(x))
    fw = Path(os.environ.get("POLAR_FIRMWARE_ROOT") or str(ROOT.parent / "tmp_polarbear" / "StandardRobotpp")).resolve()
    application = fw / "application"
    sources = sorted(p for p in application.rglob("*.c") if p.is_file()) if application.is_dir() else []
    if sources:
        hits = [p.relative_to(fw) for p in sources
                if "KEY_X" in p.read_text(encoding="utf-8", errors="ignore")]
        check("探针选的键在固件里真的没有消费者（选错键自检就会把车开走）",
              not hits, str(hits[:3]))
    else:
        SKIPPED.append("缺少固件 application C 源文件：X 键无消费者的交叉核对未跑")
        print("[SKIP] 缺少固件 application C 源文件：交叉核对未运行")
    v, d = C.evaluate_dt7_selftest([0, 0, x, x], x, 0)
    check("出现目标键位 -> PASS", v == "PASS", f"{v} {d}")
    v, _ = C.evaluate_dt7_selftest([0, 8, 12], x, 0)
    check("键位动了但不是我们发的位 -> PARTIAL（载荷错位，不许判通）", v == "PARTIAL", v)
    v, _ = C.evaluate_dt7_selftest([0, 0, 0], x, 0)
    check("什么都没变 -> NO-CHANGE", v == "NO-CHANGE", v)
    v, _ = C.evaluate_dt7_selftest([], x, 0)
    check("没有回读 -> NO-READBACK（判定不成立，不许算通过）", v == "NO-READBACK", v)
    v, _ = C.evaluate_dt7_selftest([x, x], x, x)
    check("基线里本来就有这些位时不许判 PASS（假阳性防线）", v != "PASS", v)

    v, d = C.evaluate_dt7_selftest([], x, 0, frames_sent=0)
    check("自己一帧都没发 -> 必须先判 NOT-SENT（不许说成 DT7 没理这一帧）",
          v == "NOT-SENT" and "闸门" in d, f"{v} {d}")
    v, _ = C.evaluate_dt7_selftest([x, x], x, 0, frames_sent=0)
    check("发了 0 帧时哪怕看起来像 PASS 也不算通（防止按钮自己骗自己）",
          v == "NOT-SENT", v)
    v, _ = C.evaluate_dt7_selftest([], x, 0)
    check("没给 frames_sent 时保持原判据（不臆造结论）", v == "NO-READBACK", v)
    idle = KeyboardControlBridge(publisher=None, mock_output=False)
    idle.dt7_link = Spy()
    idle.dt7_enabled = True
    calls = []
    idle.send_from_state = lambda *a, **k: calls.append(1)
    idle.tick(None, None, now=10.0)
    n_idle = len(calls)
    idle.dt7_forced_keys = x
    idle.last_send_at = 0.0
    idle.tick(None, None, now=90.0)
    check("按住探针键位期间算活跃：空闲不授权也该持续发（否则自检测的是空气）",
          len(calls) > n_idle, f"{n_idle} -> {len(calls)}")
    idle.dt7_forced_keys = None
    idle.dt7_send_paused = True
    n2 = len(calls)
    idle.last_send_at = 0.0
    br0 = idle
    br0.send_command(cmd(keys=1))
    check("暂停把手生效：一帧都不写，也不补松手",
          len(idle.dt7_link.calls) == 0 and idle.dt7_link.released == 0,
          f"{len(idle.dt7_link.calls)} {idle.dt7_link.released}")
    del br0

    # 无线时点自转"没反应"是必然会被问到的事：原因必须能程序化拿到
    from rm_client.backend.manager import spin_block_reason as sbr
    blocked = sbr(allow_spin=True, link_active=False, estop=False, module_on=True)
    check("0x5A 不在线时说出原因与三条办法",
          "0x04" in blocked and "tcp" in blocked and "SHIFT" in blocked, blocked[:60])
    check("链路在线时不许弹这个提示",
          sbr(allow_spin=True, link_active=True, estop=False, module_on=True) == "")
    check("没要求自转时不许弹（别把没做的事说成问题）",
          sbr(allow_spin=False, link_active=False, estop=False, module_on=True) == "")
    check("急停时也不弹（那是另一件事，已有急停显示）",
          sbr(allow_spin=True, link_active=False, estop=True, module_on=True) == "")
    check("SpinMode 模块关着时不弹",
          sbr(allow_spin=True, link_active=False, estop=False, module_on=False) == "")
    no_fb = sbr(allow_spin=True, link_active=False, estop=False, module_on=True,
                    reported=None)
    got_fb = sbr(allow_spin=True, link_active=False, estop=False, module_on=True,
                   reported=0)
    check("车上从没回读过 0x1F 时，要说清楚是 host_bridge 桥没打",
          "未打 host_bridge 桥" in no_fb and "无法确认" in no_fb, no_fb[-48:])
    check("有回读但链路断了时，理由要引用那个回读值（两种情形不许同一句话）",
          "回读是 0" in got_fb and got_fb != no_fb, got_fb[-48:])

    print("\n--- 2) 粘键判据：超时清零的形态不许被误判成粘键 ---")
    stuck = [x] * 40
    v, _ = C.evaluate_dt7_hold(stuck, x)
    check("停发后稳态仍停在 X -> STUCK", v == "STUCK", v)
    fading = [x] * 30 + [0] * 10          # 前一段还停着、后面自己掉：这是安全侧
    v, _ = C.evaluate_dt7_hold(fading, x)
    check("前一段仍停着但最后掉回 -> 必须判 EXPIRES（判据看稳态）", v == "EXPIRES", v)
    v, _ = C.evaluate_dt7_hold([], x)
    check("没样本 -> NO-READBACK", v == "NO-READBACK", v)

    print("\n--- 3) 两个把手真的作用在发送路径上 ---")
    br = KeyboardControlBridge(publisher=None, mock_output=True)
    spy = Spy()
    br.dt7_link = spy
    br.dt7_enabled = True
    # KEYBOARD_BITS 存的是**位序号**（W=0），要的是位图值：1 << 位序
    br.send_command(cmd(keys=1 << C.KEYBOARD_BITS["W"]))
    check("平时发的是键盘当前值", spy.calls and spy.calls[-1]["pressed_bits"] == 1, str(spy.calls[-1:]))
    br.dt7_forced_keys = x
    br.send_command(cmd(keys=1))
    check("强制键位时发的是探针位 X（覆盖键盘当前值）",
          spy.calls[-1]["pressed_bits"] == x, str(spy.calls[-1]))
    n = len(spy.calls)
    br.dt7_send_paused = True
    br.send_command(cmd(keys=1))
    check("暂停阶段一帧都不写", len(spy.calls) == n, f"{len(spy.calls)} vs {n}")
    check("暂停阶段也不许补松手帧（否则量的就不是进程被杀）", spy.released == 0, str(spy.released))
    br.dt7_send_paused = False
    br.dt7_forced_keys = None
    br.send_command(cmd(keys=5))
    check("把手复位后恢复正常", spy.calls[-1]["pressed_bits"] == 5, str(spy.calls[-1]))

    print("\n--- 4) 按钮不许是空壳：设置项、分派、处理函数三处必须成对存在 ---")
    catalog = (ROOT / "rm_client/module_catalog.py").read_text(encoding="utf-8")
    mix = (ROOT / "rm_client/ui/polar/mixins/config_actions_mixin.py").read_text(encoding="utf-8")
    check("RobotLink 设置项里有这个按钮", catalog.count('"Run DT7 Link Selftest"') == 1)
    check("同名按钮经真实解析与分派恰好调用 DT7 处理函数一次",
          dispatched_config_calls("Run DT7 Link Selftest") == ["dt7"])
    check("近似名字不误入 DT7 处理函数",
          all(dispatched_config_calls(name) == [] for name in
              ("Run DT7 Link Selftest ", "run dt7 link selftest", "Run DT7 Link")))
    check("其他诊断按钮不误入 DT7 处理函数",
          dispatched_config_calls("Rescan Serial Ports") == ["rescan"]
          and dispatched_config_calls("Run Wireless KM Selftest") == ["radio"])
    check("处理函数存在", "def run_dt7_link_selftest(self) -> None:" in mix)
    check("按钮自己不开新串口（同口互抢是最难查的故障）",
          "import serial" not in mix and "Serial(" not in mix)
    check("按钮不阻塞渲染线程（用 QTimer，不用 sleep）",
          "time.sleep" not in mix.split("def run_dt7_link_selftest")[1].split("def start_kmc")[0])
    check("探针键位走的是 control 的两个把手，不是绕过去直写",
          "dt7_forced_keys = PROBE_BITS" in mix and "dt7_send_paused = phase == \"hold\"" in mix)
    rep = C.build_dt7_selftest_report(keys=["X"], forced_bits=x, baseline=0,
                                      verdict="PASS", detail="d", hold_verdict="EXPIRES",
                                      hold_detail="h", samples=10, tail_samples=8)
    check("报告字段齐且 passed/safe 是算出来的",
          rep["passed"] is True and rep["safe_when_dying"] is True
          and rep["sent_bits"] == x and rep["tool"].endswith("(ui)"), str(rep)[:70])
    print("\n" + ("有失败项" if FAILED else "C 路界面自检全部通过"))
    if SKIPPED:
        print("跳过项（别当已验）：" + "；".join(SKIPPED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
