import importlib


def test_workspace_packages_import() -> None:
    importlib.import_module("mana_leak_core")
    api = importlib.import_module("mana_leak_api.main")
    assert api.app.title == "Mana Leak API"
