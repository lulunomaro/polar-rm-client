"""Declarative default modules and deterministic, owned registry assembly.

All environment-dependent candidates arrive in CatalogChoices. Importing or
building this catalog never discovers a device, selects a protocol, loads UI
code, or creates a worker. Keep the catalog declarations together: their length
is data volume, not a reason to split configuration identities across files.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Iterable

from .crosshair_profile import FIELDS as CROSSHAIR_FIELDS
from .module_settings import (
    CONTROL_BIND_OPTIONS, CONTROL_SLOT_ORDER, HUD_MODES, LEGACY_GUI_MODES,
    MODULE_LIST_STYLES, TACTICAL_MAP_STATUS_DISPLAYS, Module, ModuleRegistry,
    Setting, action_group_setting, action_setting, bool_setting, mode_setting,
    number_setting,
)


@dataclass(frozen=True)
class CatalogChoices:
    """Detached candidate snapshots; defaults remain declared in the catalog."""
    scenario_modes: tuple[str, ...]
    match_modes: tuple[str, ...]
    robot_roles: tuple[str, ...]
    referee_profile: str
    referee_profiles: tuple[str, ...]
    angle_rx_ports: tuple[str, ...]
    angle_tx_ports: tuple[str, ...]
    robot_ports: tuple[str, ...]
    dt7_ports: tuple[str, ...]
    cc_ports: tuple[str, ...]


def _copy_module(module: Module) -> Module:
    """A registry owns its settings and hook; caller prototypes stay untouched."""
    settings = [replace(setting, value=deepcopy(setting.value),
                        options=deepcopy(setting.options)) for setting in module.settings]
    return replace(module, settings=settings)


def _populate_function_bar(modules: list[Module]) -> None:
    function_bar = next((module for module in modules if module.name == "MinecraftCombatHUD"), None)
    if function_bar is None:
        return
    choices = ["Empty", *(module.name for module in modules if module.name != "MinecraftCombatHUD")]
    for setting in function_bar.settings:
        if setting.name.startswith("Slot "):
            setting.options = list(choices)


def assemble_registry(modules: Iterable[Module], extensions: Iterable[Module] = ()) -> ModuleRegistry:
    """Copy declarative modules, derive complete choices, then bind one registry."""
    owned = [_copy_module(module) for module in (*modules, *extensions)]
    _populate_function_bar(owned)
    return ModuleRegistry(owned)


def default_modules(choices: CatalogChoices) -> list[Module]:
    """Declare prototypes; assemble_registry gives their mutable values one owner."""
    modules = [
        Module(
            "Scenario",
            "System",
            "None",
            [
                mode_setting("Mode", "LabDebug", list(choices.scenario_modes)),
                bool_setting("Show Startup", True),
                mode_setting("Startup Style", "LiquidMenu", ["LiquidMenu", "PolarLauncher"]),
                bool_setting("Mock Writes", True, "默认禁用真实串口和机器人输出。"),
                bool_setting("Official Limits", False, "应用官方频率和载荷限制。"),
            ],
            enabled=True,
            description="启动场景和连接配置选择器。",
        ),
        Module(
            "StartupVisual",
            "System",
            "None",
            [
                mode_setting("Background Mode", "VideoParallax", ["PkgScene", "VideoParallax", "ParticleGrid", "Static"]),
                mode_setting("Wallpaper Profile", "BlackHole", ["BlackHole", "TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]),
                mode_setting("Motion Quality", "Smooth60", ["Smooth60", "Balanced30", "Source"]),
                mode_setting("Glass Style", "LiquidGlass", ["LiquidGlass", "Legacy"]),
                number_setting("Glass Strength", 1.00, 0.40, 1.40, 0.05),
                number_setting("Parallax Strength", 22, 0, 60, 1, "px"),
                number_setting("Background Zoom", 1.00, 1.0, 1.35, 0.01),
                number_setting("Dim Overlay", 0.28, 0.0, 0.8, 0.01),
                action_setting("Import Wallpaper", "导入 MP4/GIF/图片壁纸到客户端壁纸目录。"),
                action_setting("Reset Wallpaper", "恢复默认黑洞壁纸。"),
            ],
            enabled=True,
            description="启动页背景、pkg 壁纸、MP4 导入、视差和暗角管理。",
        ),
        Module(
            "MatchMode",
            "System",
            "None",
            [
                mode_setting("Mode", "RMUC7v7", list(choices.match_modes)),
                mode_setting("Alliance", "Red", ["Red", "Blue"]),
                bool_setting("Second Step", True, "场景选择后显示 RMUC/RMUL 二级选择页。"),
            ],
            enabled=True,
            description="比赛制式选择器，用于切换官方地图、HUD 默认值和协议状态映射。",
        ),
        Module(
            "RobotProfile",
            "System",
            "None",
            [
                mode_setting("Role", "Infantry1", list(choices.robot_roles)),
                number_setting("Level", 1, 1, 10, 1),
                mode_setting("Hero Type", "Melee", ["Melee", "Range"]),
                mode_setting("Infantry Chassis", "Power", ["Power", "Health"]),
                mode_setting("Infantry Shooter", "Burst", ["Burst", "Cooling"]),
                mode_setting("Sentinel Control", "Automatic", ["Automatic", "SemiAuto"]),
            ],
            enabled=True,
            description="按官方规则选择机器人角色、等级与性能分支，并驱动血量、热量、冷却和功率上限。",
        ),
        Module(
            "AllianceProfile",
            "System",
            "None",
            [
                mode_setting("Side", "Red", ["Red", "Blue"]),
                bool_setting("Mirror Tactical Map", True),
            ],
            enabled=True,
            description="本机阵营档案。统一驱动战术地图、雷达、ESP 与自瞄的红蓝敌我关系。",
        ),
        Module(
            "ConfigManager",
            "System",
            "None",
            [
                mode_setting("Profile", "default", ["default"], "configs/ 目录中的客户端配置文件。"),
                bool_setting("Auto Save", True),
                bool_setting("Load On Start", True),
                action_setting("Save Current", "保存当前模块、按键绑定、GUI、HUD 和布局。"),
                action_setting("Reload Profile", "重新读取当前选择的配置文件。"),
                action_setting("Import Config", "导入外部 .json 配置到 configs/。"),
            ],
            enabled=True,
            description="本地客户端配置保存、加载和导入管理器。",
        ),
        Module(
            "EmergencyStop",
            "System",
            "F12",
            [
                action_setting("Trigger E-Stop", "立即发送全零键鼠帧并锁止全部上行。"),
                action_setting("Release E-Stop", "仅在确认机器人安全后解除；快捷键 Ctrl+Shift+F12。"),
            ],
            enabled=True,
            description="赛事控制急停：F12 触发，Ctrl+Shift+F12 解除。",
        ),
        Module(
            "MatchReadiness",
            "System",
            "None",
            [
                action_group_setting(
                    "Qualification Actions",
                    [
                        "New Qualification Session",
                        "Reset Qualification Session",
                        "Run Preflight",
                        "Run Video Test",
                        "Run KMC Test",
                        "Record DT7 Safety Test",
                        "Run Economy Send Test",
                        "Record E-Stop Test",
                        "Observe Firmware Zero",
                        "Record Firmware Zero Test",
                        "Run Loss Recovery Test",
                        "Record Hero Test",
                        "Record Economy Test",
                        "Review PRM1 Vision Schema",
                        "Run PRM1 Vision Bench",
                        "Review PRM1 Spin Schema",
                        "Run PRM1 Spin Bench",
                        "Start Match Soak",
                        "Stop Match Soak",
                        "Export Evidence",
                        "Field Sign-off",
                        "Arm Match Control",
                        "Lock Match Control",
                    ],
                    "现场资格证据与双人签字；没有真车、裁判与正式图传证据时保持 NOT READY。",
                ),
            ],
            enabled=True,
            description="Fail-closed 现场资格、证据导出和比赛控制硬闸门。",
        ),
        Module(
            "ArmorAutoAim",
            "Combat",
            "V",
            [
                bool_setting("Mock Output", True, "云台输出保持在模拟模式。"),
                number_setting("Fov", 85, 10, 180, 1, "deg"),
                number_setting("Smooth", 0.28, 0.01, 1.0, 0.01),
                mode_setting("Priority", "Armor", ["Armor", "Distance", "Health", "Radar"]),
                mode_setting("Lock Style", "White Outline", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring", "Heli Gimbal"]),
                bool_setting("Draw Fov", True, "绘制中心荧光白自瞄圆（仅圆内可锁定）。"),
                number_setting(
                    "Circle Size",
                    160,
                    32,
                    480,
                    2,
                    "px",
                    "自瞄锁定半径（逻辑像素）。只有圆心距落在此圆内的装甲才会被锁定；全屏 3D ESP 仍照常绘制。",
                ),
            ],
            enabled=True,
            description="装甲板自瞄 UI。荧光白圆限制锁定范围；3D ESP 全屏可见。",
        ),
        Module(
            "EliminationFX",
            "Render",
            "None",
            [
                mode_setting("Preset", "Default Scrolling", [
                    "Default Scrolling", "Combo", "Card", "CS2 Native", "Battlefield 1", "Minimal", "PUBG Subtitle",
                    "Battlefield 5", "Battlefield 6", "Total Battlefield", "Valorant Prime", "Valorant Glitchpop",
                    "Valorant Singularity V1", "Valorant Singularity V2", "Valorant Singularity V3",
                    "Valorant Gaia", "Valorant Gaia V1", "Valorant Gaia V2", "Valorant Gaia V3",
                    "Valorant Bubblegum", "Valorant Bubblegum V1", "Valorant Bubblegum V2", "Valorant Bubblegum V3",
                    "Valorant Champions 2021", "Valorant Prelude V1", "Valorant Prelude V2", "Valorant Prelude V3",
                    "Valorant Primordium", "Valorant Primordium V1", "Valorant Primordium V2", "Valorant Primordium V3",
                    "Valorant Radiant Crisis 001", "Valorant RGX", "Valorant RGX V1", "Valorant RGX V2", "Valorant RGX V3",
                ]),
                mode_setting("Element Visibility", "Preset", ["Preset", "Full HUD", "Icons Only"]),
                bool_setting("Kill Feed", True, "击杀信息流（ELIMINATED x N）。"),
                bool_setting("Score Popup", True, "击杀 +分数 弹字。"),
                bool_setting("Bonus Tags", True, "加分项标签行（gd656 bonus_list）。"),
                bool_setting("Lock Target Bonus", True, "锁定目标加分：持续交战同一目标后的击杀追加“锁定目标”标签与提示音（gd656 LOCKED_TARGET）。"),
                bool_setting("Combo Subtitle", True, "连杀字幕（N KILL COMBO）。"),
                bool_setting("Scrolling Icons", True, "滚动击杀图标条（默认/BF5/BF6 通用）。"),
                bool_setting("Combo Icons", True, "CF 连杀图标。"),
                bool_setting("Card Icons", True, "CS2 卡牌图标与卡池条。"),
                bool_setting("Battlefield Icons", True, "战地 1 大图标。"),
                bool_setting("Valorant Icons", True, "无畏契约系图标。"),
                bool_setting("Honor Badges", True, "荣誉徽章：特殊战况（爆头/远程/连杀/载具等）点亮 gd656 honor 图标。"),
                bool_setting("Hit Info", True, "命中信息：受击伤害弹字 + 命中音效（gd656 hit_info）。"),
                bool_setting("Match Progress", True, "顶部基地推进条：双方基地血量消耗进度与红蓝比分。"),
                mode_setting("Team Theme", "Alliance", ["Alliance", "CT Blue", "T Gold"]),
                bool_setting("Sound Enabled", True),
                number_setting("Sound Volume", 55, 0, 100, 1, "%"),
                number_setting("Scale", 1.0, 0.45, 2.0, 0.05),
                mode_setting("Responsive Scale", "Auto", ["Auto", "Fixed"]),
                mode_setting("Anchor Policy", "Combat Focus", ["Combat Focus", "Original Bottom", "Manual"]),
                number_setting("X Offset", 0, -800, 800, 1, "px"),
                number_setting("Y Offset", 0, -500, 500, 1, "px"),
                number_setting("Combo Window", 5.0, 1.0, 12.0, 0.5, "s"),
                mode_setting("Evidence Mode", "Auto", ["Auto", "Referee Only", "Vision Only"]),
                mode_setting("Preview Sequence", "Normal 1-6", ["Normal 1-6", "All Kill Types", "Single"]),
                action_setting("Preview FX", "按当前官方预设播放完整预览序列。"),
                action_setting("Test Elimination", "注入一条本地测试击杀事件。"),
            ],
            enabled=True,
            description="GD656 击杀图标、音效与官方/视觉双轨战果显示。",
        ),
        Module(
            "AudioFeedback",
            "System",
            "None",
            [
                bool_setting("Enabled", True, "客户端交互音效总开关。"),
                bool_setting("Module Toggle Sounds", True, "点击或按键切换模块时播放反馈音。"),
                mode_setting("Sound Theme", "Polar UI", ["Custom", "Polar UI", "Polar Classic", "LiquidBounce"]),
                mode_setting("Custom Enable Sound", "No Sound", ["No Sound"], "自定义主题的模块开启音效。"),
                mode_setting("Custom Disable Sound", "No Sound", ["No Sound"], "自定义主题的模块关闭音效。"),
                mode_setting("Custom Invalid Sound", "No Sound", ["No Sound"], "自定义主题的无效操作提示音。"),
                number_setting("UI Volume", 45, 0, 100, 1, "%"),
                action_setting("Import Enable Sound", "导入模块开启音效。"),
                action_setting("Import Disable Sound", "导入模块关闭音效。"),
                action_setting("Import Invalid Sound", "导入无效操作音效。"),
                action_setting("Preview Enable", "试听模块开启音效。"),
                action_setting("Preview Disable", "试听模块关闭音效。"),
                action_setting("Preview Invalid", "试听无效操作提示音。"),
            ],
            enabled=True,
            description="统一管理模块切换和界面交互音效。",
        ),
        Module(
            "EnergyAutoAim",
            "Combat",
            "G",
            [
                bool_setting("Mock Output", True, "能量机关输出保持在模拟模式。"),
                mode_setting("Rune Type", "Auto", ["Auto", "Small", "Large"]),
                mode_setting("Predict", "Adaptive", ["Off", "Fixed", "Adaptive"]),
                number_setting("Lead Time", 0.18, 0.0, 0.6, 0.01, "s"),
                number_setting("Fov", 58, 10, 140, 1, "deg"),
                mode_setting("Lock Style", "Rune Ring", ["Polar Corner", "White Outline", "Minimal Bracket", "Rune Ring", "Heli Gimbal"]),
                bool_setting("Show Progress", True),
            ],
            description="7v7 能量机关自瞄 UI，并预留打符预测接口。",
        ),
        Module(
            "ArmorLock",
            "Combat",
            "B",
            [
                mode_setting("Lock Point", "Center", ["Center", "Top", "Predict"]),
                number_setting("Predict", 0.12, 0.0, 0.5, 0.01, "s"),
                bool_setting("Heat Guard", True),
            ],
            description="装甲板目标选择和锁定策略预留模块。",
        ),
        Module(
            "HeroLob",
            "Combat",
            "K",
            [
                mode_setting("Mode", "Radar Assisted", ["Radar Assisted", "Manual"]),
                mode_setting("Target", "Enemy Base", ["Enemy Base", "Enemy Outpost", "Manual"]),
                number_setting("Distance", 18.0, 1.0, 60.0, 0.1, "m"),
                number_setting("Height Delta", 0.0, -12.0, 12.0, 0.05, "m"),
                number_setting("Bearing", 0.0, -180.0, 180.0, 0.1, "deg"),
                mode_setting("Arc", "High", ["High", "Low"]),
                number_setting("Muzzle Speed", 16.0, 5.0, 35.0, 0.1, "m/s"),
                number_setting("Gravity", 9.80665, 8.0, 11.0, 0.001, "m/s2"),
                number_setting("Yaw Offset", 0.0, -20.0, 20.0, 0.05, "deg"),
                number_setting("Pitch Offset", 0.0, -20.0, 20.0, 0.05, "deg"),
                number_setting("Muzzle Length", 0.35, 0.0, 1.0, 0.01, "m"),
                number_setting("Yaw Calibration Tolerance", 1.0, 0.1, 5.0, 0.1, "deg"),
                number_setting("Pitch Calibration Tolerance", 1.5, 0.1, 5.0, 0.1, "deg"),
                number_setting("Max Roll", 8.0, 1.0, 20.0, 0.5, "deg"),
                bool_setting("Require Fresh Telemetry", True),
                number_setting("Telemetry Timeout", 0.50, 0.10, 2.00, 0.05, "s"),
                number_setting("Heat Guard", 0.88, 0.40, 1.00, 0.01),
                bool_setting("Mock Output", True, "Decision support only; this module never fires automatically."),
                action_setting("Calibrate Lob Yaw", "Align yaw against a known field line."),
                action_setting("Calibrate Lob Pitch", "Store the operator-confirmed iterative pitch calibration."),
                action_setting("Confirm Relocalization", "Confirm radar/map relocalization after every reset."),
                action_setting("Invalidate Relocalization", "Fail closed after a localisation discontinuity."),
                action_setting("Open Deploy Interface", "Open the dedicated deployment/lob operator console."),
                action_setting("Preview Lob", "Calculate yaw, pitch and flight time without sending a command."),
                action_setting("Arm Assisted Lob", "Arm decision support only; no fire command is emitted."),
                action_setting("Disarm Assisted Lob"),
            ],
            description="英雄吊射辅助：雷达定位、偏航/俯仰标定与弹道预览。严格失效保护，绝不自动开火。",
        ),
        Module(
            "FireControlStub",
            "Combat",
            "None",
            [
                bool_setting("Safety Lock", True),
                number_setting("Max Rate", 8, 1, 20, 1, "Hz"),
                mode_setting("Mode", "Manual", ["Manual", "Confirm", "Disabled"]),
            ],
            description="不输出真实发射命令，仅保留接口占位。",
        ),
        Module(
            "SpinMode",
            "Movement",
            "X",
            [
                mode_setting("Direction", "Auto", ["Auto", "CW", "CCW"]),
                number_setting("Spin Speed", 420, 120, 720, 10, "dps"),
                bool_setting("Power Guard", True),
                bool_setting(
                    "Mock Output",
                    True,
                    "关闭后才会通过 MQTT CustomControl 发送 PRM1 kind=2；机器人固件必须实现同一 schema。",
                ),
            ],
            enabled=True,
            description="RM 底盘小陀螺模式；真实输出仅走官方 MQTT CustomControl，默认安全模拟。",
        ),
        Module(
            "AutoSpinOnHit",
            "Movement",
            "None",
            [
                number_setting("Minimum Damage", 1, 1, 500, 1, "HP"),
                number_setting("Duration", 2.5, 0.2, 8.0, 0.1, "s"),
                number_setting("Cooldown", 1.0, 0.0, 10.0, 0.1, "s"),
                bool_setting("Only During Match", True),
                bool_setting("Require Referee Link", True),
                bool_setting("Power Guard", True),
                number_setting("Minimum Buffer", 10, 0, 100, 1, "%"),
                bool_setting("Auto Disable", True),
            ],
            description=(
                "A new RobotInjuryStat 17/42 mm damage delta temporarily "
                "enables SpinMode; cumulative baselines and other damage do not trigger it."
            ),
        ),
        Module(
            "ChassisAssist",
            "Movement",
            "C",
            [
                number_setting("Power Limit", 70, 30, 120, 1, "W"),
                bool_setting("Buffer Protect", True),
                mode_setting("Follow", "Gimbal", ["Gimbal", "Field", "Free"]),
            ],
            description="底盘辅助模块，预留串口和控制输出钩子。",
        ),
        Module(
            "PowerGuard",
            "Movement",
            "None",
            [
                number_setting("Heat Warn", 180, 60, 240, 1),
                number_setting("Energy Warn", 20, 0, 100, 1, "%"),
                bool_setting("Auto Limit", False),
            ],
            description="底盘与发射机构功率、热量保护辅助模块。",
        ),
        Module(
            "CameraFeed",
            "Vision",
            "None",
            [
                mode_setting("Source", "Webcam", ["Webcam", "Shark UDP", "UDP HEVC", "Native 0x0310", "Synthetic"]),
                number_setting("Camera ID", 0, 0, 5, 1),
                number_setting("Capture Width", 960, 320, 1920, 16, "px"),
                number_setting("Capture Height", 540, 240, 1080, 16, "px"),
                bool_setting("Mirror", False),
            ],
            enabled=True,
            description="使用电脑摄像头作为当前机器人图传。",
        ),
        Module(
            "TongjiVisionBridge",
            "Vision",
            "None",
            [
                mode_setting("Vision Source", "Local", ["Local", "Tongji", "Auto", "Mock"], "Local 直接使用同济 YOLO 权重；Tongji 接收 UDP 9871；Auto 优先外部同济。"),
                bool_setting("Listen", True, "接收外部同济检测器发到本机的 UDP JSON。"),
                number_setting("UDP Port", 9871, 1024, 65535, 1),
                number_setting("UDP Timeout", 250, 80, 2000, 10, "ms"),
                mode_setting("Local Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("External Device", "AUTO", ["AUTO", "GPU", "CPU"]),
                mode_setting("Local Model", "Tongji YOLOv5", ["Tongji YOLOv5"]),
                mode_setting("Vision Kill Sensitivity", "Lab Responsive", ["Lab Responsive", "Balanced", "Match Reliable"], "控制视觉灯灭、低亮与丢检击杀的灵敏度；实验室优先联调，官方优先可靠。"),
                bool_setting("Desktop Safe", True),
            ],
            enabled=True,
            description="同济视觉源配置：Local 同帧 YOLO 或外部 UDP rm.vision.tongji.v1。桌面联调只缓存云台建议，不发送控制。",
        ),
        Module(
            "CameraCalibration",
            "Vision",
            "None",
            [
                mode_setting("Profile", "polar_webcam", ["polar_webcam"]),
                number_setting("Board Columns", 9, 4, 14, 1),
                number_setting("Board Rows", 6, 3, 10, 1),
                number_setting("Square Size", 25, 10, 80, 1, "mm"),
                action_setting("Capture Chessboard", "从当前电脑摄像头帧采集一组棋盘格角点。"),
                action_setting("Solve Calibration", "使用已采集的角点计算内参和畸变参数。"),
                action_setting("Export Tongji YAML", "导出同济桥接器使用的桌面相机配置。"),
            ],
            enabled=True,
            description="电脑摄像头棋盘格标定。未标定时只允许检测，不允许可信预测装甲显示。",
        ),
        Module(
            "Armor3DESP",
            "Vision",
            "None",
            [
                mode_setting(
                    "Style",
                    "JelloShadow",
                    ["JelloShadow", "Wireframe3D", "PolarOutline2D", "Prediction Rack"],
                ),
                mode_setting("Renderer", "GPU Auto", ["GPU Auto", "CPU"]),
                bool_setting("Show Prediction", False),
                bool_setting("Show Labels", False),
                bool_setting("Prediction Labels", False),
                bool_setting("Show Depth", True),
                number_setting("Line Width", 2.0, 1.0, 5.0, 0.25, "px"),
                number_setting("Opacity", 0.82, 0.15, 1.0, 0.05),
            ],
            enabled=True,
            description="使用同济 Tracker 的真实装甲四点与多装甲重投影结果渲染预测 ESP。",
        ),
        Module(
            "RobotOverlay",
            "Vision",
            "R",
            [
                bool_setting("Armor Boxes", True),
                bool_setting("Robot ID", True),
                bool_setting("Distance", True),
                mode_setting(
                    "Color",
                    "White",
                    ["White", "Team", "Accent", "Threat"],
                    "装甲盒配色。默认 White = 接线前的实际画面（纯白）；"
                    "Team 按视觉识别到的装甲板颜色分红蓝；Threat 需威胁度数据，"
                    "拿不到时降级回 Team 并在诊断里记原因。",
                ),
                mode_setting("ESP Style", "3D Wire", ["3D Wire", "3D Glass", "Polar Outline", "Flat 2D"]),
                number_setting("3D Depth", 0.22, 0.05, 0.50, 0.01),
                bool_setting("Predict Plates", True),
                number_setting("Armor Count", 4, 2, 4, 1),
                number_setting("Radius M", 0.23, 0.10, 0.45, 0.01, "m"),
                number_setting("Predict Horizon", 0.06, 0.0, 0.30, 0.01, "s"),
                number_setting("Max Targets", 6, 1, 12, 1),
                bool_setting("Only Secondary", True),
            ],
            enabled=True,
            description="将 ESP 视觉职责映射为 RM 机器人/装甲板覆盖层。",
        ),
        Module(
            "RadarOverlay",
            "Vision",
            "None",
            [
                bool_setting("High Light", True),
                number_setting("Fade", 0.65, 0.1, 1.0, 0.05),
                mode_setting(
                    "Map",
                    "Both",
                    ["Both", "Mini", "Full", "Hidden"],
                    "雷达标记画在哪种形态的地图上。默认 Both = 接线前的实际画面"
                    "（缩略与展开态都画）；Mini 只在缩略态画，Full 只在长按展开态画，"
                    "Hidden 都不画。",
                ),
            ],
            description="雷达信息显示，并预留地图点击联动接口。",
        ),
        Module(
            "MapMarker",
            "Strategy",
            "M",
            [
                mode_setting("Mark Type", "Attack", ["Attack", "Defense", "Warning", "Custom"]),
                # 默认 True 是刻意的：接这个开关之前，发送处一直硬写
                # `is_send_all=1`（全队广播）。若沿用 False 作默认，接线会把
                # 广播变成单发——那是比赛可见的行为改变，不该顺带引入。
                # wire 取值 1..4 按选项顺序映射（协议注释确认 3=mode、6=type，
                # 但官方文档未列出各取值语义，需与裁判协议表交叉核对）。
                bool_setting(
                    "Send All",
                    True,
                    "广播给全队。默认开启以匹配接线前的实际发送行为。",
                ),
                number_setting("Repeat", 1, 1, 5, 1),
            ],
            description="MapClickInfo/MapClickCmd 的 UI 预留模块。",
        ),
        Module(
            "TacticalMap",
            "Strategy",
            "Tab",
            [
                mode_setting("Map Size", "Wide", ["Compact", "Wide", "Full Tactical"]),
                mode_setting("Chrome", "Minimal", ["Minimal", "Thin", "Full"]),
                mode_setting("Status Display", "LiquidAuto", TACTICAL_MAP_STATUS_DISPLAYS),
                mode_setting("Palette", "RM Tactical", ["RM Tactical", "Liquid Neutral"]),
                mode_setting("Official Map", "RMUC 7v7 Official Map", ["RMUC 7v7 Official Map", "RMUL 3v3 Official Map"]),
                bool_setting("Mirror Blue", True),
                bool_setting("Robot Dots", True),
                bool_setting("Map Marks", True),
                bool_setting("Radar Pulse", True),
                number_setting("Opacity", 0.72, 0.35, 1.0, 0.05),
            ],
            enabled=True,
            description="基于官方地图的小地图，支持 RM 坐标、雷达高亮和地图点击预留位。",
        ),
        Module(
            "LiquidTacticalMap",
            "Render",
            "None",
            [
                mode_setting("Pointer Style", "Triangle", ["Triangle", "Wedge", "Dot"]),
                mode_setting("Entity Color", "Team", ["Team", "Distance", "Health"]),
                bool_setting("Show Labels", True),
                bool_setting("Show Values", True),
                mode_setting("Dock Animation", "Fly", ["Fly", "Flip", "Fade"]),
            ],
            enabled=True,
            description="官方 RM 小地图的 Liquid 风格视觉皮肤。",
        ),
        Module(
            "SentinelCommand",
            "Strategy",
            "None",
            [
                mode_setting("Posture", "Attack", ["Attack", "Defense", "Move"]),
                bool_setting("Confirm Prompt", True),
            ],
            description="哨兵/守卫控制指令 UI 预留模块。",
        ),
        Module(
            "AirSupport",
            "Strategy",
            "None",
            [
                mode_setting("Command", "Free", ["Free", "Paid", "Abort"]),
                bool_setting("Require Confirm", True),
            ],
            description="空中支援指令接口。",
        ),
        Module(
            "TopMatchBar",
            "Render",
            "None",
            [
                number_setting("Scale", 0.72, 0.35, 1.4, 0.05),
                mode_setting("Match Scope", "RMUC Only", ["RMUC Only", "All Match Modes"]),
                mode_setting("Roster Scope", "Match Roles", ["Match Roles", "Ground Robots", "All Roles"]),
                mode_setting("Score Metric", "Referee Score", ["Unavailable", "Referee Score"]),
                bool_setting("Show Scores", True),
                bool_setting("Show Kills", True),
                bool_setting("Show Health", True),
                bool_setting("BF6 Progress", True),
                bool_setting("Source Motion Preview", False,
                             "原样式0.3s击杀标记缩放预览，尚未完成原版实录逐帧验收。"),
                bool_setting("Local CS2 Assets", True, "使用本机私有CS2图标、纹理与字体；无素材包时回退原创绘制。"),
            ],
            enabled=True,
            description="CS式RM比赛顶部：车辆HP、独立系统状态和原BF6基地进度；缺失遥测保持未知。",
        ),
        Module(
            "MatchTimer",
            "Strategy",
            "None",
            [
                bool_setting("Show Stage", True),
                bool_setting("Show Scores", True),
                bool_setting("Pulse Last 30s", True),
            ],
            enabled=True,
            description="GameStatus 阶段、倒计时、分数和回合信息显示。",
        ),
        Module(
            "BaseOutpostStatus",
            "Strategy",
            "None",
            [
                bool_setting("Own Base", True),
                bool_setting("Enemy Base", True),
                bool_setting("Outposts", True),
                bool_setting("Shield", True),
            ],
            enabled=True,
            description="7v7 GlobalUnitStatus 基地与前哨站血量/护盾显示。",
        ),
        Module(
            "VictoryPointStatus",
            "Strategy",
            "None",
            [
                bool_setting("Victory Points", True),
                bool_setting("Control Zone", True),
                bool_setting("Team Status", True),
            ],
            description="3v3 胜利点和控制区状态显示。",
        ),
        Module(
            "EconomyPanel",
            "Strategy",
            "B",
            [
                bool_setting("Gold", True),
                bool_setting("Tech Level", True),
                bool_setting("Radar Level", True),
                mode_setting("Open Style", "Dynamic Island", ["Dynamic Island", "Full Sheet"]),
                mode_setting("Shop Layout", "CS2 Grid", ["CS2 Grid", "Classic"]),
                bool_setting("CS2 Feedback", True, "CS2购买悬停、确认闪亮与本机原生音效；不改变液态玻璃或交易确认。"),
                bool_setting("Mock Requests", False, "正式赛事默认通过 MQTT 发送；实验室/模拟场景仍强制 Mock。"),
            ],
            enabled=True,
            description="按比赛模式与机器人状态提供弹丸、恢复、复活和支援操作。",
        ),
        Module(
            "ProjectileShop",
            "Strategy",
            "None",
            [
                mode_setting("Ammo Type", "17mm", ["17mm", "42mm"]),
                bool_setting("Mock Request", False, "正式赛事默认通过 MQTT 发送；实验室/模拟场景仍强制 Mock。"),
                bool_setting("Show Cost", True),
            ],
            enabled=True,
            description="直接打开经济工作区弹丸页；正式请求等待裁判状态确认。",
        ),
        Module(
            "MatchChat",
            "Strategy",
            "T",
            [
                bool_setting("Referee Notices", True),
                number_setting("Fade Seconds", 10, 3, 30, 1, "s"),
                number_setting("Closed Lines", 6, 1, 10, 1),
                number_setting("Open Lines", 16, 6, 24, 1),
            ],
            enabled=True,
            description="Minecraft-style local match chat and referee event feed.",
        ),
        Module(
            "QuickActionWheel",
            "System",
            "Q",
            [
                mode_setting("Sectors", "8", ["4", "6", "8"]),
                number_setting("Radius", 118, 72, 180, 2, "px"),
                number_setting("Dead Zone", 38, 18, 96, 2, "px"),
                bool_setting("Show Labels", True),
                mode_setting("Label Style", "Action", ["Action", "Compact", "Off"]),
                mode_setting("Icon Style", "Initials", ["Initials", "Dot", "Off"]),
                mode_setting(
                    "Up",
                    "Economy",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Upper Right",
                    "Ammo",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Right",
                    "Auto Aim",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Lower Right",
                    "Armor Lock",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Down",
                    "Match Chat",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Lower Left",
                    "Spin Mode",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Left",
                    "Friction Wheel",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
                mode_setting(
                    "Upper Left",
                    "ClickGUI",
                    ["Economy", "Ammo", "Auto Aim", "Armor Lock", "Match Chat", "Spin Mode", "Friction Wheel", "ClickGUI"],
                ),
            ],
            enabled=True,
            description="Hold Q, point at an existing client action, then release to execute it.",
        ),
        Module(
            "ThermalPowerPanel",
            "Render",
            "None",
            [
                bool_setting("Heat", True),
                bool_setting("Chassis Power", True),
                bool_setting("Buffer", True),
            ],
            enabled=True,
            description="RobotDynamicStatus 热量、底盘功率、缓冲能量和弹丸数量显示。",
        ),
        Module(
            "BuffTimeline",
            "Render",
            "None",
            [
                bool_setting("Buffs", True),
                bool_setting("Special Mechanism", True),
                bool_setting("Timeline", True),
            ],
            enabled=True,
            description="Buff 和特殊机制状态标签。",
        ),
        Module(
            "MQTTLink",
            "System",
            "None",
            [
                mode_setting("Host", "127.0.0.1", ["127.0.0.1", "192.168.12.1"]),
                number_setting("Port", 3333, 1000, 9999, 1),
                bool_setting("Auto Connect", False),
                mode_setting("Referee Profile", choices.referee_profile, list(choices.referee_profiles)),
            ],
            description="裁判系统 MQTT/protobuf；年度协议显式选择，未知版本禁止回退。",
        ),
        Module(
            "UDPVideo",
            "System",
            "None",
            [
                mode_setting("Host", "127.0.0.1", ["127.0.0.1", "0.0.0.0", "192.168.12.2"]),
                number_setting("Port", 3334, 1000, 9999, 1),
                bool_setting("Reassemble", True),
            ],
            description="SharkDataSever UDP HEVC 分片重组接口。",
        ),
        Module(
            "SerialBridge",
            "System",
            "None",
            [
                mode_setting("Port", "Stub", ["Stub", "COM1", "COM3", "COM5"]),
                number_setting("Baud", 115200, 9600, 921600, 9600),
                bool_setting("Mock Writes", True),
            ],
            description="RS232/串口桥接 stub，用于后续接入真实机器人。",
        ),
        Module(
            "ControllerAngles",
            "System",
            "None",
            [
                mode_setting("Angle RX Port", "Stub", ["Stub"] + [p for p in choices.angle_rx_ports if p not in {"Stub", "Auto"}]),
                mode_setting("Angle TX Port", "Stub", ["Stub"] + [p for p in choices.angle_tx_ports if p not in {"Stub", "Auto"}]),
                bool_setting("Forward Angles", False),
                bool_setting("Angle Mock Writes", True),
                action_setting("Arm Angle Forward", "只授权本次进程。需新鲜六轴数据、RobotLink 和 Allow Drive；断流后必须重新授权。"),
                action_setting("Disarm Angle Forward", "停止角度转发，等待车端独立超时；不发送全零目标角度。"),
                action_setting("Show Angle Status", "显示六轴角度接收有效性、转发状态和拦截原因。"),
                action_setting("Rescan Serial Ports", "重新扫描显式 RX/TX 串口；不会自动选择设备。"),
            ],
            description="工程实体控制器六轴角度：A5/0x0302/30 字节，115200。默认关闭及 mock；300ms 独立超时。",
        ),
        Module(
            "RobotLink",
            "System",
            "None",
            [
                # 选项启动时从系统实际探测，机器人 USB CDC 在车上可能是任意 COM 号；
                # "Auto" 会在启动时自动选中唯一/最像 STM32 的 USB 串口。
                mode_setting("Port", "Auto", list(choices.robot_ports)),
                number_setting("Baud", 115200, 9600, 921600, 9600),
                number_setting("Send Hz", 50, 10, 100, 1),
                # 能力位：由场景写入，表示本环境允许队内链路真写。
                bool_setting("Allow Drive", False),
                # 队内链路的写入开关。**不能**复用场景级标志：场景级 `mock_writes`
                # 的含义是“裁判/官方链路保持 mock”，而 `allow_polar_drive` 的含义是
                # “这个场景能不能自动授权驾驶”。早期用后者反推队内写入开关，
                # 导致大疆官方场景（allow_polar_drive=False）下队内链路被静默 mock，
                # 一帧都发不出去。现在写入权只属于本设置项。
                bool_setting("Mock Writes", False),
                # 允许客户端下发 SHIFT（小陀螺）这类**模式切换**键位。
                # 默认关：小陀螺是防御性底盘模式、不涉及发射，但它会改变底盘
                # 行为，所以仍要操作员显式授权。“受击后小陀螺”走 0x5A 需要开启。
                # 无论本项开关如何，Q/E（摩擦轮）与 F（sc_flag）永不下发。
                bool_setting("Allow Spin Key", False),
                # 无线键鼠出口：键鼠打包成 PRM1 kind=3，经 CustomControl(0x0302)
                # -> 图传链路 -> 机器人 0x0311，由车上 host_km_link 落到键位账本。
                # 默认关：开着要同时满足车上固件带 POLAR-KM-LINK、固件里
                # __CONTROL_LINK_KM 不是 CL_KM_RC，否则只是白占图传带宽。
                # 本项只是"意愿"，真正生效还要 模块启用 + Allow Drive + 非急停
                # （判定在 backend/manager.py 的 RobotLink 应用处）。
                bool_setting("Radio KM Egress", False),
                # 端到端自检：观察 2.5 秒车上回读的 0x0311 到达计数涨不涨。
                # 自检本身不注入任何按键，所以车不会因为这个动作而动。
                # ---- 自定义控制器出口（0x0306 -> 官方选手端 -> DT7）----
                # 依据：《2026 通信协议》表 1-1 "自定义控制器←→选手端 115200"、
                # 1.5 节"操作手可使用自定义控制器模拟键鼠操作选手端"、表 1-43 的
                # 8 字节布局。这条路**不走车上图传串口**，所以 0x0311 那条
                # 量不通时它仍然可用；代价是必须有官方选手端在跑。
                # 直连 DT7（不跑官方选手端也能发键鼠）。帧结构/位域/CRC 已在厂商
                # 二进制里核实，但**命令号与载荷字段顺序仍是候选值**
                # （configs/dt7_profile.json、README 第 20/21 节），
                # 因此默认关，且没现场确认前不许上正式比赛。
                bool_setting("Direct DT7 Egress", False),
                mode_setting("DT7 Port", "Auto", list(choices.dt7_ports)),
                # 串口清单是启动那一刻算出来的，而现场十有八九"先开客户端、后插 DT7"：
                # 那时下拉里只有 Auto，Auto 认不出 DJI 口就宁可拒发（不许乱开别人的口），
                # 于是只剩"重启程序"一条路 —— 场上一抖就是几十秒。给一个手动重扫。
                action_setting(
                    "Rescan Serial Ports",
                    "重新枚举 Port / DT7 Port / CC Port 三个下拉的候选口。"
                    "只换候选、不改当前值；当前值即使暂时不在列表里也会被保住。",
                ),
                # 真发闸：勾了才往 DT7 那个 DJI VCOM 写字节。不勾 = 只产帧不碰硬件。
                bool_setting("DT7 Real TX", False),
                # 开火前置执行器（摩擦轮 Q/E）默认不许经客户端注入，
                # 与 0x0311 侧的 KM_KEY_ALLOWED_MASK 同一条纪律；勾了才发。
                bool_setting("DT7 Allow Fire Keys", False),
                bool_setting("Custom Controller Egress", False),
                mode_setting("CC Port", "Auto", list(choices.cc_ports)),
                number_setting("CC Hz", 30, 5, 30, 1),
                # 默认 mock：没确认选手端挑口规则之前，绝不写真串口。
                bool_setting("CC Mock Writes", True),
                action_setting(
                    "Run DT7 Link Selftest",
                    "一键验证 C 路（PC→DT7→DR16→C 板）通不通 + DT7 会不会粘键。"
                    "刻意用固件**没有任何消费者**的 X 键当探针：链路再通也不会让车动一下。"
                    "判据只认车上合并后的最终键位 km_key（需插着 C 板 USB 0x5A 读回读）。",
                ),
                action_setting(
                    "Run Wireless KM Selftest",
                    "观察 2.5 秒车上回读的 0x0311 到达计数（km_rx）是否增长；"
                    "判据来自车那一侧，不是客户端自证。需插着 USB 0x5A 才能读回读。",
                ),
                # 意图位：由操作员控，两个都要开才会真的发。
                # ---- D 路：车端 USB<->WiFi 桥（不依赖大疆客户端、不动固件）----
                # 默认仍是直插串口；换 tcp 就是把"字节从哪儿来"换掉，
                # 协议层 polar_link 一个字都不动。
                mode_setting("Link Mode", "serial", ["serial", "tcp"]),
                mode_setting("Bridge Host", "", ["", "10.50.0.10", "192.168.12.10"],
                             description="车端 bridge_server 所在小电脑的 IP（Link Mode=tcp 才用）"),
                number_setting("Bridge Port", 50570, 1024, 65535, 1),
                bool_setting("Client Chassis Drive", False),
                bool_setting("Client Gimbal Control", False),
                # 现场约定：视觉 NUC 自瞄优先级最高，默认占用，客户端让位。
                bool_setting("Vision NUC Occupied", True),
                number_setting("Speed Scale %", 100, 10, 100, 5),
                number_setting("Yaw Scale %", 100, 10, 100, 5),
                number_setting("Gimbal Slew", 8, 1, 45, 1),
                number_setting("Mouse Sensitivity", 1.0, 0.05, 10.0, 0.05,
                               description="本客户端鼠标位移倍率；不是车辆NUC的自瞄增益，不改变DT7原始输入。"),
                bool_setting("Invert Mouse Y", False, "反转本客户端鼠标垂直控制方向。"),
                number_setting("Mouse Wheel Scale", 1.0, 0.1, 4.0, 0.1),
                # 驾驶上行通道。virtual_rc 复用固件已生效的 WASD 驾驶
                # （chassis_omni.c 读 rc->key.v 直接产生斜坡速度，自带限幅与
                # 低通），固件只需补一处 VIRTUAL_RC_CTRL → rc_ctrl 接线；
                # robot_cmd 则需新增 speed_vector 消费者。选 both 可平滑过渡。
                mode_setting(
                    "Drive Channel",
                    "robot_cmd",
                    ["robot_cmd", "virtual_rc", "both"],
                ),
                # 驾驶/键鼠协议槽的可自定义绑定。默认等于历史物理键（W/A/S/D…），
                # 改绑后上位机发的仍是同一个协议槽位，因此不影响固件语义，
                # 也不影响“只透传驾驶槽”的安全掩码。
                *[
                    mode_setting(f"Bind {slot}", slot, CONTROL_BIND_OPTIONS)
                    for slot in CONTROL_SLOT_ORDER
                ],
            ],
            description=(
                "队内 StandardRobot++ 0x5A 链路：读取机器人自身遥测驱动 HUD，"
                "并上发云台角度与底盘速度。fire 位始终为 0。"
            ),
        ),
        Module(
            "HUD",
            "Render",
            "H",
            [
                bool_setting("Watermark", True),
                bool_setting("Module List", True),
                bool_setting("Dynamic Island", True),
                bool_setting("Target HUD", True),
                bool_setting("KeyBinds", True),
                bool_setting("Robot List", True),
                bool_setting("Status Cards", True),
                bool_setting("RM Indicators", True),
                mode_setting("Combat Preset", "Polar", ["Polar", "Minecraft 1.21.11"]),
                mode_setting("Density", "CombatCompact", ["CombatCompact", "Balanced", "DebugFull"]),
                mode_setting("Visual Tone", "LiquidClean", ["LiquidClean", "PolarLegacy"]),
                mode_setting("Target Style", "RoundCompact", ["RoundCompact", "BarOnly", "FullCard"]),
                mode_setting("Right Display", "ModuleListHud", HUD_MODES),
                mode_setting("ModuleList Style", "JelloActiveMods", MODULE_LIST_STYLES),
                mode_setting("Jello List Size", "Normal", ["Normal", "Small", "Tiny"]),
                bool_setting("Jello List Animations", True),
                bool_setting("Jello List Sound", True, "Sigma ActiveMods toggle sound."),
                mode_setting("ModuleList Surface", "LiquidGlass", ["Native", "LiquidGlass"]),
                mode_setting("ModuleList Alignment", "Right", ["Right", "Left"]),
                mode_setting("ModuleList Order", "Descending", ["Descending", "Ascending"]),
                mode_setting("Module Tags", "Category", ["Category", "Keybind", "Off"]),
                mode_setting("Module Animation", "FlyFlip", ["FlyFlip", "Slide", "Fade"]),
                action_setting(
                    "Restore Compact HUD",
                    "仅恢复紧凑 HUD、小地图边框和目标面板，不修改模块开关、按键、连接或比赛配置。",
                ),
            ],
            enabled=True,
            description="将 POLAR HUD 职责映射为 RM 状态覆盖层。",
        ),
        Module(
            "UIScale",
            "Render",
            "None",
            [
                bool_setting("Auto Scale", True, "根据当前窗口尺寸缩放 UI。"),
                bool_setting("High Res Optimize", True),
                number_setting("HUD Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("GUI Scale", 1.0, 0.6, 2.5, 0.05),
                number_setting("Animation Speed", 1.0, 0.5, 1.8, 0.05),
                mode_setting("HUD Refresh Rate", "144", ["120", "144", "165", "240"]),
                number_setting("Reference Width", 1280, 960, 3840, 80, "px"),
                number_setting("Reference Height", 720, 540, 2160, 60, "px"),
            ],
            enabled=True,
            description="适配不同显示器尺寸的响应式 UI 缩放控制器。",
        ),
        Module(
            "Crosshair",
            "Render",
            "None",
            [
                mode_setting("Style", "RM Dot", ["RM Dot", "Circle", "Cross", "CS2"], "CS2为静态近似；动态散布、后坐力和开镜效果没有RM遥测，不会模拟。"),
                number_setting("Size", 18, 4, 48, 1),
                number_setting("Crosshair Scale", 1, .5, 8, .1, "x", "整体缩放准星（包括导入的CS2准星）；不改分享码参数或自瞄FOV。"),
                bool_setting("Heat Color", True),
                action_setting("Open Crosshair Editor", "CS式分组参数与多背景实时预览；支持双向分享码/JSON/CFG。"),
                action_setting("Import Crosshair Code", "读取CS/CSGO分享码、准星JSON或白名单CFG，不执行命令。"),
                action_setting("Import Crosshair File", "读取外部准星JSON/CFG/TXT；未知字段拒绝，不静默忽略。"),
                action_setting("Crosshair Import Details", "查看保留的动态/scope参数、原始分享码及限幅说明。"),
                number_setting("CS2 Style", 4, *CROSSHAIR_FIELDS["CS2 Style"], 1),
                number_setting("Length", 5, *CROSSHAIR_FIELDS["Length"], .1),
                number_setting("Gap", 3, *CROSSHAIR_FIELDS["Gap"], .1),
                number_setting("Thickness", 1, *CROSSHAIR_FIELDS["Thickness"], .1),
                number_setting("Reference Height", 1080, *CROSSHAIR_FIELDS["Reference Height"], 1, "px"),
                mode_setting("Geometry Units", "Pixels", ["Pixels", "Legacy"]),
                bool_setting("Center Dot", False),
                bool_setting("T Shape", False),
                number_setting("Red", 80, *CROSSHAIR_FIELDS["Red"], 1),
                number_setting("Green", 255, *CROSSHAIR_FIELDS["Green"], 1),
                number_setting("Blue", 80, *CROSSHAIR_FIELDS["Blue"], 1),
                number_setting("Alpha", 255, *CROSSHAIR_FIELDS["Alpha"], 1),
                number_setting("Color Mode", 5, *CROSSHAIR_FIELDS["Color Mode"], 1),
                bool_setting("Use Alpha", True),
                number_setting("Outline Mode", 1, *CROSSHAIR_FIELDS["Outline Mode"], 1),
                number_setting("Outline Width", 1, *CROSSHAIR_FIELDS["Outline Width"], .5),
                number_setting("Outline Red", 0, *CROSSHAIR_FIELDS["Outline Red"], 1),
                number_setting("Outline Green", 0, *CROSSHAIR_FIELDS["Outline Green"], 1),
                number_setting("Outline Blue", 0, *CROSSHAIR_FIELDS["Outline Blue"], 1),
                number_setting("Outline Alpha", 255, *CROSSHAIR_FIELDS["Outline Alpha"], 1),
                bool_setting("Follow Recoil", False, "仅保留外部参数；无CS后坐力遥测，不驱动RM控制。"),
                number_setting("Dynamic Spread Limit", 0, *CROSSHAIR_FIELDS["Dynamic Spread Limit"], 1),
                number_setting("Split Distance", 7, *CROSSHAIR_FIELDS["Split Distance"], 1),
                number_setting("Inner Split Alpha", 1, *CROSSHAIR_FIELDS["Inner Split Alpha"], .01),
                number_setting("Outer Split Alpha", .5, *CROSSHAIR_FIELDS["Outer Split Alpha"], .01),
                number_setting("Split Size Ratio", .3, *CROSSHAIR_FIELDS["Split Size Ratio"], .01),
                number_setting("Fixed Gap", 3, *CROSSHAIR_FIELDS["Fixed Gap"], .1),
                bool_setting("Weapon Gap", False),
                number_setting("Scope Dot Scale", 1, *CROSSHAIR_FIELDS["Scope Dot Scale"], .01),
                bool_setting("Scope Dot Uses Color", False),
                Setting("Source Format", "text", "Custom"),
                Setting("Imported Code", "text", ""),
                Setting("Import Notes", "text", ""),
            ],
            enabled=True,
        ),
        Module(
            "CombatTargetHUD",
            "Render",
            "None",
            [
                mode_setting("Visual", "Clear", ["Classic", "Clear", "Enhanced"]),
                bool_setting("Show Bars", False),
                bool_setting("Show Status Line", True),
            ],
            enabled=True,
            description="围绕中心准星显示本机血量、射击热量、自瞄状态和弹丸余量。",
        ),
        Module(
            "MinecraftCombatHUD",
            "Render",
            "None",
            [
                mode_setting("GUI Scale", "Auto", ["Auto", "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x"]),
                number_setting(
                    "Scale Multiplier",
                    1.0,
                    0.50,
                    2.00,
                    0.05,
                    description="仅缩放原版 Minecraft HUD 像素层；外挂覆盖层保持独立缩放。",
                ),
                *[
                    mode_setting(f"Slot {index}", default, ["Empty", default])
                    for index, default in enumerate(
                        (
                            "ArmorAutoAim", "ArmorLock", "FrictionWheel", "SpinMode",
                            "EconomyPanel", "ProjectileShop", "TacticalMap", "MatchChat", "ClickGui",
                        ),
                        1,
                    )
                ],
            ],
            enabled=False,
            description="Minecraft 1.21.11 战斗 HUD 与九格自定义功能栏；可从 Jello ClickGUI 拖放模块。",
        ),
        Module(
            "ChassisGimbalIndicator",
            "Render",
            "None",
            [
                mode_setting("Style", "Minimal", ["Minimal", "Tank", "Compass"]),
                bool_setting("Show Angle", True),
                bool_setting("Show Mode", True),
                number_setting("Size", 56, 42, 120, 2, "px"),
                number_setting("Smooth", 0.35, 0.05, 1.0, 0.05),
            ],
            enabled=True,
            description="底盘-云台相对角方向指示器，显示车体方向、炮塔方向和 yaw 差。",
        ),
        Module(
            "ClickGui",
            "Render",
            "RightShift",
            [
                number_setting("Scale", 1.0, 0.5, 2.0, 0.05),
                mode_setting("Language", "简体中文", ["English", "简体中文"], "切换界面显示语言；不改变配置键和协议值。"),
                mode_setting("Render Backend", "Unified GPU", ["Unified GPU", "Compatibility Painter"]),
                bool_setting("Save Layout", True),
                action_setting("Open Keybind Manager", "打开 Jello 完整键盘；按键弹层查看、删除并添加模块快捷键。"),
                action_group_setting(
                    "Old ClickGUIs",
                    LEGACY_GUI_MODES,
                    "显示所有旧 ClickGUI",
                ),
            ],
            description="Jello Sigma 5.1.0 主 ClickGUI 一比一复刻；旧界面收纳在 Old ClickGUIs 中。",
        ),
        Module(
            "GimbalPower",
            "Render",
            "None",
            [
                bool_setting("Show Voltage", True),
                bool_setting("Glow Alert", True),
                number_setting("Warn Below", 18, 0, 30, 1, "V"),
            ],
            enabled=True,
            description="把 RMUI GIMPOWER 元素转换为 POLAR 右侧 HUD。",
        ),
        Module(
            "PowerLimit",
            "Render",
            "None",
            [
                bool_setting("Show Limit", True),
                number_setting("Limit", 80, 30, 120, 1, "W"),
                bool_setting("Pulse Overrun", True),
            ],
            enabled=True,
            description="把 RMUI POWERLIM 元素转换为 POLAR 状态卡片。",
        ),
        Module(
            "SpeedLimit",
            "Render",
            "None",
            [
                bool_setting("Show Speed", True),
                number_setting("Limit", 420, 120, 720, 10, "dps"),
                mode_setting("Unit", "dps", ["dps", "rpm", "%"]),
            ],
            enabled=True,
            description="把 RMUI SPEEDLIM 元素转换为 POLAR 状态卡片。",
        ),
        Module(
            "Capacitor",
            "Render",
            "None",
            [
                bool_setting("Show Bar", True),
                number_setting("Warn", 25, 0, 100, 1, "%"),
                bool_setting("Use Glow", True),
            ],
            enabled=True,
            description="把 RMUI CAP 元素转换为 POLAR 进度卡片。",
        ),
        Module(
            "FrictionWheel",
            "Combat",
            "F",
            [
                bool_setting("Show State", True),
                bool_setting("Spin Preview", True),
                number_setting("Target RPM", 5200, 1000, 9000, 100, "rpm"),
            ],
            description="把 RMUI FRIC 元素作为摩擦轮状态模块。",
        ),
        Module(
            "LockState",
            "Combat",
            "L",
            [
                bool_setting("Show Lock", True),
                mode_setting("Lock Source", "Vision", ["Vision", "Radar", "Manual"]),
                bool_setting("Pulse Locked", True),
            ],
            enabled=True,
            description="把 RMUI LOCK 元素转换为 POLAR 目标锁定指示器。",
        ),
        Module(
            "BuffStatus",
            "Strategy",
            "None",
            [
                bool_setting("Small Buff", True),
                bool_setting("Large Buff", True),
                bool_setting("Anti Rotation", True),
            ],
            enabled=True,
            description="把 RMUI SMLBUFF/LARBUFF/ANTIRO 转换为 POLAR 状态标签。",
        ),
        Module(
            "ProjectileAllowance",
            "Strategy",
            "None",
            [
                bool_setting("17mm", True),
                bool_setting("42mm", True),
                bool_setting("Warn Low", True),
            ],
            enabled=True,
            description="POLAR HUD 风格的发弹允许量显示。",
        ),
        Module(
            "JumpPTEWheelLES",
            "System",
            "None",
            [
                bool_setting("JUP", True),
                bool_setting("PTE", True),
                bool_setting("WHE", True),
                bool_setting("LES", True),
            ],
            enabled=True,
            description="把 RMUI JUP/PTE/WHE/LES 状态元素转换为 POLAR 指示器。",
        ),
    ]
    return modules
