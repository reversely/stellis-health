"""Read one document page in a process with resource limits."""

import io
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile

MAX_PIXELS = 8_000_000
MAX_SIDE = 4096
MAX_TEXT = 100_000


def page_image(path, media_type, number):
    """Returns a bounded PNG for a PDF page or an oriented image."""
    if media_type == "application/pdf":
        import pymupdf
        with pymupdf.open(path) as document:
            page = document[number - 1]
            area = page.rect.width * page.rect.height
            if area <= 0:
                raise ValueError("Empty page")
            scale = min(200 / 72, math.sqrt(MAX_PIXELS / area),
                        MAX_SIDE / max(page.rect.width, page.rect.height))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                   colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 30_000_000
    with Image.open(path) as source:
        if source.width * source.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Image limit")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((2800, 2800))
        canvas = Image.new("RGBA", image.size, "white")
        canvas.alpha_composite(image)
        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="PNG")
        return output.getvalue()


def main():
    """Applies process limits before rendering and reading one page."""
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 ** 2,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (25, 26))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 ** 2,) * 2)
    except ImportError:
        pass
    path, media_type, number, executable = sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="stellis-ocr-") as directory:
        image = pathlib.Path(directory) / "page.png"
        image.write_bytes(page_image(path, media_type, int(number)))
        result = subprocess.run(
            [executable, str(image), "stdout", "-l", "eng", "--psm", "3"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"}, check=True)
        text = result.stdout.decode("utf-8").strip()
        if len(text) > MAX_TEXT:
            raise ValueError("Text limit")
        print(json.dumps({"text": text}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # The parent reports a fixed message without file names or page text.
        sys.exit(2)
