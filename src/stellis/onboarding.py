"""Durable first-account setup; proposals become workspace objects only after review."""
from __future__ import annotations

import asyncio
import json
import threading
from stellis import agents, agent_runtime, auth, collections, notebooks, pages, runtime_profile, runtime_settings, storage

_LOCK = threading.Lock()
USES = [
    {"id":"meetings", "name":"Meetings", "icon":"microphone", "collection":"Work", "notebook":"Meetings", "description":"Keep recordings, notes and to-dos together."},
    {"id":"learning", "name":"Learning", "icon":"notebook", "collection":"Learning", "notebook":"Learning", "description":"Learn a topic one step at a time with a tutor."},
    {"id":"projects", "name":"Projects", "icon":"folder", "collection":"Work", "notebook":"Projects", "description":"Give a project its own notes, sources and agents."},
    {"id":"home", "name":"Home projects", "icon":"folder-plus", "collection":"Home", "notebook":"Home projects", "description":"Keep plans, decisions and next steps in one place."},
    {"id":"family", "name":"Family", "icon":"calendar", "collection":"Home", "notebook":"Family", "description":"Organize shared plans and the details you want to remember."},
    {"id":"research", "name":"Deep research", "icon":"search", "collection":"Work", "notebook":"Research", "description":"Ask an agent to investigate and cite its sources."},
    {"id":"slides", "name":"Slide decks", "icon":"layout-dashboard", "collection":"Work", "notebook":"Presentations", "description":"Turn notebook sources into a first slide deck."},
]


def _read():
    with storage.connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS onboarding_setup (id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL)")
        row = conn.execute("SELECT state_json FROM onboarding_setup WHERE id=1").fetchone()
    state = json.loads(row[0]) if row else {"step":"welcome", "uses":[], "ready":None, "created":[]}
    # Saved setups can name a use that a later release removed.
    known = {entry["id"] for entry in USES}
    state["uses"] = [item for item in state.get("uses", []) if item in known]
    return state


def _save(state):
    with storage.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO onboarding_setup VALUES (1, ?)", (json.dumps(state),))


def available(account_id):
    return auth.needs_onboarding(account_id) or bool(_read().get("editing"))


def reopen(account_id):
    with _LOCK:
        state = _read()
        if not auth.needs_onboarding(account_id) and not state.get("editing"):
            state.update(step="welcome", editing=True, uses=[], task_choices={}, scan=None, notebook_choices=None)
            _save(state)
            from stellis import intake
            intake.reset()
    return status(account_id)


def _require(account_id):
    if not available(account_id):
        raise ValueError("This account has already finished setup")


def status(account_id):
    from stellis import health_catalogue, health_preferences
    state = _read()
    defaults = {book['name'].lower():book['todo_enabled'] for book in notebooks.list_notebooks()}
    inherited = {group['name'].lower():group['todo_list']=='on' for group in collections.list_collections()}
    return {**state, "health_preferences":health_preferences.preferences(), "health_catalogue":health_catalogue.configuration(), "task_defaults":defaults, "collection_task_defaults":inherited, "required":available(account_id), "catalog":USES,
            "proposals":[item for item in USES if item["id"] in state["uses"]], "runtime":runtime_settings.status(),
            "managed":runtime_profile.shared_provider_keys()}


def choose(account_id, uses, step, task_choices=None, notebook_choices=None):
    _require(account_id)
    if any(item not in {entry["id"] for entry in USES} for item in uses):
        raise ValueError("Choose a listed use")
    if task_choices is not None:
        if not isinstance(task_choices, dict) or len(task_choices) > 32 or any(
                not isinstance(key, str) or not 1 <= len(key) <= 100 or type(value) is not bool
                for key, value in task_choices.items()):
            raise ValueError("Choose whether each notebook suggests tasks")
    if notebook_choices is not None:
        if len(json.dumps(notebook_choices)) > 200_000:
            raise ValueError("The notebook selection is too large")
    with _LOCK:
        state = _read(); state.update(uses=list(dict.fromkeys(uses)),step=step)
        if task_choices is not None:
            state['task_choices'] = dict(task_choices)
        if notebook_choices is not None:
            state['notebook_choices'] = notebook_choices
        _save(state)
    return status(account_id)


async def check(account_id):
    _require(account_id)
    route = await asyncio.to_thread(runtime_settings.check)
    observed = route["observed"]
    if observed["inference"]["state"] != "available" or not route["agent_configured"]:
        raise ValueError("The inference model or agent runtime is unavailable. Configure this deployment, then check again.")
    if route["local_inference"] and observed["agent"]["state"] != "reported":
        raise ValueError("The managed agent is not using the configured local model. Check its route in Settings.")
    default = next(item for item in agents.list_agents() if item["is_default"])
    reply = await agent_runtime.ask(default["id"], "This is a connection check during first setup. Reply with one short sentence saying you are ready to help.")
    with _LOCK:
        state = _read(); state["ready"] = {"checked_at":observed["checked_at"], "model":route["agent_model"], "local":route["local_inference"], "job_id":reply["job_id"]}; _save(state)
    return status(account_id)


MAX_RECORD_GROUPS = 100
HEALTH_COLLECTION = "Health"


def _check_record_groups(record_groups):
    """Checks the reviewed notebooks proposed from the person's records."""
    if len(record_groups) > MAX_RECORD_GROUPS:
        raise ValueError(f"Choose at most {MAX_RECORD_GROUPS} notebooks")


def complete(account_id, proposal_id=None, group_ids=None, record_groups=None):
    """Finishes setup: files the reviewed record groups into notebooks.

    Args:
        account_id: The account finishing setup.
        proposal_id: A reviewed calendar proposal, or None.
        group_ids: The calendar groups the person kept.
        record_groups: Notebooks proposed from the intake call and the
            person's records, each with ``notebook_name``, ``notebook_id``,
            ``items`` ({kind, id}),
            ``suggest_tasks`` and an optional ``goal``, which becomes a to-do
            on the notebook's Goals page.
    """
    record_groups = record_groups or []
    _check_record_groups(record_groups)
    if not available(account_id):
        return {"complete":True, "created":_read()["created"]}
    with _LOCK:
        state = _read()
        if state["step"] != "review":
            raise ValueError("Review your notebooks before finishing setup")
        if (state.get("scan") or {}).get("state") in {"reading","planning"}:
            raise ValueError("Wait for reading to finish or stop it before setup")
        from stellis import onboarding_sources
        imported = onboarding_sources.reviewed(proposal_id, group_ids or [])
        created = []
        chosen = [item for item in USES if item["id"] in state["uses"]]
        chosen += [{"id":g["id"],"notebook":g["name"],"collection":g["collection"],"sources":g["sources"]} for g in imported]
        selected_policies = {}
        for item in chosen:
            if item['id'] in state.get('task_choices', {}):
                name = item['notebook'].casefold()
                setting = state['task_choices'][item['id']]
                if name in selected_policies and selected_policies[name] != setting:
                    raise ValueError("Choose the same task setting for repeated notebook names")
                selected_policies[name] = setting
        for item in chosen:
            collection = next((c for c in collections.list_collections() if c["name"].casefold()==item["collection"].casefold()),None)
            if collection is None: collection = collections.create(item["collection"])
            book = next((b for b in notebooks.list_notebooks() if b["name"].casefold()==item["notebook"].casefold()),None)
            if book is None:
                book = notebooks.create(item["notebook"])
                collections.assign_notebook(book["id"],collection["id"])
            if item['notebook'].casefold() in selected_policies:
                notebooks.update(book['id'], todo_list='on' if selected_policies[item['notebook'].casefold()] else 'off')
            if item.get("sources"):
                onboarding_sources.file_sources(book["id"],item["sources"])
            created.append({"notebook_id":book["id"], "name":book["name"]})
        from stellis import health_catalogue, notebook_sources
        # Validate the complete selection before creating any notebook.
        for group in record_groups:
            if group.get("notebook_id"):
                if not notebooks.exists(group["notebook_id"]):
                    raise LookupError("Notebook not found")
            elif group.get("template_id"):
                if health_catalogue.template(group["template_id"]) is None:
                    raise ValueError("Choose a listed notebook template")
            else:
                notebooks._name(group.get("notebook_name", ""))
                if health_catalogue.collection(group.get("collection_id")) is None:
                    raise ValueError("Choose a listed health collection")
        new_groups = [g for g in record_groups if not g.get("notebook_id")]
        selected = iter(health_catalogue.activate([{
            "template_id":g.get("template_id"), "collection_id":g.get("collection_id"),
            "name":g.get("notebook_name", ""), "description":g.get("description", "")
        } for g in new_groups]) if new_groups else [])
        from stellis import goals, home_focus, onboarding_profile
        topics = home_focus.topic_labels()
        # Setup goals become tracked goals with their targets, rather than to-do text on a notebook.
        targets = onboarding_profile.saved_goal_targets(state.get("profile", {}).get("goals"))
        tracked = {goals.sentence(target) for target in targets}
        for group in record_groups:
            identifier = group.get("notebook_id") or next(selected)["id"]
            if group.get('notebook_id') and group.get('collection_changed'):
                collections.assign_notebook(identifier, group.get('collection_id'))
            book = notebooks.update(identifier, todo_list="on" if group.get("suggest_tasks") else "off")
            linked, failed = 0, []
            for item in group.get("items", []):
                prefix = {"recording":"conversation", "document":"document", "note":"page"}.get(item["kind"])
                if prefix is None:
                    raise ValueError("Choose an existing record")
                try:
                    notebook_sources.link(identifier, prefix+":"+item["id"])
                    linked += 1
                except (LookupError, ValueError) as exc:
                    failed.append({**item, "error":str(exc)})
            goal = " ".join(group.get("goal", "").split())
            # A topic name such as "Pregnancy" is the notebook's subject, not a goal to work on.
            if goal and goal.casefold() not in topics and goal not in tracked:
                pages.create("Goals", identifier, f"- [ ] {goal}\n")
            created.append({"notebook_id":identifier,"name":book["name"],"filed":linked,"failed":failed})
        for target in targets:
            goals.create({key: target[key] for key in ("activity", "amount", "times_per_week")})
        agents.list_agents()  # Stellis answers in each new notebook.
        state.update(step="complete",created=created,editing=False); _save(state)
        auth.complete_onboarding(account_id)
    return {"complete":True, "created":created}
