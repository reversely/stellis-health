"""Date proposals preserve source evidence and never replace confirmed dates."""
import io
import os
import tempfile
from unittest.mock import patch

from PIL import Image
import pytest

from stellis import document_dates, documents


def pages(text):
    return [dict(number=1, text=text, text_source='ocr')]


def test_encounter_wins_over_birth_specimen_and_report_dates():
    result = document_dates.suggest(pages('DOB: 1993-03-14\nClinical date\n2022-02-07\nCollected: 2022-02-02\nReport date: 2022-02-08'))
    assert result['date'] == '2022-02-07'
    assert result['candidates'][0]['quote'] == 'Clinical date 2022-02-07'
    assert result['candidates'][0]['text_source'] == 'ocr'
    assert not any(c['date'] == '1993-03-14' for c in result['candidates'])


@pytest.mark.parametrize('text', ['Encounter date 03/04/2025', 'Clinical date 2025-04-03\nClinical date 2025-04-04'])
def test_ambiguous_dates_need_review(text):
    result = document_dates.suggest(pages(text))
    assert result['status'] == 'ambiguous'
    assert result['date'] is None


@pytest.mark.parametrize('text,expected', [('Date of service: 14 March 2023','2023-03-14'), ('Exam date: November 15, 2024','2024-11-15'), ('Collection date: 19/06/2026','2026-06-19'), ('Encounter date: 2024-02-31', None), ('DOB: 1993-03-14\nNext appointment 2025-01-01',None)])
def test_supported_dates_and_missing_evidence(text, expected):
    assert document_dates.suggest(pages(text))['date'] == expected


def test_ocr_revisions_refresh_suggestions_but_keep_confirmed_date():
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STELLIS_DATA_DIR':folder}):
        image=io.BytesIO();Image.new('RGB',(40,30),'white').save(image,format='PNG')
        record,_=documents.create('scan.png',image.getvalue())
        assert record['date_suggestion']['status']=='missing'
        record=documents.save_page(record['id'],1,'Encounter date: 2025-01-08',0,source='ocr')
        assert record['date_suggestion']['date']=='2025-01-08'
        documents.update(record['id'],record_date='2025-01-06')
        record=documents.save_page(record['id'],1,'Encounter date: 2025-01-09',1,source='manual')
        assert record['date_suggestion']['date']=='2025-01-09'
        assert record['record_date']=='2025-01-06'
        with pytest.raises(ValueError):
            documents.update(record['id'],record_date='2025-02-30')


def test_lab_printout_month_abbreviation_dates():
    # The owner's urinalysis printout (#270): day/abbreviated month/year, label above a table.
    text = ('Admission Date: 16/Nov/2023\nLab #: 26-23-320-0137\nCollected: 16/Nov/2023 16:06\n'
            'Received: 16/Nov/2023 16:15\nGlucose; Urine Negative <5.5 mmol/L 16/Nov/2023 17:22')
    result = document_dates.suggest(pages(text))
    assert (result['status'], result['date']) == ('suggested', '2023-11-16')
    assert document_dates.suggest(pages('Collected: 3-Feb-2024 09:10'))['date'] == '2024-02-03'
