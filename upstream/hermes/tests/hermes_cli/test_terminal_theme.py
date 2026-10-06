from hermes_cli.web_server_dashboard import _normalise_theme_definition


def test_terminal_theme_survives_wire_projection():
    theme = _normalise_theme_definition({"name": "test", "terminalBackground": "#1d2027", "terminalForeground": "#e5e7eb"})
    assert theme["terminalBackground"] == "#1d2027"
    assert theme["terminalForeground"] == "#e5e7eb"


def test_terminal_theme_leaves_unset_defaults_alone():
    theme = _normalise_theme_definition({"name": "test", "terminalBackground": {}, "terminalForeground": " "})
    assert "terminalBackground" not in theme
    assert "terminalForeground" not in theme
