"""Notebook presentations of shared health data and registered capabilities."""
from stellis import care_events, health_facts, health_catalogue, notebook_sources, notebooks, readings, timeline

CORE = ('sources','notes','chat','search')
OPTIONAL = ('timeline','care_schedule','trends','check_ins','intervention_history')


def supported(book):
    """Returns registered features for a curated or custom notebook."""
    entry = health_catalogue.template(book.get('template_id'))
    return entry['supported_features'] if entry else list(CORE+OPTIONAL)


def context(notebook_id):
    """Projects shared clinical facts and available views for one notebook."""
    book = next((b for b in notebooks.list_notebooks() if b['id']==notebook_id),None)
    if book is None:
        raise LookupError('Notebook not found')
    import json
    requested = json.loads(book.get('capabilities_json') or 'null')
    requested = supported(book) if requested is None else list(CORE)+requested
    sources = notebook_sources.list_sources(notebook_id)
    document_ids = {s['id'].partition(':')[2] for s in sources if s['id'].startswith('document:')}
    facts = [f for f in health_facts.accepted() if f['document_id'] in document_ids]
    points = [p for f in facts if (p := readings.fact_point(f)) is not None]
    points.extend(readings.stored_points(notebook_id))
    schedule = care_events.list_all(notebook_id)
    for point in points:
        if 'unit' not in point:
            point['unit'] = readings.MEASURES[point['measure']].unit
            point['label'] = readings.MEASURES[point['measure']].label
    events = timeline.entries(notebook_id)
    requirements = {'sources':True,'notes':True,'chat':any(s['selected'] for s in sources),
                    'search':any(s['selected'] for s in sources),'timeline':bool(events),
                    'trends':bool(points),'check_ins':book['todo_enabled'],
                    'intervention_history':any(f['kind'] in {'medication','procedure','treatment'} for f in facts),
                    'care_schedule':bool(schedule)}
    active = [key for key in requested if requirements.get(key,False)]
    return {'supported_features':supported(book), 'requested_features':requested,
            'care_schedule':schedule,'active_features':active,'facts':facts,'timeline':events,'measurements':points}
