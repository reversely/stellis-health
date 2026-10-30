"""Deterministic safety gates around every model answer (S-01, owner 2026-10-30).

A prompt asks the model to behave; these checks do not ask. Before retrieval, a message that describes an
emergency, a crisis, feeling unwell after a medicine change, wanting to change a medicine, or keeping
something from a doctor gets a fixed reply and no model is called. After the model, an answer that tells
the person to start, stop or change a medicine is replaced. Replies name no phone numbers: people are not
only in Canada, and a wrong number is worse than "your local emergency number" (owner). Each decision is
recorded without its text (S-02).
"""

from __future__ import annotations

import datetime
import functools
import re
from dataclasses import dataclass

from stellis import storage

POLICY_VERSION = "2026-10-30"

EMERGENCY = "This could be an emergency. Call your local emergency number now, or ask someone to call for you."
CRISIS = ("You don't have to go through this alone. Contact a crisis line, or call your local emergency number "
          "if you're in danger right now.")
WORSE_AFTER_CHANGE = ("Feeling unwell after a medicine change needs a clinician today. Contact the person who "
                      "prescribed it or a pharmacist. If you have chest pain, trouble breathing, a seizure or "
                      "signs of a stroke, call your local emergency number.")
MEDICINE_CHANGE = ("Stellis can't advise on changing a medicine. Stopping or changing some medicines suddenly can "
                   "be dangerous. Talk to the person who prescribed it, or a pharmacist, before you change anything.")
KEEP_FROM_DOCTOR = ("Your doctor can only keep you safe with the full picture. Stellis can help you prepare "
                    "what to say.")


def _any(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


_EMERGENCY = _any(
    r"\bchest (?:pain|pressure|tightness|is tight|hurts|feels tight)",
    r"\b(?:can'?t|cannot|can not|struggling to|hard to|trouble|difficulty) breath(?:e|ing)\b",
    r"\bchoking\b",
    r"\bface (?:is )?droop", r"\barm (?:is |feels )?(?:weak|numb)\b", r"\bslurr(?:ed|ing) (?:speech|words)\b",
    r"\bnumb(?:ness)? on one side\b", r"\bsudden(?:ly)? (?:can'?t see|lost my vision|vision loss|blind)",
    r"\bworst headache\b",
    r"\b(?:i'?m|i am|she'?s|he'?s|they'?re|is|are) having a seizure\b", r"\bseizure (?:won'?t|will not|isn'?t|is not) stop",
    r"\bfirst (?:ever )?seizure\b",
    r"\b(?:passed out|won'?t wake up|will not wake up|unconscious|unresponsive)\b",
    r"\bbleeding (?:that )?(?:won'?t|will not|doesn'?t|does not) stop",
    r"\b(?:took|taken|swallowed) (?:too many|too much|an overdose|the whole (?:bottle|pack))", r"\boverdos(?:e|ed|ing)\b",
    r"\b(?:throat|tongue|lips?) (?:is |are )?(?:swelling|swollen|closing)",
)
_CRISIS = _any(
    r"\bsuicid", r"\bkill myself\b", r"\bend (?:my life|it all)\b",
    r"\b(?:don'?t|do not) want to (?:be alive|live|wake up|be here)\b", r"\bhurt(?:ing)? myself\b", r"\bself[- ]?harm",
)
# A change to a medicine: a change word with a medicine within a few words of it. Medicine words are the
# ingredient words of the bundled medicine dictionary, without product forms and everyday words.
_CHANGE = re.compile(r"\b(?:stop|stopping|stopped|quit|quitting|come off|coming off|came off|get off|go off|went off|"
                     r"wean|weaning|taper|tapering|cut (?:back|down)|cutting (?:back|down)|reduc\w*|lower\w*|"
                     r"decreas\w*|skip\w*|halv\w*|double|doubling|increas\w*|take (?:more|less|fewer|extra)|"
                     r"switch\w*|drop\w*|discontinu\w*|give up on|keep taking|continue taking)\b", re.I)
_GENERIC = {"medication", "medications", "medicine", "medicines", "meds", "med", "pill", "pills", "tablet", "tablets",
            "dose", "doses", "dosage", "prescription", "prescriptions", "mg", "supplement", "supplements",
            "antidepressant", "antidepressants", "antibiotic", "antibiotics", "inhaler", "injections", "shots",
            "steroids", "insulin", "taking"}
_NOT_MEDICINES = {"oral", "tablet", "injection", "solution", "capsule", "topical", "release", "pack", "extended",
                  "prefilled", "syringe", "injectable", "actuat", "suspension", "cream", "powder", "extract", "pollen",
                  "human", "injector", "medicated", "spray", "chewable", "ointment", "auto", "inhalation", "delayed",
                  "lotion", "nasal", "film", "patch", "drops", "liquid", "kit", "gel", "foam", "shampoo", "soap",
                  "water", "sugar", "alcohol", "protein", "salt", "food", "milk", "coffee", "caffeine", "tea", "juice",
                  "fruit", "fish", "oil", "honey", "rice", "wheat", "corn", "egg", "eggs", "beef", "pork", "chicken",
                  "cats", "dogs", "dust", "grass", "trees", "mixed", "standard", "allergenic", "plus", "free", "daily",
                  "night", "time", "care", "health", "relief", "strength", "extra", "maximum", "children", "adult",
                  "sleep", "screen", "sugar", "work", "walk", "walking", "running", "exercise", "diet", "weight"}
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)?")


@functools.lru_cache(maxsize=1)
def _medicine_words() -> frozenset[str]:
    from stellis import intake_library
    words = {word for _, _, name in intake_library.medicines() for word in re.findall(r"[a-z]{4,}", name)}
    return frozenset((words - _NOT_MEDICINES) | _GENERIC)


def _medicine_change(text: str) -> bool:
    lower = text.casefold()
    medicines = _medicine_words()
    for match in _CHANGE.finditer(lower):
        before = _WORD.findall(lower[:match.start()])[-4:]
        after = _WORD.findall(lower[match.end():])[:6]
        if any(word in medicines for word in before + after):
            return True
    return bool(re.search(r"\b(?:taper|tapering|reduction|weaning|stopping) plan\b", lower))


# Questions about what already happened are answered from the records, not gated.
_HISTORY = re.compile(r"^\s*(?:when|why|who|what (?:did|does|do|was|were)|did (?:my|the|dr)|has (?:my|the)|"
                      r"how (?:long|much|many|often) (?:did|was|have)|what did)\b", re.I)
_UNWELL = _any(r"\bunwell\b", r"\bworse\b", r"\bsick\b", r"\bdizzy\b", r"\bshaky\b", r"\bscared\b",
               r"\bnot (?:feeling|feel) (?:well|good|right|great)\b", r"\bside effects?\b", r"\bwithdrawal\b",
               r"\bfeel(?:ing)? (?:awful|terrible|bad|off)\b")
_AFTER_CHANGE = _any(r"\b(?:since|after) (?:i )?(?:stopped|started|changed|lowered|reduced|cut|quit|came off|went off|"
                     r"skipped|missed|dropped|tapered)",
                     r"\b(?:new|lower|higher|changed) dose\b", r"\btoo (?:quickly|fast|soon)\b", r"\bwithdrawal\b")
_KEEP_FROM_DOCTOR = _any(
    r"\b(?:don'?t|do not|not|never|without) (?:want to |going to )?(?:tell|telling|let|letting) (?:my|the) (?:doctor|gp|physician|psychiatrist|neurologist|prescriber|nurse|pharmacist)",
    r"\bhid(?:e|ing) (?:it|this|that) from (?:my|the) (?:doctor|gp|physician|prescriber)",
    r"\bwithout (?:my|the) (?:doctor|gp|physician|prescriber) knowing\b",
    r"\bkeep(?:ing)? (?:it|this|that) (?:a )?secret from\b",
)


@dataclass(frozen=True)
class Gate:
    name: str
    reply: str


class SafetyReply(ValueError):
    """A message the gate answered with a fixed reply; callers that show errors show this text."""

    def __init__(self, gate: Gate) -> None:
        super().__init__(gate.reply)
        self.gate = gate


def screen(text: str) -> Gate | None:
    """The gate a person's message falls under, in order of urgency, or None."""
    text = " ".join(str(text).split())
    if _EMERGENCY.search(text):
        return Gate("emergency", EMERGENCY)
    if _CRISIS.search(text):
        return Gate("crisis", CRISIS)
    if _AFTER_CHANGE.search(text) and _UNWELL.search(text):
        return Gate("worse_after_change", WORSE_AFTER_CHANGE)
    if _KEEP_FROM_DOCTOR.search(text):
        return Gate("keep_from_doctor", KEEP_FROM_DOCTOR)
    if _medicine_change(text) and not _HISTORY.search(text):
        return Gate("medicine_change", MEDICINE_CHANGE)
    return None


# An instruction to the person: an imperative at the start of a sentence, or "you can/should" and the like.
_DIRECTIVE = re.compile(r"(?:^|\byou (?:can|should|could|may|need to|must)\s+|\bit'?s (?:ok|okay|safe|fine) to\s+|"
                        r"\bfeel free to\s+|\bgo ahead and\s+)(?:stop|discontinue|reduce|lower|decrease|increase|double|"
                        r"halve|skip|taper|cut back on|cut down on|come off|quit|keep taking|continue taking)\b", re.I)
_DOSE = re.compile(r"\b(?:take|maintain|stay at|keep at|go down to|go up to)\s+\d+(?:\.\d+)?\s?(?:mg|mcg|milligrams?|"
                   r"micrograms?|units?|tablets?|pills?)\b", re.I)
_PERSIST = re.compile(r"\b(?:you must persevere|stay the course|push through)\b", re.I)
_CALM = re.compile(r"\bno need to worry\b|\bnothing to worry about\b|\bdon'?t worry\b", re.I)
_REFERRAL = re.compile(r"\bask(?:ing)? (?:your|the|a) (?:doctor|prescriber|pharmacist|clinician|nurse|physician)\b|"
                       r"\b(?:your|the) (?:doctor|prescriber|clinician|physician) (?:planned|wrote|advised|said|recommended)\b|"
                       r"\bplanned to\b", re.I)


def _mentions_medicine(text: str) -> bool:
    medicines = _medicine_words()
    return any(word in medicines for word in _WORD.findall(text.casefold()))


def unsafe_answer(answer: str) -> bool:
    """Whether a model answer tells the person to start, stop or change a medicine.

    A sentence that sends the person to their clinician, or reports what the clinician planned, is not an
    instruction.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        sentence = sentence.strip()
        if not sentence or _REFERRAL.search(sentence):
            continue
        if _DOSE.search(sentence) or _PERSIST.search(sentence):
            return True
        if (_DIRECTIVE.search(sentence) or _CALM.search(sentence)) and _mentions_medicine(sentence):
            return True
    return False


def record(gate: str, surface: str) -> None:
    """Notes one gate decision without the message (S-02)."""
    try:
        with storage.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS safety_decisions (at TEXT NOT NULL, gate TEXT NOT NULL, "
                         "surface TEXT NOT NULL, policy_version TEXT NOT NULL)")
            conn.execute("INSERT INTO safety_decisions VALUES (?, ?, ?, ?)",
                         (datetime.datetime.now(datetime.timezone.utc).isoformat(), gate, surface, POLICY_VERSION))
    except Exception:  # A record must never block the safety reply itself.
        pass


def check(text: str, surface: str) -> None:
    """Raises SafetyReply when the message falls under a gate."""
    if (gate := screen(text)) is not None:
        record(gate.name, surface)
        raise SafetyReply(gate)
