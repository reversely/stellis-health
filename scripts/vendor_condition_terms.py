"""Build an offline condition-name index from a downloaded MedlinePlus topic ZIP.

Only title/alias/URL metadata is retained. This title-based subset is a lookup aid,
not a diagnosis classifier; free-text answers remain available.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

SOURCE = 'https://medlineplus.gov/xml/mplus_topics_compressed_2026-10-24.zip'
# Keep condition titles rather than fitness, goals, procedures or service categories.
CONDITION = re.compile(r'\b(disease|disorder|syndrome|cancer|infection|injur|allerg|anemia|aneurysm|angina|anxiety|arrhythmia|arthritis|asthma|autism|bipolar|bronchitis|celiac|cerebral palsy|cirrhosis|colitis|concussion|constipation|copd|crohn|cyst|dementia|depression|diabetes|diverticul|eczema|endometriosis|epilepsy|fibromyalgia|fibroid|fracture|gallstone|glaucoma|gout|heart attack|heart failure|hepatitis|hernia|hiv|hypertension|hypothyroid|hyperthyroid|infertility|insomnia|kidney stone|leukemia|lupus|lymphoma|migraine|multiple sclerosis|obesity|osteoporosis|pain|parkinson|pneumonia|polycystic|psoriasis|reflux|scoliosis|sickle cell|sleep apnea|stroke|thrombosis|ulcer|high blood pressure|high cholesterol|adhd)', re.I)
EXCLUDE = re.compile(r'\b(screening|tests?|surgery|therapy|rehabilitation|prevention|medicines|drugs|vaccines|caregivers|living with|coping with)\b', re.I)


def build(path):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.xml'))))
    terms = []
    for topic in root:
        title = topic.get('title', '')
        if topic.get('language') != 'English' or not CONDITION.search(title) or EXCLUDE.search(title):
            continue
        terms.append({'id': topic.get('id'), 'label': title, 'url': topic.get('url'),
                      'aliases': sorted({t.text for tag in ('also-called', 'see-reference') for t in topic.findall(tag) if t.text})})
    return {'source_url': SOURCE, 'date_generated': root.get('date-generated'),
            'attribution': 'Condition names from MedlinePlus.gov, U.S. National Library of Medicine.',
            'scope': 'Title-based condition subset; free-text entries remain available.',
            'terms': sorted(terms, key=lambda t: t['label'].casefold())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zipfile')
    parser.add_argument('--output', default='src/stellis/condition_terms.json')
    args = parser.parse_args()
    result = build(args.zipfile)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f"Indexed {len(result['terms'])} condition titles")
