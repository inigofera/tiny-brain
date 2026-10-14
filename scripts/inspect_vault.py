from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect an Obsidian archive without changing it")
    parser.add_argument("archive")
    args = parser.parse_args()
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SystemExit("duplicate archive paths detected")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"unsafe archive path: {name}")
        roots = sorted({name.split("/", 1)[0] for name in names if name})
        markdown = [name for name in names if name.lower().endswith(".md")]
        sizes = Counter("markdown" if name.lower().endswith(".md") else "other" for name in names)
        digest = hashlib.sha256()
        for info in archive.infolist():
            digest.update(info.filename.encode())
            digest.update(str(info.file_size).encode())
        print(json.dumps({"entries": len(names), "roots": roots, "markdown": len(markdown), "types": sizes, "archive_manifest_sha256": digest.hexdigest(), "sample": names[:30]}, indent=2, default=dict))


if __name__ == "__main__":
    main()
