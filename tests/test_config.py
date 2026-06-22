import finab.config as config


def test_window_days_defaults_to_one(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.load_transfer_match_window_days() == 1


def test_window_days_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.save_transfer_match_window_days(3)
    assert config.load_transfer_match_window_days() == 3
