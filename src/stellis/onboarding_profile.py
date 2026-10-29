"""Personal setup answers from the About you form, projected into the intake notes."""
import datetime
import re
import threading

from stellis import auth, goals as goal_model, intake, intake_library, onboarding, health_preferences, health_catalogue

CYCLE_GOAL = "Menstrual health and cycle tracking"
INTERESTS = [
    {"id": "wellness", "label": "General wellness", "note": "General wellness and preventive care"},
    {"id": "nutrition", "label": "Nutrition", "note": "Nutrition"},
    {"id": "fitness", "label": "Movement", "note": "Fitness and movement"},
    {"id": "sleep", "label": "Sleep", "note": "Sleep"},
    {"id": "emotional-wellbeing", "label": "Emotional wellbeing", "note": "Emotional wellbeing"},
    {"id": "pregnancy", "label": "Pregnancy", "note": "Pregnancy"},
    {"id": "sexual-health", "label": "Sexual health", "note": "Sexual health"},
    {"id": "dental-care", "label": "Dental care", "note": "Dental care"},
]

def focus_choices():
    catalogue = health_catalogue.configuration()
    # Areas are the catalogue's families; Stellis creates the notebooks inside them.
    return [{'id': c['collection_id'], 'label': c['display_name'], 'group': c['group']}
            for c in catalogue['collections']]


FIELDS = [
    {"id": "name", "question": "First, what should I call you?", "kind": "text"},
    {"id": "gender", "question": "How do you describe your gender?", "kind": "text",
     "choices": ["Woman", "Man", "Non-binary", "Prefer not to say"]},
    {"id": "cycle_tracking", "question": "If you have a period, are you interested in starting cycle tracking?", "kind": "choice",
     "choices": ["Yes", "No", "I don't have a period", "Maybe later"]},
    {"id": "birth_date", "question": "When is your birth date?", "kind": "date"},
    {"id": "conditions", "question": "Anything you're managing?", "hint": "Conditions, or where you are now",
     "kind": "long", "library": "conditions", "choices": ["None that I know of"],
     "suggestions": [*intake_library.LIFE_STAGES, *intake_library.COMMON_CONDITIONS]},
    {"id": "medicines", "question": "Do you take any medicines or supplements?", "kind": "long",
     "library": "medicines", "choices": ["None"], "suggestions": list(intake_library.COMMON_MEDICINES)},
    # Every goal is measurable: tapped with a default target the person can change (owner, 2026-10-28).
    {"id": "goals", "question": "Any goals to start with?", "hint": "Choose any, then set the amount and days",
     "kind": "goals", "choices": intake_library.goal_choices()},
    {"id": "focus", "question": "Which other areas would you like to keep track of through Stellis?", "hint": "Choose up to 5", "kind": "multiple",
     "max_selections": 5, "choices": focus_choices()},
]
BY_ID = {field["id"]: field for field in FIELDS}
# Retain older confirmed answers and API clients without turning interests into diagnoses.
BY_ID['period'] = {'id':'period', 'question':'Do you have a period?', 'kind':'choice', 'choices':['Yes','No','Not currently','Prefer not to say']}
BY_ID['interests'] = {'id':'interests', 'question':'Which other areas interest you?', 'kind':'multiple', 'choices':INTERESTS}
def fields(profile):
    return FIELDS


def status():
    state = onboarding._read()
    profile = dict(state.get("profile", {}))
    if isinstance(profile.get("focus"), list):
        profile["focus"] = list(dict.fromkeys(health_catalogue.family_id(value) or value for value in profile["focus"]))
    preference = health_preferences.preferences()["experiences_periods"]
    if preference is not None:
        profile["period"] = "Yes" if preference else "No"
    return {"answers": profile, "fields": fields(profile),
            "step": {"interests":"focus", "period":"cycle_tracking"}.get(state.get("profile_step"), state.get("profile_step", "name")), "complete": state.get("profile_completed", False)}


# The form saves each answer as it changes, so saves can overlap; each one rewrites the intake notes
# against their revision, and the second of two overlapping saves would be refused.
_SAVE_LOCK = threading.Lock()


def save(account_id, field, value):
    """Save only an explicitly confirmed field in the account's setup state."""
    with _SAVE_LOCK:
        return _save(account_id, field, value)


def _save(account_id, field, value):
    onboarding._require(account_id)
    if field not in BY_ID:
        raise ValueError("Choose a listed setup field")
    definition = BY_ID[field]
    if value is not None:
        if field == "focus":
            allowed = {item['id'] for item in focus_choices()}
            if isinstance(value, list):
                value = [(health_catalogue.family_id(item) or item) if isinstance(item,str) else item for item in value]
            if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
                raise ValueError("Choose listed areas of interest")
            value = list(dict.fromkeys(value))
            if len(value) > 5:
                raise ValueError("Choose up to 5 areas of interest")
        elif field == "goals":
            value = _goal_targets(value)
        elif field == "interests":
            if not isinstance(value, list) or any(item not in {i["id"] for i in INTERESTS} for item in value):
                raise ValueError("Choose listed interests")
            value = list(dict.fromkeys(value))
        elif not isinstance(value, str) or len(value) > (2000 if definition["kind"] == "long" else 100):
            raise ValueError("Enter a shorter answer")
        else:
            value = value.strip()
            if definition["kind"] == "choice" and value not in definition["choices"]:
                raise ValueError("Choose a listed answer")
            if field == "birth_date" and value:
                try:
                    date = datetime.date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("Enter a valid birth date") from exc
                if date > datetime.date.today() or date.year < 1900:
                    raise ValueError("Enter a birth date between 1900 and today")
    with onboarding._LOCK:
        state = onboarding._read()
        profile = state.setdefault("profile", {})
        profile[field] = value
        sequence = [f["id"] for f in fields(profile)]
        if field in sequence:
            following = sequence[sequence.index(field) + 1] if sequence.index(field) + 1 < len(sequence) else "complete"
            # Changing an earlier answer returns to where the person was, rather than asking the rest again.
            current = state.get("profile_step")
            order = [*sequence, "complete"]
            if current in order and order.index(current) > order.index(following):
                following = current
            state["profile_step"] = following
        else:
            state["profile_step"] = "birth_date" if field == "period" else "complete"
        state["profile_completed"] = state["profile_step"] == "complete"
        onboarding._save(state)
    if field == "period":
        # Share the tracker visibility preference; gender does not establish period status.
        health_preferences.save({"Yes": True, "No": False}.get(value))
    if field == 'cycle_tracking':
        if value == 'Yes':
            health_preferences.save(True)
        elif value == "I don't have a period":
            health_preferences.save(False)
    if field == "name" and value:
        current = auth.profile(account_id)
        auth.update_profile(value, current["email"], account_id)
    _seed_notes(profile)
    return status()


def _goal_targets(value):
    """Checks chosen goals: each a listed goal ID, alone or with its amount and days a week.

    Returns:
        One target per goal: ``id``, ``activity``, ``amount`` and ``times_per_week``.

    Raises:
        ValueError: A goal is not listed, or its amount or days are outside the goal's choices.
    """
    choices = {choice["id"]: choice for choice in BY_ID["goals"]["choices"]}
    if not isinstance(value, list):
        raise ValueError("Choose goals from the list")
    targets = {}
    for item in value:
        item = {"id": item} if isinstance(item, str) else item
        choice = choices.get(item.get("id")) if isinstance(item, dict) else None
        if choice is None:
            raise ValueError("Choose goals from the list")
        fields = {"activity": choice["activity"], "amount": item.get("amount", choice["amount"]),
                  "times_per_week": item.get("times_per_week", choice["days"])}
        goal_model.draft(fields)
        targets[choice["id"]] = {"id": choice["id"], **fields}
    return list(targets.values())


def goal_lines(value):
    """Saved goals as sentences with their targets, such as "Walk 20 minutes, 4 days a week"."""
    lines = []
    for item in value if isinstance(value, list) else []:
        try:
            lines.append(goal_model.sentence(_goal_targets([item])[0]))
        except ValueError:
            continue  # A goal saved before goals were measurable has no target to show.
    return lines


def goal_templates(value):
    """Notebook template for each saved goal's sentence, for goals that belong in one."""
    templates = {choice["id"]: choice["template_id"] for choice in BY_ID["goals"]["choices"]}
    return [(goal_model.sentence(target), templates[target["id"]])
            for target in saved_goal_targets(value) if templates[target["id"]]]


def saved_goal_targets(value):
    """Saved goal targets, or none when they were saved before goals were measurable."""
    try:
        return _goal_targets(value)
    except ValueError:
        return []


def _seed_notes(profile):
    """Project confirmed setup answers into editable intake notes for review."""
    state = intake.load()
    setup = onboarding._read()
    previous = setup.get("profile_note_values", {})
    existing = {section["heading"]: [item for item in section["items"]
                                    if item not in previous.get(section["heading"], [])]
                for section in state["notes"]}
    interest_notes = [i["note"] for i in INTERESTS if i["id"] in (profile.get("interests") or [])]
    def lines(value):
        return [line.strip()[:intake.MAX_ITEM] for line in re.split(r"[\n;]", value or "") if line.strip()]
    focus_notes = [item['label'] for item in focus_choices() if item['id'] in {health_catalogue.family_id(value) for value in (profile.get('focus') or [])}]
    additions = {"Goals": [*goal_lines(profile.get("goals")), *interest_notes, *focus_notes],
                 "Conditions": lines(profile.get("conditions")), "Medicines": lines(profile.get("medicines"))}
    if profile.get("cycle_tracking") == "Yes":
        additions["Goals"].append(CYCLE_GOAL)
    for heading, values in additions.items():
        for value in values:
            if value and value not in existing.get(heading, []):
                existing.setdefault(heading, []).append(value)
    intake.save_notes([{"heading": key, "items": values} for key, values in existing.items()], state["notes_revision"])
    with onboarding._LOCK:
        setup = onboarding._read()
        setup["profile_note_values"] = additions
        onboarding._save(setup)
