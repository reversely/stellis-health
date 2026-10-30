"""Versioned health catalogue and evidence-backed notebook recommendations."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
import uuid

from stellis import notebooks, storage


@lru_cache(maxsize=1)
def configuration():
    """Loads the product catalogue without inference or network access."""
    return json.loads(Path(__file__).with_name('health_catalogue.json').read_text())


def collection(identifier):
    """Resolves one registered collection ID."""
    return next((c for c in configuration()['collections'] if c['collection_id'] == identifier), None)


def canonical_id(identifier):
    """Resolves a retired template ID to the template that replaced it."""
    if not isinstance(identifier, str):
        return None
    return configuration().get('template_aliases', {}).get(identifier, identifier)


def family_id(identifier):
    """The collection (family) a template or collection ID belongs to, or None."""
    if collection(identifier):
        return identifier
    entry = template(identifier)
    return entry['collection_id'] if entry else None


def family_templates(identifier):
    """A family's notebook templates, in catalogue order."""
    return [t for t in configuration()['templates'] if t['collection_id'] == identifier]


def template(identifier):
    """Resolves one registered notebook template ID."""
    identifier = canonical_id(identifier)
    return next((t for t in configuration()['templates'] if t['template_id'] == identifier), None)


def ensure_collection(conn, identifier):
    """Creates an approved collection only when a notebook needs it."""
    entry = collection(identifier)
    if entry is None:
        raise ValueError('Choose a listed health collection')
    row = conn.execute('SELECT id FROM collections WHERE taxonomy_id = ?', (identifier,)).fetchone()
    if row:
        return row['id']
    # Adopt an exact legacy group rather than create a conflicting display name.
    row = conn.execute('SELECT id FROM collections WHERE normalized = ?', (entry['display_name'].casefold(),)).fetchone()
    if row:
        conn.execute('UPDATE collections SET taxonomy_id = ? WHERE id = ?', (identifier, row['id']))
        return row['id']
    conn.execute('INSERT INTO collections (id,name,normalized,color,created_at,taxonomy_id) VALUES (?,?,?,?,?,?)',
                 (identifier, entry['display_name'], entry['display_name'].casefold(), notebooks.PALETTE[0], notebooks._now(), identifier))
    return identifier


def activate(selections):
    """Creates user-confirmed notebooks atomically and preserves existing titles."""
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
        raise ValueError('Choose between 1 and 100 notebooks')
    evidence = {g['template_id']:g['evidence'] for g in recommend()['groups']}
    results = []
    with storage.connect() as conn:
        for selection in selections:
            tid = selection.get('template_id')
            entry = template(tid) if tid else None
            if entry: tid = entry['template_id']
            if tid and entry is None:
                raise ValueError('Choose a listed notebook template')
            cid = entry['collection_id'] if entry else selection.get('collection_id')
            parent = ensure_collection(conn, cid)
            title = entry['display_name'] if entry else notebooks._name(selection.get('name', ''))
            description = entry['description'] if entry else str(selection.get('description', '')).strip()
            if len(description) > 1000:
                raise ValueError('Keep the description under 1000 characters')
            if tid:
                identities = [tid, *[alias for alias, target in configuration().get('template_aliases', {}).items() if target == tid]]
                marks = ','.join('?' for _ in identities)
                existing = conn.execute(f'SELECT id,name,collection_id FROM notebooks WHERE template_id IN ({marks}) ORDER BY CASE WHEN template_id=? THEN 0 ELSE 1 END,created_at,id LIMIT 1', (*identities,tid)).fetchone()
            else:
                existing = conn.execute('SELECT id,name,collection_id FROM notebooks WHERE normalized = ?', (title.casefold(),)).fetchone()
            if existing is None and tid:
                existing = conn.execute('SELECT id,name,collection_id FROM notebooks WHERE normalized = ? AND template_id IS NULL',
                                        (title.casefold(),)).fetchone()
                if existing:
                    # Explicit selection adopts an existing workspace without copying its records.
                    conn.execute('UPDATE notebooks SET template_id = ?, catalogue_version = ?, collection_id = COALESCE(collection_id, ?) WHERE id = ?',
                                 (tid, configuration()['version'], parent, existing['id']))
                    existing = conn.execute('SELECT id,name,collection_id FROM notebooks WHERE id = ?', (existing['id'],)).fetchone()
            if existing:
                # Re-selecting an archived template restores it without changing organization.
                conn.execute('UPDATE notebooks SET archived_at = NULL WHERE id = ?', (existing['id'],))
                results.append(dict(existing))
                continue
            if conn.execute('SELECT 1 FROM notebooks WHERE normalized = ?', (title.casefold(),)).fetchone():
                raise ValueError('A notebook with this name already exists')
            identifier = str(uuid.uuid4())
            conn.execute('INSERT INTO notebooks (id,name,normalized,color,created_at,collection_id,template_id,description,catalogue_version,recommendation_evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (identifier,title,title.casefold(),notebooks.PALETTE[len(results) % len(notebooks.PALETTE)],notebooks._now(),parent,tid,description,configuration()['version'],json.dumps(evidence.get(tid,[]))))
            results.append({'id':identifier,'name':title,'collection_id':parent})
    return results


def view():
    """Returns catalogue configuration plus account-specific selection status."""
    with storage.connect() as conn:
        selected = {canonical_id(row['template_id']): row['id'] for row in conn.execute('SELECT id,template_id FROM notebooks WHERE template_id IS NOT NULL AND archived_at IS NULL')}
        archived = [dict(row) for row in conn.execute('SELECT id,name FROM notebooks WHERE archived_at IS NOT NULL ORDER BY name')]
    return {**configuration(), 'templates':[{**entry,'notebook_id':selected.get(entry['template_id'])} for entry in configuration()['templates']], 'archived':archived}


def _matches(text, concept):
    return re.search(r'(?<!\w)' + re.escape(concept) + r'(?!\w)', text, re.I) is not None


def recommend():
    """Matches current goals and care notes to templates with literal evidence.

    Existing intake inference extracts the editable notes. This conservative
    matcher adds no provider call and leaves the full catalogue available.
    """
    from stellis import intake, sorting
    state = intake.load()
    items = sorting.unsorted()[:sorting.MAX_ITEMS]
    notes = state.get('notes', '')
    if isinstance(notes, list):
        notes = '\n'.join('## '+str(x.get('heading',''))+'\n'+'\n'.join(x.get('items',[])) for x in notes if isinstance(x,dict))
    heading = ''
    evidence = []
    for line in str(notes).splitlines():
        if line.startswith('#'):
            heading = line.lstrip('# ').strip().casefold()
            continue
        quote = line.strip(' -*\t')
        if not quote or heading == 'to confirm':
            continue
        if re.search(r'\b(history of|historical|previously|resolved|no longer|family history|mother|father|denies|ruled out|no|not|possible|suspected|unconfirmed)\b', quote, re.I):
            continue
        priority = 3 if heading == 'goals' else 2 if heading in {'conditions','care team','reminders'} else 0
        if priority:
            evidence.append((priority, quote[:400], {'kind':'intake','id':'notes','quote':quote[:400]}, None))
    # A setup goal names its own notebook, since "Walk 20 minutes" names no clinical concept.
    from stellis import onboarding, onboarding_profile
    saved_goals = onboarding._read().get('profile', {}).get('goals')
    # Only a measurable goal is ever a notebook's goal; cycle tracking or an area is just its reason.
    measurable = set(onboarding_profile.goal_lines(saved_goals))
    goal_of = lambda hits: next((q for p,q,e in hits if q in measurable), '')
    for sentence, template_id in onboarding_profile.goal_templates(saved_goals):
        evidence.append((3, sentence, {'kind':'intake','id':'notes','quote':sentence}, template_id))
    # The answers propose notebooks first; records the answers don't place propose theirs below.
    groups = []
    books = notebooks.list_notebooks()
    from stellis import collections
    parents = {c['id']:c for c in collections.list_collections()}
    for entry in configuration()['templates']:
        hits = [(p,q,e) for p,q,e,named in evidence
                if named == entry['template_id'] or (named is None and any(_matches(q,c) for c in entry['clinical_concepts']))]
        if not hits:
            continue
        hits.sort(key=lambda h:-h[0])
        matched = [item.public() for item in items if any(_matches(item.title+' '+item.excerpt,c) for c in entry['clinical_concepts'])]
        existing = next((b for b in books if canonical_id(b.get('template_id')) == entry['template_id']),None)
        parent = parents.get(existing['collection_id']) if existing else None
        groups.append({'template_id':entry['template_id'],'collection_id':(parent.get('taxonomy_id') or parent['id']) if parent else entry['collection_id'],
                       'collection_name':parent['name'] if parent else collection(entry['collection_id'])['display_name'],
                       'notebook_name':existing['name'] if existing else entry['display_name'],
                       'notebook_id':existing['id'] if existing else None,
                       'reason':hits[0][1], 'evidence':[h[2] for h in hits[:3]],
                       'goal':goal_of(hits), 'items':matched,
                       'kind':'objective' if hits[0][0] == 3 else 'condition',
                       'named_by_person':False,'problem':'','priority':hits[0][0]})
    # A family's general notebook gives way to its specific ones when they cover every reason it matched:
    # High Blood Pressure proposes Blood Pressure alone, not Heart Care beside it.
    general = {family_templates(f['collection_id'])[0]['template_id'] for f in configuration()['collections']}
    def uncovered(group):
        family = template(group['template_id'])['collection_id']
        specific = {e['quote'] for other in groups if other['template_id'] not in general
                    and template(other['template_id'])['collection_id'] == family for e in other['evidence']}
        # Naming the family itself asks for a notebook there, which a specific one already gives.
        if specific:
            specific.add(collection(family)['display_name'])
        return [e for e in group['evidence'] if e['quote'] not in specific]
    kept = []
    for group in groups:
        if group['template_id'] in general:
            left = uncovered(group)
            if not left:
                continue
            # Kept for what the specific notebooks don't cover, so that is its reason.
            group = {**group, 'reason': left[0]['quote'], 'evidence': left}
        kept.append(group)
    groups = kept
    # A family named in the goals with no notebook of its own yet starts with its first template.
    proposed = {g['collection_id'] for g in groups}
    for family in configuration()['collections']:
        if family['collection_id'] in proposed:
            continue
        hits = [(p,q,e) for p,q,e,named in evidence if named is None and _matches(q, family['display_name'])]
        if not hits:
            continue
        entry = family_templates(family['collection_id'])[0]
        existing = next((b for b in books if canonical_id(b.get('template_id')) == entry['template_id']),None)
        groups.append({'template_id':entry['template_id'],'collection_id':family['collection_id'],
                       'collection_name':family['display_name'],
                       'notebook_name':existing['name'] if existing else entry['display_name'],
                       'notebook_id':existing['id'] if existing else None,
                       'reason':hits[0][1], 'evidence':[h[2] for h in hits[:3]],
                       'goal':goal_of(hits), 'items':[],
                       'kind':'objective' if hits[0][0] == 3 else 'condition',
                       'named_by_person':False,'problem':'','priority':hits[0][0]})
    # A record the answers don't place files by what it says it is (owner, 2026-10-30: "it's literally in
    # the name"): its title first, then its first page; the longest term wins and, on a tie, the specific
    # notebook over its family's general one. It joins that notebook's group or proposes the notebook.
    placed = {(i['kind'],i['id']) for g in groups for i in g['items']}
    by_template = {g['template_id']:g for g in groups}
    for item in items:
        if (item.kind,item.id) in placed:
            continue
        best = None
        for rank, text in ((2, item.title), (1, item.excerpt)):
            for entry in configuration()['templates']:
                found = [c for c in entry['clinical_concepts'] if _matches(text, c)]
                if found:
                    score = (rank, max(map(len, found)), entry['template_id'] not in general)
                    if best is None or score > best[0]:
                        best = (score, entry)
            if best:
                break
        if not best:
            continue
        entry = best[1]
        group = by_template.get(entry['template_id'])
        if group is None:
            existing = next((b for b in books if canonical_id(b.get('template_id')) == entry['template_id']),None)
            parent = parents.get(existing['collection_id']) if existing else None
            group = {'template_id':entry['template_id'],'collection_id':(parent.get('taxonomy_id') or parent['id']) if parent else entry['collection_id'],
                     'collection_name':parent['name'] if parent else collection(entry['collection_id'])['display_name'],
                     'notebook_name':existing['name'] if existing else entry['display_name'],
                     'notebook_id':existing['id'] if existing else None,
                     'reason':item.title, 'evidence':[{'kind':item.kind,'id':item.id,'quote':item.title}],
                     'goal':'', 'items':[], 'kind':'record',
                     'named_by_person':False,'problem':'','priority':1}
            groups.append(group)
            by_template[entry['template_id']] = group
        group['items'].append(item.public())
    groups.sort(key=lambda g:-g['priority'])
    groups = groups[:8]
    used = {(i['kind'],i['id']) for g in groups for i in g['items']}
    return {'groups':groups,'unsorted':[i.public() for i in items if (i.kind,i.id) not in used], 'question':'','reply':''}


async def file_unsorted(result):
    """Files the records ``recommend`` left over with Stellis's sorting model, as Records does.

    Setup should file every record it can by itself (owner, 2026-10-30: "This should be automatic ...
    using inference too for filing"). Each record joins a proposed notebook or proposes a catalogue
    one. When the model is unavailable or unreadable the records stay under Not filed.
    """
    from stellis import agent_gateway, sorting
    left = {(i['kind'], i['id']) for i in result['unsorted']}
    items = [i for i in sorting.unsorted() if (i.kind, i.id) in left]
    if not items:
        return result
    groups = result['groups']
    books = notebooks.list_notebooks()
    names = [f"{g['notebook_name']} ({g['collection_name']})" for g in groups]
    names += [str(b['name']) for b in books if str(b['name']).casefold() not in {g['notebook_name'].casefold() for g in groups}]
    families = [{'family': c['display_name'], 'usual_notebooks': [t['display_name'] for t in family_templates(c['collection_id'])]}
                for c in configuration()['collections']]
    prompt = sorting.build_prompt(items, names, families)
    ids = {str(b['name']).casefold(): str(b['id']) for b in books}
    try:
        reply, _ = await agent_gateway.turn(prompt, 'sorting')
        filed = sorting.place(sorting.parse_groups(reply, items, ids))
    except (agent_gateway.GatewayError, sorting.SortingError):
        return result
    for proposal in filed:
        name = proposal['notebook_name'].casefold()
        group = next((g for g in groups if g['notebook_name'].casefold() == name
                      or (proposal.get('template_id') and g['template_id'] == proposal['template_id'])), None)
        if group is None:
            if len(groups) >= 8:
                continue
            group = {'template_id': proposal.get('template_id'), 'collection_id': proposal['collection_id'],
                     'collection_name': proposal['collection_name'], 'notebook_name': proposal['notebook_name'],
                     'notebook_id': proposal['notebook_id'], 'reason': proposal['reason'],
                     'evidence': [{'kind': i['kind'], 'id': i['id'], 'quote': i['title']} for i in proposal['items'][:3]],
                     'goal': '', 'items': [], 'kind': 'record', 'named_by_person': False, 'problem': '', 'priority': 1}
            groups.append(group)
        group['items'].extend(proposal['items'])
    used = {(i['kind'], i['id']) for g in groups for i in g['items']}
    return {**result, 'groups': groups, 'unsorted': [i for i in result['unsorted'] if (i['kind'], i['id']) not in used]}
