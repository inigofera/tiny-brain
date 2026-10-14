from pathlib import Path


def test_archive_fixture_exists():
    assert Path("obsidian-20261007T153549Z-1-001.zip").exists()
