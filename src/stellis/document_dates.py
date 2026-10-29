"""Evidence-backed encounter-date candidates from embedded or OCR page text."""
from __future__ import annotations

from datetime import date, datetime
import re

_MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_DATE = rf'(?:\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}|\d{{1,2}}\s+{_MONTH}\s+\d{{4}}|{_MONTH}\s+\d{{1,2}},?\s+\d{{4}})'
_LABELS = (
    (0, 'encounter', r'clinical\s+date|encounter\s+date|date\s+of\s+(?:service|visit|examination|exam|encounter)|(?:visit|exam(?:ination)?)\s+date'),
    (1, 'procedure', r'procedure\s+date|date\s+of\s+procedure'),
    (2, 'specimen', r'(?:date\s+)?collected|collection\s+date|specimen\s+date|date\s+of\s+collection'),
    (3, 'report', r'report\s+date|date\s+(?:reported|of\s+report)'),
)


def _dates(raw: str) -> list[str]:
    """Keeps ambiguous numeric day/month orders as separate candidates."""
    candidates = []
    if re.fullmatch(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', raw):
        year, month, day = map(int, re.split(r'[-/]', raw))
        try:
            return [date(year, month, day).isoformat()]
        except ValueError:
            return []
    if re.fullmatch(r'\d{1,2}[/-]\d{1,2}[/-]\d{4}', raw):
        first, second, year = map(int, re.split(r'[-/]', raw))
        for month, day in ((second, first), (first, second)):
            try:
                value = date(year, month, day).isoformat()
                if value not in candidates:
                    candidates.append(value)
            except ValueError:
                pass
        return candidates
    for pattern in ('%d %B %Y', '%d %b %Y', '%B %d %Y', '%b %d %Y'):
        try:
            return [datetime.strptime(raw.replace(',', ''), pattern).date().isoformat()]
        except ValueError:
            pass
    return []


def suggest(pages: list[dict]) -> dict:
    """Returns labelled evidence, prefilling only an unambiguous best candidate."""
    candidates = []
    seen = set()
    for page in pages:
        text = str(page['text'])[:100_000]
        for rank, role, label in _LABELS:
            # A label may precede its value on the next line in extracted table text.
            pattern = rf'(?<![\w])(?:{label})\s*[:|]?\s*(?P<date>{_DATE})(?!\d)'
            for match in re.finditer(pattern, text, re.I):
                for value in _dates(match['date']):
                    key = (value, role, page['number'])
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(dict(date=value, role=role, rank=rank,
                                           page=page['number'],
                                           quote=' '.join(match[0].split()),
                                           text_source=page.get('text_source', 'embedded')))
    candidates.sort(key=lambda item: (item['rank'], item['page'], item['date']))
    if not candidates:
        return dict(status='missing', date=None, candidates=[])
    best = {item['date'] for item in candidates if item['rank'] == candidates[0]['rank']}
    return dict(status='suggested' if len(best) == 1 else 'ambiguous',
                date=next(iter(best)) if len(best) == 1 else None,
                candidates=candidates[:20])
