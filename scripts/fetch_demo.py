"""Download the demo content (Maya's synthetic records) at the revision named in DEMO_REVISION.

Usage:
  python scripts/fetch_demo.py [DEST [REVISION]]

REVISION defaults to the one in DEMO_REVISION, and DEST to .demo/<revision>. With the default DEST, the
script also links src/stellis/static/maya to it (git-ignored), the folder the app falls back to when
STELLIS_DEMO_DIR is unset, so development, tests and film scripts find the demo where they always have.
The script prints the folder. Files already present with the right size are kept, so a second run only
fills gaps.
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DATASET = "reversely/stellis-health-demo"
ROOT = Path(__file__).resolve().parents[1]
LINK = ROOT / "src" / "stellis" / "static" / "maya"


def files(revision: str) -> list[dict]:
    """Lists the dataset's files at a revision, from the Hugging Face API."""
    quoted = urllib.parse.quote(revision, safe="")
    url = f"https://huggingface.co/api/datasets/{DATASET}/tree/{quoted}?recursive=1"
    with urllib.request.urlopen(url, timeout=30) as response:
        return [entry for entry in json.load(response) if entry["type"] == "file"]


def fetch(revision: str, dest: Path) -> Path:
    """Downloads every content file at the revision into dest, skipping the dataset's own files."""
    for entry in files(revision):
        path = entry["path"]
        if path in {"README.md", ".gitattributes"}:
            continue
        target = dest / path
        if target.is_file() and target.stat().st_size == entry["size"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/datasets/{DATASET}/resolve/{urllib.parse.quote(revision, safe='')}/{urllib.parse.quote(path)}"
        partial = target.with_name(target.name + ".part")
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        partial.replace(target)
    return dest


def main() -> int:
    revision = sys.argv[2] if len(sys.argv) > 2 else (ROOT / "DEMO_REVISION").read_text().strip()
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".demo" / revision
    fetch(revision, dest)
    if len(sys.argv) == 1 and (LINK.is_symlink() or not LINK.exists()):
        LINK.unlink(missing_ok=True)
        LINK.symlink_to(Path("..", "..", "..", ".demo", revision))
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
