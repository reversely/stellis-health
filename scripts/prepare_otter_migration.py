"""Extract browser-downloaded exports and attach their text as local references.

Run ``prepare`` before uploading the extracted MP3s through Stellis. Run
``attach`` afterward to pair each export with its imported audio by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from stellis import storage


def members(archive: zipfile.ZipFile, suffix: str) -> list[zipfile.ZipInfo]:
    return [item for item in archive.infolist() if not item.is_dir()
            and not item.filename.startswith("__MACOSX/")
            and item.filename.lower().endswith(suffix)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "attach"])
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    if args.phase == "prepare":
        manifest = []
        for archive_path in sorted(args.export_dir.glob("*.zip")):
            with zipfile.ZipFile(archive_path) as archive:
                audio = members(archive, ".mp3")
                transcript = members(archive, "_transcript.txt")
                summary = members(archive, "_summary.txt")
                if len(audio) != 1 or len(transcript) != 1:
                    raise ValueError(f"Expected one MP3 and transcript in {archive_path.name}")
                if audio[0].file_size > 1_000_000_000:
                    raise ValueError(f"Oversized MP3 in {archive_path.name}")
                audio_path = args.output_dir / f"{archive_path.stem}.mp3"
                digest = hashlib.sha256()
                with archive.open(audio[0]) as source, audio_path.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        target.write(chunk)
                audio_path.chmod(0o600)
                transcript_text = archive.read(transcript[0]).decode("utf-8-sig")
                summary_text = archive.read(summary[0]).decode("utf-8-sig") if summary else ""
                (args.output_dir / f"{archive_path.stem}_transcript.txt").write_text(transcript_text)
                (args.output_dir / f"{archive_path.stem}_summary.txt").write_text(summary_text)
                manifest.append({"otter_id": archive_path.stem, "archive": archive_path.name,
                                 "audio_name": audio[0].filename, "sha256": digest.hexdigest(),
                                 "audio_path": str(audio_path), "transcript_chars": len(transcript_text)})
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Prepared {len(manifest)} exports")
    else:
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest:
            record = storage.find_by_hash(entry["sha256"])
            if record is None:
                print(f"MISSING {entry['otter_id']}")
                continue
            stem = entry["otter_id"]
            storage.save_import_reference(
                str(record["id"]),
                (args.output_dir / f"{stem}_transcript.txt").read_text(),
                (args.output_dir / f"{stem}_summary.txt").read_text(),
                entry["archive"],
            )
            print(f"ATTACHED {stem} {record['id']}")


if __name__ == "__main__":
    main()
