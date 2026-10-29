"""Conservative OCR candidates for device photos; saving always needs review."""
from __future__ import annotations

import re

from stellis import document_ocr, documents, readings

UNITS = {'weight': ('kg', 'lb'), 'blood_pressure': ('mmHg',),
         'blood_glucose': ('mg/dL', 'mmol/L'), 'temperature': ('°C', '°F')}
NUMBER = r'(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)(?![\d.,])'


def normalize(measure: str, value: float, unit: str) -> float:
    """Converts supported device units into the existing series units."""
    if unit not in UNITS.get(measure, ()):
        raise ValueError('Choose a listed unit for this measurement')
    if measure == 'weight' and unit == 'lb':
        return round(value * 0.45359237, 1)
    if measure == 'blood_glucose' and unit == 'mmol/L':
        return round(value * 18.018, 1)
    if measure == 'temperature' and unit == '°F':
        return round((value - 32) * 5 / 9, 1)
    return value


def candidates(text: str, measure: str) -> list[dict]:
    """Returns bounded labelled values, never an unlabelled guessed unit."""
    if measure not in UNITS:
        raise ValueError('Choose a scale, blood pressure, glucose or temperature photo')
    patterns = {
        'weight': NUMBER + r'\s*(kg|lbs?)(?!\w)',
        'blood_glucose': NUMBER + r'\s*(mg\s*/\s*d[l1]|mmol\s*/\s*[l1])(?!\w)',
        'temperature': NUMBER + r'\s*([°º]?\s*[cf])(?!\w)',
        'blood_pressure': r'(?<!\d)(\d{2,3})\s*/\s*(\d{2,3})(?!\d)(?:\s*mm\s*hg)?',
    }
    normalized_units = {'kg':'kg','lb':'lb','lbs':'lb','mg/dl':'mg/dL','mg/d1':'mg/dL',
                        'mmol/l':'mmol/L','mmol/1':'mmol/L','c':'°C','f':'°F'}
    text = text[:100_000]
    found = []
    seen = set()
    for match in re.finditer(patterns[measure], text, re.I):
        value = float(match[1].replace(',','.'))
        value2 = float(match[2]) if measure == 'blood_pressure' else None
        unit = 'mmHg' if value2 is not None else normalized_units[re.sub(r'[\s°º]','',match[2]).lower()]
        converted = normalize(measure,value,unit)
        low, high = readings.MEASURES[measure].bounds
        if not low <= converted <= high or (value2 is not None and not low <= value2 < converted):
            continue
        item = {'measure':measure,'value':value,'value2':value2,'unit':unit,'quote':match[0].strip()}
        key = (value, value2, unit)
        if key not in seen:
            seen.add(key)
            found.append(item)
            if len(found) == 10:
                break
    if measure == 'blood_pressure' and not found:
        systolic = re.search(r'\bSYS(?:TOLIC)?\s*[:=]?\s*(\d{2,3})\b',text,re.I)
        diastolic = re.search(r'\bDIA(?:STOLIC)?\s*[:=]?\s*(\d{2,3})\b',text,re.I)
        if systolic and diastolic:
            return candidates(systolic[1]+'/'+diastolic[1],measure)
    return found[:10]


def extract(document_id: str, measure: str) -> dict:
    """Reads the uploaded image with the existing bounded OCR service."""
    if measure not in UNITS:
        raise ValueError('Choose a supported measurement')
    record = documents.get(document_id)
    if not str(record['media_type']).startswith('image/'):
        raise ValueError('Choose a photo for a device reading')
    page = documents.get_page(record, 1)
    if not page['text']:
        document_ocr.read_page(document_id,1)
        page = documents.get_page(documents.get(document_id),1)
    return {'document_id':document_id, 'candidates':candidates(page['text'],measure),
            'target':'/document/'+document_id, 'text':page['text'][:100_000]}
