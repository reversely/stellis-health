"""Locally matched, attributed public explanations; never patient facts or replies."""
import hashlib,json,re
from pathlib import Path

def entries():return json.loads(Path(__file__).with_name('clinical_references.json').read_text())
def get(identity):return next((item for item in entries() if item['id']==identity),None)
def digest(item):return hashlib.sha256(item['summary'].encode()).hexdigest()
def matching(text):
 return [item for item in entries() if any(re.search(r'\b'+re.escape(term)+r'\b',text,re.I) for term in item['terms'])]
