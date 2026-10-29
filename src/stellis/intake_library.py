"""Local suggestions and NLM medicine names for reviewable intake answers."""

from functools import lru_cache
import json
from pathlib import Path

from stellis import health_catalogue

ATTRIBUTION = ("This product uses publicly available data from the U.S. National Library of Medicine (NLM), "
               "National Institutes of Health, Department of Health and Human Services; NLM is not responsible "
               "for the product and does not endorse or recommend this or any other product.")
# Setup goals are measurable: an activity from goals.ACTIVITIES, a default amount and days a week the
# person can change, and the notebook template each one belongs in (owner, 2026-10-28).
GOALS = (
    ("walk", "walk", 20, 4, "fitness--fitness"),
    ("exercise", "exercise", 30, 3, "fitness--fitness"),
    ("stretch", "stretch", 10, 5, "fitness--mobility"),
    ("sleep", "sleep", 7, 5, "sleep--sleep"),
    ("produce", "produce", 5, 7, "nutrition--nutrition"),
    ("meditate", "meditate", 10, 5, "mental-health--mental-health"),
    ("blood_pressure", "blood_pressure", None, 3, "heart-circulation--blood-pressure"),
    ("vitals", "pulse", None, 2, None),
)
# Life stages sit beside conditions as taps; each names its notebook in plain words the catalogue knows.
LIFE_STAGES = ("Trying to conceive", "Pregnant", "Postpartum", "Menopause")
# Common conditions offered as taps before any search; each is a MedlinePlus condition label.
COMMON_CONDITIONS = ("High Blood Pressure", "Diabetes Type 2", "Asthma", "Anxiety", "Depression",
                     "Migraine", "Hypothyroidism", "Arthritis", "Anemia", "Allergy")
# Common supplements as plain names, since the RxNorm library lists specific products and strengths.
COMMON_MEDICINES = ("Vitamin D", "Iron", "Folic acid", "Prenatal vitamin", "Multivitamin", "Vitamin B12",
                    "Magnesium", "Omega-3")


def goal_choices():
    """Setup goal choices with their default target and the choices for amount and days."""
    from stellis import goals
    choices = []
    for key, activity, amount, days, template in GOALS:
        definition = goals.ACTIVITIES[activity]
        choices.append({"id": key, "activity": activity, "name": definition.label, "amount": amount, "days": days,
                        "template_id": template,
                        "amounts": list(definition.amounts), "unit": definition.unit,
                        "label": goals.sentence({"activity": activity, "amount": amount, "times_per_week": days})})
    return choices


@lru_cache(maxsize=1)
def medicines():
    data = json.loads(Path(__file__).with_name("medicine_terms.json").read_text())
    return [(identifier, name, name.casefold()) for identifier, name in data["terms"]]


@lru_cache(maxsize=1)
def conditions():
    return json.loads(Path(__file__).with_name('condition_terms.json').read_text())


def search(category: str, query: str):
    """Search reference names without sending the person's query to a provider."""
    words = query.casefold().split()
    if category == "goals":
        candidates = [{"id": choice["id"], "label": choice["label"], "detail": "Goal",
                       "answer": "My goal: " + choice["label"] + "."} for choice in goal_choices()]
    elif category == "conditions":
        data = conditions()
        if not words:
            return {"items": [], "total": 0, "attribution": data['attribution']}
        matched = [item for item in data['terms'] if all(word in (item['label'] + ' ' + ' '.join(item['aliases'])).casefold() for word in words)]
        return {"items": [{"id": item['id'], "label": item['label'], "detail": 'MedlinePlus condition', "url": item['url'], "answer": item['label']} for item in matched[:20]],
                "total": len(matched), "attribution": data['attribution']}
    elif category == "health":
        candidates = [{"id": t["template_id"], "label": t["display_name"],
                       "detail": t["description"], "answer": "I'd like to discuss " + t["display_name"] + "."}
                      for t in health_catalogue.configuration()["templates"]]
    elif category == "medicines":
        if not words:
            return {"items": [], "total": 0, "attribution": ATTRIBUTION}
        matched = [(identifier, name) for identifier, name, key in medicines()
                   if all(word in key for word in words)]
        matched.sort(key=lambda item: (not item[1].casefold().startswith(query.casefold()), len(item[1]), item[1]))
        return {"items": [{"id": identifier, "label": name, "detail": "RxNorm " + identifier,
                            "answer": "Medicine or supplement to discuss: " + name + "."}
                           for identifier, name in matched[:20]],
                "total": len(matched), "attribution": ATTRIBUTION}
    else:
        raise ValueError("Choose Goals, Conditions, Health topics or Medicines and supplements")
    matched = [item for item in candidates
               if all(word in (item["label"] + " " + item["detail"]).casefold() for word in words)]
    return {"items": matched[:20], "total": len(matched), "attribution": ATTRIBUTION}
