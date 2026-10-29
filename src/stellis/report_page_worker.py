"""Bounded original-page rendering and engine-independent line geometry."""
import csv
import hashlib
import io
import json
import os
import pathlib
import resource
import subprocess
import sys
import tempfile

from document_ocr_worker import page_image


def embedded(path, number):
    import pymupdf
    with pymupdf.open(path) as pdf:
        page = pdf[number - 1]
        width, height = page.rect.width, page.rect.height
        lines = {}
        for word in page.get_text('words'):
            rect = pymupdf.Rect(word[:4]) * page.rotation_matrix
            # PDF generators often put each table cell in its own text block.
            # The original y baseline, rather than a block ID, defines the row.
            key = round(word[3] / 3)
            item = lines.setdefault(key, {'text': [], 'rect': rect})
            item['text'].append((word[0], word[4]))
            item['rect'] |= rect
        return [{'text': ' '.join(text for _, text in sorted(item['text'])),
                 'box': [item['rect'].x0 / width, item['rect'].y0 / height,
                         item['rect'].width / width, item['rect'].height / height]}
                for item in lines.values()]


def scanned(png, executable):
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    with tempfile.TemporaryDirectory(prefix='stellis-report-') as directory:
        path = pathlib.Path(directory) / 'page.png'
        path.write_bytes(png)
        output = subprocess.run([executable, str(path), 'stdout', '-l', 'eng',
                                 '--psm', '3', 'tsv'], check=True, timeout=20,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={**os.environ, 'OMP_THREAD_LIMIT': '1'}).stdout
    if len(output) > 4_000_000:
        raise ValueError('Output limit')
    lines = {}
    for row in csv.DictReader(io.StringIO(output.decode()), delimiter='\t'):
        if row['level'] != '5' or not row['text'].strip():
            continue
        x, y, w, h = [int(row[k]) for k in ('left', 'top', 'width', 'height')]
        key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
        item = lines.setdefault(key, {'text': [], 'rect': [x, y, x+w, y+h]})
        item['text'].append(row['text'])
        box = item['rect']
        item['rect'] = [min(box[0], x), min(box[1], y), max(box[2], x+w), max(box[3], y+h)]
    return [{'text': ' '.join(item['text']),
             'box': [item['rect'][0] / width, item['rect'][1] / height,
                     (item['rect'][2]-item['rect'][0]) / width,
                     (item['rect'][3]-item['rect'][1]) / height]}
            for item in lines.values()]


def main():
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    os.environ['OMP_NUM_THREADS'] = '1'
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (25, 26))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2,) * 2)
    path, media_type, number, mode, executable = sys.argv[1:]
    png = page_image(path, media_type, int(number))
    if mode == 'image':
        sys.stdout.buffer.write(png)
        return
    lines = embedded(path, int(number)) if media_type == 'application/pdf' else []
    engine = 'pdf-text'
    if not lines:
        lines = scanned(png, executable)
        engine = 'tesseract-tsv'
    if len(lines) > 3000 or sum(len(line['text']) for line in lines) > 100_000:
        raise ValueError('Page limit')
    for index, line in enumerate(lines):
        line['id'] = str(index)
        line['box'] = [round(v, 6) for v in line['box']]
    if engine == 'pdf-text':
        import pymupdf
        engine_version = pymupdf.VersionBind
    else:
        engine_version = subprocess.run([executable, '--version'], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=3, check=True).stdout.decode().splitlines()[0]
    print(json.dumps({'engine': engine, 'engine_version': engine_version,
                      'source_digest': hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(),
                      'regions': lines}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(2)
