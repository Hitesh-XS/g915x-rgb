from g915x_rgb.gkey_actions import get_profile_switch_target


def test_returns_target_for_switch_profile_action():
    actions = {
        0xB4: {
            "type": "switch_profile",
            "target": "Blue",
        }
    }

    assert get_profile_switch_target(actions, 0xB4) == "Blue"


def test_returns_none_when_action_is_missing():
    assert get_profile_switch_target({}, 0xB4) is None


def test_returns_none_for_unsupported_action():
    actions = {
        0xB4: {
            "type": "launch_application",
            "target": "Blue",
        }
    }

    assert get_profile_switch_target(actions, 0xB4) is None


def test_returns_none_for_invalid_target():
    actions = {
        0xB4: {
            "type": "switch_profile",
            "target": "   ",
        }
    }

    assert get_profile_switch_target(actions, 0xB4) is None


def test_returns_none_for_non_dict_action():
    assert get_profile_switch_target({0xB4: "Blue"}, 0xB4) is None


def test_strips_whitespace_from_profile_target():
    actions = {
        0xB4: {
            "type": "switch_profile",
            "target": "  Blue  ",
        }
    }

    assert get_profile_switch_target(actions, 0xB4) == "Blue"