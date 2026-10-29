"""Build the local name library from an official RxTerms allconcepts response."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('response', type=Path)
    args = parser.parse_args()
    concepts = json.loads(args.response.read_text())['minConceptGroup']['minConcept']
    terms = sorted({(item['rxcui'], item['fullName']) for item in concepts
                    if item['termType'] in {'SCD', 'SBD', 'GPCK', 'BPCK'}} , key=lambda item: (item[1], item[0]))
    output = Path(__file__).resolve().parents[1] / 'src/stellis/medicine_terms.json'
    output.write_text(json.dumps({'source':'https://rxnav.nlm.nih.gov/REST/RxTerms/allconcepts.json',
                                  'terms':terms}, ensure_ascii=False, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    main()
