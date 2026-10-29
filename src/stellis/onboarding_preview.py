"""Read-only onboarding projection of the existing synthetic Maya corpus.

Uses the native document, approval, notebook and readings pipeline in temporary
storage. No example records or approvals are written into the person's account.
"""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock, Thread

from stellis import collections, demo_patient, documents, health_catalogue, health_facts, notebooks, readings, storage

_LOCK = Lock()


def warm():
    """Builds the preview in the background so Meet Stellis does not wait for it."""
    if snapshot.cache_info().currsize == 0 and not _LOCK.locked():
        # Only an account process builds; the cache file serves the rest.
        Thread(target=snapshot, daemon=True).start()


def _cache_file(key):
    # One copy at the shared app root: the preview is the same synthetic data for every account.
    root = Path(os.environ.get("STELLIS_AUTH_DB_DIR") or storage.base_dir())
    return root / f"onboarding-preview-{key}.json"


def _key():
    """Changes when the sample records or this projection change."""
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(json.dumps(demo_patient.guide(), sort_keys=True).encode())
    return digest.hexdigest()[:16]


@lru_cache(maxsize=1)
def snapshot():
    path = _cache_file(_key())
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    result = _build()
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(result))
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
    return result


def _build():
    with _LOCK, TemporaryDirectory(prefix='stellis-onboarding-preview-') as folder:
        with storage.demo_workspace(Path(folder)):
            guide = demo_patient.guide()
            year = min(item['year'] for item in guide['steps'])
            steps = [item for item in guide['steps'] if item['year'] == year]
            templates = list(dict.fromkeys(book['template_id'] for item in steps for book in item['notebooks']))
            health_catalogue.activate([{'template_id': key} for key in templates])
            books = {book['template_id']: book for book in notebooks.list_notebooks()}
            sources = {}
            for item in steps:
                content = (demo_patient.STATIC / item['download'].removeprefix('/static/maya/')).read_bytes()
                book = books[health_catalogue.canonical_id(item['notebooks'][0]['template_id'])]
                record, _ = documents.create(item['filename'], content, book['id'], item['date'])
                health_facts.prepare(record['id'])
                health_facts.accept_all(record['id'])
                sources[record['id']] = item['download']
            measures = [measure for measure in readings.series() if measure['points']]
            for measure in measures:
                for point in measure['points']:
                    if point.get('document_id') in sources:
                        point['target'] = sources[point['document_id']]
            return {'profile': guide['patient'], 'notebooks': notebooks.list_notebooks(),
                    'collections': collections.list_collections(), 'measures': measures,
                    'records': [{**record, 'target': sources[record['id']]} for record in documents.list_all()]}
