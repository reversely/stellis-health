"""The call reads the newest reports by their own date (#270)."""
import io

from PIL import Image

from stellis import call_records, documents


def _report(title, text):
    image = io.BytesIO()
    # Identical files are one upload; a size per title keeps them apart.
    Image.new("RGB", (40 + sum(map(ord, title)) % 200, 30), "white").save(image, format="PNG")
    record, _ = documents.create(title + ".png", image.getvalue())
    documents.save_page(record["id"], 1, text, documents.get(record["id"])["pages"][0]["revision"], source="manual")


def test_newest_dated_reports_come_before_an_undated_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("STELLIS_DATA_DIR", str(tmp_path))
    _report("Old panel", "Collection date: 2024-03-24\nFerritin 52 ug/L")
    _report("Undated note", "Ferritin looked fine")
    _report("New panel", "Collection date: 2031-12-14\nFerritin 48 ug/L")

    titles = [source["title"] for source in call_records._sources()]

    assert titles == ["New panel, 2031-12-14", "Old panel, 2024-03-24", "Undated note, date not on the report"]


def test_results_questions_are_recognised():
    assert call_records.about_records("Can you pull up some of my past reports from my most recent panel?")
    assert not call_records.about_records("I walked for 30 minutes this morning")
