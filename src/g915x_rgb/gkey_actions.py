def get_profile_switch_target(actions, address):
    action = actions.get(address)

    if not isinstance(action, dict):
        return None

    if action.get("type") != "switch_profile":
        return None

    target = action.get("target")

    if isinstance(target, str) and target.strip():
        return target.strip()

    return None