"""Demo-only upload, review and one-action reset routes."""
import asyncio
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from stellis import demo_patient, storage

router = APIRouter(prefix='/demo')


def response(value, active=None):
    result = JSONResponse(value)
    if active is True:
        result.set_cookie(demo_patient.COOKIE, 'maya', httponly=True, samesite='strict', path='/')
    elif active is False:
        result.delete_cookie(demo_patient.COOKIE, path='/')
    result.headers['Cache-Control'] = 'no-store'
    return result


class StoryDate(BaseModel):
    date: str = Field(max_length=10)


@router.get('/files')
def files():
    """Maya's records and visit recordings by year, as files a visitor adds through the real upload (#258)."""
    years: dict[str, list[dict[str, str]]] = {}
    for folder in ('documents', 'recordings'):
        for path in sorted((demo_patient.STATIC / folder).rglob('*')):
            if path.suffix.lower() in {'.pdf', '.mp3'}:
                code, day, slug = path.stem.split('_', 2)
                # A recording is titled from its file name, so it gets the name a person would give it.
                name = slug.replace('-', ' ').capitalize() + '.mp3' if path.suffix.lower() == '.mp3' else path.name
                years.setdefault(day[:4], []).append({
                    'name': name, 'url': '/static/maya/' + path.relative_to(demo_patient.STATIC).as_posix(),
                    'type': 'audio/mpeg' if path.suffix.lower() == '.mp3' else 'application/pdf'})
    # Records after the snapshot's five years, added in the short demo to show an upload changing her history.
    followups = [{'name': path.name, 'url': '/static/maya/' + path.relative_to(demo_patient.STATIC).as_posix(), 'type': 'application/pdf'}
                 for path in sorted((demo_patient.STATIC / 'followups').glob('*.pdf'))]
    return {'years': years, 'followups': followups}


@router.get('/stored-answers')
def stored_answers(scope: str = Query(max_length=220)):
    """Questions about one notebook or report the demo can still answer from stored replies."""
    from stellis import privacy
    return {'questions': privacy.stored_questions(scope)}


@router.get('/story-date')
async def story_date():
    """The demo workspace's story date and the range the timeline scrub covers (#252)."""
    return await checked(demo_patient.story)


@router.put('/story-date')
async def move_story_date(request: StoryDate):
    """Moves the demo workspace to another day; pages then show it as of that day."""
    # The demo middleware updates the browser's story-clock cookie from the saved date.
    return response(await checked(lambda: demo_patient.move_story(request.date)))


@router.get('/status')
def status(request: Request):
    return {**demo_patient.status(), 'active': request.cookies.get(demo_patient.COOKIE) == 'maya' and demo_patient.existing_workspace() is not None}


@router.post('/enter')
def enter():
    """Enters an empty example workspace whose today is after Maya's last record, so her five years of
    records read as history when the visitor adds them (#258)."""
    from stellis import story_clock
    if not demo_patient.available():
        raise HTTPException(404, 'The demo is not installed on this server.')
    target = demo_patient.workspace()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if story_clock.stored(target) is None:
        story_clock.save(target, story_clock.LAST, advance=True)
    return response(demo_patient.status(), True)


@router.post('/seed')
async def seed():
    """Enters a fresh example workspace already holding Maya's five filed years (#247)."""
    try:
        result = await asyncio.to_thread(demo_patient.seed)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return response(result, True)


@router.post('/populate')
async def populate():
    """Enters the example workspace and fills it, for a tour that needs records in place."""
    demo_patient.workspace()
    with storage.demo_workspace(demo_patient.workspace()):
        result = await demo_patient.populate()
    return response(result, True)


@router.post('/leave')
def leave():
    return response({'active': False}, False)


async def cancel_pending():
    from stellis import agent_runtime, agents
    from stellis import server
    await server.cancel_demo_calls()
    pending=[]
    if demo_patient._control().exists():
        with storage.demo_workspace(demo_patient.workspace()):
            for agent in agents.list_agents():
                for job in agents.jobs(agent['id']):
                    task=agent_runtime._RUNNING_JOBS.get(job['id'])
                    if task:
                        task.cancel(); pending.append(task)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@router.delete('/history')
async def clear():
    await cancel_pending()
    demo_patient.clear()
    return response({'cleared': True}, False)


async def checked(action):
    try:
        workspace = demo_patient.existing_workspace()
        if workspace is None:
            raise ValueError('Start Maya’s demo before uploading records')
        with storage.demo_workspace(workspace):
            def synchronized():
                with demo_patient._LOCK:
                    return action()
            return await asyncio.to_thread(synchronized)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post('/upload')
async def upload(file: UploadFile = File(...)):
    if not (file.filename or '').lower().endswith('.zip'):
        raise HTTPException(422, 'Choose a yearly demo ZIP')
    content = await file.read(demo_patient.MAX_ZIP_BYTES + 1)
    return await checked(lambda: demo_patient.import_year(content))


@router.get('/years/{year}')
async def review(year: str):
    return await checked(lambda: demo_patient.review(year))


class Dates(BaseModel):
    dates: dict[str, str]


@router.post('/years/{year}/dates')
async def confirm_dates(year: str, request: Dates):
    return await checked(lambda: demo_patient.confirm_dates(year, request.dates))


@router.post('/years/{year}/sort')
async def sort(year: str):
    """Starts a round: snapshots the library, then asks Stellis for its sorting proposal."""
    from stellis import sorting
    await checked(lambda: demo_patient.begin_round(year))
    with storage.demo_workspace(demo_patient.existing_workspace()):
        try:
            return await sorting.suggest()
        except sorting.SortingError as error:
            raise HTTPException(503, str(error)) from error


class Reasons(BaseModel):
    reasons: dict[str, str] = {}


@router.post('/years/{year}/finish')
async def finish(year: str, request: Reasons):
    """Closes the year after the person has approved Stellis's proposals; results wait for review."""
    return await checked(lambda: demo_patient.finish_round(
        year, {k[:80]: v[:200] for k, v in list(request.reasons.items())[:20]}))


@router.post('/years/{year}/care')
async def start_care(year: str):
    """The person approves the year's care changes; Stellis applies them record by record."""
    return await checked(lambda: demo_patient.start_year_care(year))


@router.get('/years/{year}/care')
async def care(year: str):
    return await checked(lambda: demo_patient.care_status(year))


@router.post('/years/{year}/add')
async def add_pack(year: str):
    """One click adds the year's pack: read, date, file and extract run on their own (#258)."""
    return await checked(lambda: demo_patient.start_add_year(year))


@router.get('/years/{year}/add')
async def pack(year: str):
    return await checked(lambda: demo_patient.pack_status(year))


@router.get('/history')
async def history():
    return await checked(demo_patient.history)
