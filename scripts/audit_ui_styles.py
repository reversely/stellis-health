"""Check component CSS for embedded palettes and report shared-token adoption.

The theme owns colour literals, including semantic chart/media palettes. Vendor
assets and data-selected notebook/calendar colours are outside this CSS audit.
"""
from pathlib import Path
import argparse
import json
import re

STATIC = Path(__file__).resolve().parents[1] / 'src' / 'stellis' / 'static'
COLOUR = re.compile(r'#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\([^)]*\)|(?<=:)\s*(?:white|black)\b')


def audit(directory=STATIC):
    files = []
    for path in sorted([*directory.glob('*.css'), *directory.glob('dash-measurements/*.css')]):
        text = re.sub(r'/\*.*?\*/', lambda match: '\n'*match[0].count('\n'), path.read_text(), flags=re.S)
        literals = [] if path.name == 'theme.css' else [
            {'line': text.count('\n', 0, match.start())+1, 'value': match[0].strip()}
            for match in COLOUR.finditer(text)]
        radii = [] if path.name == 'theme.css' else [
            {'line': text.count('\n', 0, match.start())+1, 'value': match[0]}
            for match in re.finditer(r'border-radius:\s*[^;{}]*\b[1-9]\d*px\b', text)]
        files.append({'file': str(path.relative_to(directory)), 'palette_literals': literals, 'radius_literals': radii,
                      'spacing_roles': len(re.findall(r'var\(--space-', text)),
                      'control_roles': len(re.findall(r'var\(--(?:control|action|secondary|radius|focus)-', text))})
    return {'files': files, 'violations': sum(len(item['palette_literals'])+len(item['radius_literals']) for item in files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    options = parser.parse_args()
    result = audit()
    if options.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result['files']:
            for literal in item['palette_literals']+item['radius_literals']:
                print(f"{item['file']}:{literal['line']}: move {literal['value']} into a theme role")
        print(f"{len(result['files'])} stylesheets, {result['violations']} component palette/corner literals; "
              f"{sum(f['spacing_roles'] for f in result['files'])} shared spacing references")
    raise SystemExit(bool(result['violations']))
