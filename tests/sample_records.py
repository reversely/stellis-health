"""Denver Lang's records, a synthetic patient's files the tests upload.

They live in the demo dataset (samples/denver-lang, fetched by scripts/fetch_demo.py), not in the
product: his clinical records come from his Synthea bundle and his records from outside the hospital
from written narratives, each with its own manifest-*.json.
"""

from __future__ import annotations

import json
import pathlib

from stellis import demo_patient
from stellis import documents
from stellis import notebooks
from stellis import storage

FOLDER = demo_patient.STATIC / "samples" / "denver-lang"


def manifest() -> list[dict[str, str]]:
    """Returns every record from every manifest, oldest record date first."""
    records = []
    for path in sorted(FOLDER.glob("manifest-*.json")):
        records += json.loads(path.read_text())["records"]
    return sorted(records, key=lambda record: record["record_date"])


def _notebook_id(name: str) -> str:
    with storage.connect() as conn:
        row = conn.execute("SELECT id FROM notebooks WHERE normalized = ?",
                           (name.casefold(),)).fetchone()
    return str(row["id"]) if row else str(notebooks.create(name)["id"])


def load(notebook_name: str | None = None,
         files: tuple[str, ...] | None = None) -> dict[str, object]:
    """Uploads every sample record, as a person dumping all their files.

    Args:
        notebook_name: A notebook to file the records in, created when
            missing. None leaves them in no notebook, ready to sort.
        files: Record file names to load, or None for every record.

    Returns:
        ``notebook_id`` (or None), the number of records ``added``, the
        ``total`` and ``document_ids``, oldest record first. Records the
        account already has are skipped, so a second run adds nothing.
    """
    notebook_id = _notebook_id(notebook_name) if notebook_name else None
    records = [record for record in manifest()
               if files is None or record["file"] in files]
    added = 0
    ids = []
    for record in records:
        content = (FOLDER / record["file"]).read_bytes()
        document, duplicate = documents.create(record["file"], content,
                                               notebook_id,
                                               record["record_date"])
        if not duplicate:
            documents.update(str(document["id"]), title=record["title"])
            added += 1
        ids.append(str(document["id"]))
    return {"notebook_id": notebook_id, "added": added, "total": len(records),
            "document_ids": ids}
