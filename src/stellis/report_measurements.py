"""Literal, source-backed measurement proposals from embedded or OCR page text.

This adapter reads labelled results, never diagnoses or synthesizes missing values.
Unrecognised layouts remain available to the existing model-assisted review.
"""
import re

VERSION = 3  # Revisit earlier empty literal-extraction receipts after parser changes.

# Units are deliberately narrow: accepting a label never implies a unit conversion.
TESTS = (
    ('Ferritin', r'ferritin', r'(?:ug/L|µg/L|μg/L|ng/mL)'),
    ('Hemoglobin', r'(?:ha?emoglobin|hgb)', r'g/L'),
    ('MCV', r'MCV', r'fL'),
    ('WBC', r'(?:WBC|white blood cell count)', r'(?:10\^9/L|x10\^9/L)'),
    ('Platelets', r'(?:platelets|platelet count)', r'(?:10\^9/L|x10\^9/L)'),
    ('TSH', r'(?:TSH|thyroid stimulating hormone)', r'(?:mIU/L|uIU/mL)'),
    ('HbA1c', r'(?:HbA1c|hemoglobin A1c)', r'%'),
    ('Weight', r'(?:weight|body weight)', r'kg'),
    ('Pulse', r'(?:pulse|heart rate)', r'bpm'),
    ('Blood pressure', r'(?:blood pressure|BP)', r'mmHg'),
)
_PROSE = re.compile(r"^(?:continue|start|stop|give|take|reduce|increase|decrease|patient|administer|use|repeat|return|resume|hold)\b"
                    r"|\b(?:from|to|has|have|was|were|is|are|increased|decreased|rose|fell|using|containing|daily|nightly|tablet|tablets|and)\b", re.I)
VALUE = r'\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?'


def proposals(record, day):
    """Return only complete label/value/unit spans and their literal page quotes."""
    found = []
    for page in record['pages']:
        text = page['text']
        from stellis import document_dates
        dated = document_dates.suggest([page])
        # Conflicting labelled dates on one page require the existing date review.
        if dated['status'] == 'ambiguous':
            continue
        page_day = dated['date'] or day
        for name, label, units in TESTS:
            pattern = (r'(?im)^[ \t]*' + label + r'\s*:?\s*(?P<value>' + VALUE +
                       r')\s*(?P<unit>' + units + r')(?!\w)' +
                       r'(?P<range>\s+(?:\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?)(?:[ \t\r\n]+[LH](?!\w))?)?')
            for match in re.finditer(pattern, text):
                quote = ' '.join(match.group().split())
                if len(quote) > 300:
                    continue
                found.append(dict(kind='vital_sign' if name in {'Weight','Pulse','Blood pressure'} else 'lab_result',
                    name=name, value=re.sub(r'\s*/\s*','/',match['value']), unit=match['unit'],
                    date=page_day, page=page['number'], quote=quote))
    # Discover other labelled numeric rows without a metric-name allowlist.
    # Require a compact unit token; prose/date fragments are not measurements.
    unit = r'(?:[%]|[A-Za-zµμ][A-Za-zµμ0-9^.*-]*(?:/[A-Za-zµμ][A-Za-zµμ0-9^.*-]*)?)'
    # Colon-delimited laboratory labels can contain LOINC-style brackets,
    # specimen names, digits and slashes. Keep that complete identity rather
    # than collapsing distinct tests to a familiar analyte name. A required
    # colon prevents digits in a label from becoming the reported value.
    label = r'(?:(?<!\S)[A-Za-z][A-Za-z0-9 ()\[\]/.,^+%-]{1,119}:[ \t]*|^[ \t]*[A-Za-z][A-Za-z ()-]{1,79}[ \t]*\s+)'
    generic = (r'(?m)(?P<label>'+label+r')'
               r'(?P<value>-?\d+(?:\.\d+)?)\s+(?P<unit>'+unit+r')'
               r'(?![\w/])'
               r'(?P<range>\s+\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?(?:[ \t\r\n]+[LH](?!\w))?)?')
    from stellis import readings, document_dates
    for page in record['pages']:
        dated = document_dates.suggest([page])
        if dated['status'] == 'ambiguous':
            continue
        for match in re.finditer(generic, page['text']):
            name = ' '.join(match['label'].rstrip().removesuffix(':').split())
            if readings.match_measure(name) is not None:
                continue  # Known schemas above retain their unit validation.
            # An instruction or a sentence is not a result name: "Continue the same 300 mg",
            # "Start ferrous sulfate 300 mg", "Ferritin increased from 13 ug/L".
            if not match['label'].rstrip().endswith(':') and (_PROSE.search(name) or len(name.split()) > 4):
                continue
            # Plain words are not units. Accept percent, ratios, exponents or common standalone units.
            units = match['unit']
            # Bare dose units (mg, IU, g, mL) belong to medicine instructions, not to results.
            if not any(c in units for c in '/%^') and units.casefold() not in {'fl','pg','kg','bpm','mmhg','ms','cm','mm','kpa'}:
                continue
            found.append(dict(kind='lab_result',name=name,value=match['value'],unit=units,
                              date=dated['date'] or day,page=page['number'],quote=' '.join(match.group().split())))
    # A repeated test with differing values may be a comparison table. We cannot
    # assign its column dates with this adapter, so leave it for explicit review.
    ambiguous = {(f['page'],f['name']) for f in found if len({
        (other['value'],other['unit']) for other in found
        if (other['page'],other['name'])==(f['page'],f['name'])})>1}
    return [f for f in found if (f['page'],f['name']) not in ambiguous]
