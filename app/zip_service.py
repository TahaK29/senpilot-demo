from __future__ import annotations

import zipfile
from pathlib import Path


def create_zip(
    matter_number: str,
    document_type: str,
    downloaded_files: list[str],
    download_root: Path,
) -> Path | None:
    existing_files = [Path(path) for path in downloaded_files if Path(path).exists()]
    if not existing_files:
        return None

    matter_dir = download_root / matter_number
    matter_dir.mkdir(parents=True, exist_ok=True)
    zip_path = matter_dir / f"{matter_number}_{slugify(document_type)}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in existing_files:
            archive.write(file_path, arcname=file_path.name)

    return zip_path


def slugify(value: str) -> str:
    return value.lower().replace(" ", "_")

