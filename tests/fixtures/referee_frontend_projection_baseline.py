def sync_frontend_state(self, state: RobotState) -> None:
    """Project authoritative rows, map positions and buffs onto the HUD."""
    has_real = (
        bool(state.official_robot_health)
        or bool(state.robot_positions)
        or bool(state.robot_statics)
        or bool(state.robot_dynamics)
        or bool(state.robot_modules)
    )
    if not has_real:
        return
    state.backend_frontend_synced = True

    health = [int(v) for v in state.official_robot_health[:10]]
    own_blue = int(state.robot_id) >= 100 or str(state.alliance_color).lower() == "blue"
    ally_ids = tuple(role + (100 if own_blue else 0) for role in _HEALTH_ROLE_ORDER)
    enemy_ids = tuple(role + (0 if own_blue else 100) for role in _HEALTH_ROLE_ORDER)
    health_by_id = {
        robot_id: health[index]
        for index, robot_id in enumerate(ally_ids + enemy_ids)
        if index < len(health)
    }
    observed_ids = {
        *(int(key) for key in state.robot_statics),
        *(int(key) for key in state.robot_dynamics),
        *(int(key) for key in state.robot_modules),
        *(int(item.get("robot_id", 0)) for item in state.robot_positions),
    }
    robot_ids = list(ally_ids + enemy_ids) if health else []
    robot_ids.extend(sorted(robot_id for robot_id in observed_ids if robot_id > 0 and robot_id not in robot_ids))
    rows: list[dict[str, Any]] = []
    for robot_id in robot_ids:
        base = robot_id if robot_id < 100 else robot_id - 100
        statics = state.robot_statics.get(robot_id, {})
        dynamics = state.robot_dynamics.get(robot_id, {})
        global_hp = max(0, int(health_by_id.get(robot_id, 0)))
        dynamic_hp = int(dynamics.get("current_health", 0))
        # Local 10 Hz telemetry wins over the older 1 Hz team snapshot,
        # including a valid zero at death (not a fallback to stale HP).
        hp = dynamic_hp if robot_id == int(state.robot_id) and "current_health" in dynamics else global_hp or dynamic_hp
        hp_max = int(statics.get("max_health", 0)) or DEFAULT_MAX_HEALTH
        alive_state = int(statics.get("alive_state", 1))
        if alive_state == 2 or hp <= 0:
            row_state = "DEAD"
        elif robot_id == int(state.robot_id):
            row_state = "LOCAL"
        else:
            row_state = "ALIVE"
        rows.append({
            "id": robot_id,
            "name": ROBOT_ID_NAMES.get(base, "Robot"),
            "hp": max(0, hp),
            "max": max(1, hp_max),
            "state": row_state,
            "x": None,
            "y": None,
            "modules": state.robot_modules.get(robot_id, {}),
        })
    positions = {int(p.get("robot_id", -1)): p for p in state.robot_positions}
    for row in rows:
        pos = positions.get(int(row["id"]))
        if pos:
            row["x"] = float(pos.get("x", 0.0))
            row["y"] = float(pos.get("y", 0.0))
            row["yaw"] = float(pos.get("yaw", 0.0))
    state.robot_rows = rows

    if positions:
        map_robots: list[dict[str, Any]] = []
        for robot_id, pos in positions.items():
            base = robot_id if robot_id < 100 else robot_id - 100
            global_hp = max(0, int(health_by_id.get(robot_id, 0)))
            dynamic_hp = int(state.robot_dynamics.get(robot_id, {}).get("current_health", 0))
            local_dynamics = state.robot_dynamics.get(robot_id, {})
            hp = dynamic_hp if robot_id == int(state.robot_id) and "current_health" in local_dynamics else global_hp or dynamic_hp
            hp_max = int(state.robot_statics.get(robot_id, {}).get("max_health", 0)) or DEFAULT_MAX_HEALTH
            map_robots.append({
                "id": robot_id,
                "team": "ally" if robot_id in ally_ids else "enemy",
                "name": ROBOT_ID_NAMES.get(base, "Robot"),
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "hp": max(0, hp),
                "max": max(1, hp_max),
                "highlight": False,
            })
        state.map_robots = map_robots

    if state.buffs:
        buff_rows: list[dict[str, Any]] = []
        for buff in state.buffs:
            buff_type = int(buff.get("buff_type", 0))
            remaining = max(0, int(buff.get("buff_left_time", 0)))
            buff_rows.append({
                "name": BUFF_TYPE_NAMES.get(buff_type, f"Buff {buff_type}"),
                "active": remaining > 0,
                "remaining": remaining,
                "buff_type": buff_type,
                "robot_id": int(buff.get("robot_id", 0)),
            })
        state.buff_rows = buff_rows
