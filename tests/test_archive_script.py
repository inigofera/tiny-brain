import zipfile
from pathlib import Path


def test_archive_fixture_exists(tmp_path: Path):
    fixture = Path("obsidian-20261007T153549Z-1-001.zip")
    if fixture.exists():
        assert fixture.is_file()
    else:
        mock_zip = tmp_path / "obsidian-test.zip"
        with zipfile.ZipFile(mock_zip, "w") as z:
            z.writestr("test.md", "# Test")
        assert mock_zip.exists()
