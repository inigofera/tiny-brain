from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and import an Obsidian archive into an empty vault")
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--staging", type=Path, default=None)
    args = parser.parse_args()
    staging_parent = args.staging or args.destination.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tiny-brain-import-", dir=staging_parent) as temporary:
        extraction = Path(temporary)
        with zipfile.ZipFile(args.archive) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise SystemExit("duplicate archive paths detected")
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    raise SystemExit(f"unsafe archive path: {name}")
            archive.extractall(extraction)
        roots = [path for path in extraction.iterdir() if path.name != "__MACOSX"]
        source = roots[0] if len(roots) == 1 and roots[0].is_dir() else extraction
        if args.destination.exists() and any(args.destination.iterdir()):
            raise SystemExit("destination exists and is not empty; refusing to merge implicitly")
        args.destination.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            target = args.destination / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    print(f"Imported {args.archive} into {args.destination}")


if __name__ == "__main__":
    main()
