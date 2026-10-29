"""Verify the deployed OCR pipeline with isolated image and scanned PDF records."""

import os
import tempfile

import pymupdf

from stellis import document_ocr, documents


def _picture(lines, *, scan=False):
    """Renders printed lines as an image with no text layer: a PNG, or a PDF page holding that image."""
    with pymupdf.open() as original:
        page = original.new_page()
        for number, line in enumerate(lines):
            page.insert_text((50, 80 + 28 * number), line, fontsize=18)
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    if not scan:
        return image
    with pymupdf.open() as scanned:
        scanned.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=image)
        return scanned.tobytes()


def main():
    """Fail when the image cannot recognize printed text through the app worker."""
    with tempfile.TemporaryDirectory(prefix="stellis-ocr-check-") as directory:
        os.environ["STELLIS_DATA_DIR"] = directory
        fixtures = [
            ("scan.pdf", _picture(["Medication: Metformin 500 mg"], scan=True), ("Metformin 500 mg",)),
            ("lab.png", _picture(["Glucose 67.8 mg/dL", "Urea nitrogen 2.5 mg/dL"]),
             ("67.8 mg/dL", "2.5 mg/dL")),
        ]
        for filename, content, expected in fixtures:
            record, _ = documents.create(filename, content)
            result = document_ocr.read_page(record["id"], 1)
            page = result["pages"][0]
            if page["text_source"] != "ocr" or any(text not in page["text"] for text in expected):
                raise RuntimeError(f"OCR verification failed for {filename}")
    print("OCR verification passed for scanned PDF and lab photo")


if __name__ == "__main__":
    main()
