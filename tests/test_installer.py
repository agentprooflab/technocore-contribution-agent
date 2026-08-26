from pathlib import Path


def test_launchd_installer_stages_smokes_and_rolls_back() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "install-launchd.sh").read_text()
    assert "mktemp -d" in script
    assert 'tca" coverage' in script
    assert "plutil -lint" in script
    assert "rollback()" in script
    assert "current_link.next" in script
    assert "previous_link.next" in script
    assert "launchctl print" in script
