"""Explicit call completion shared by intake and continuing conversations."""
import re

_END = re.compile(
    r"(?:(?:ok(?:ay)?|yes|yeah|no|nope)[ ,]*)?(?:goodbye|bye(?: bye)?|hang up|"
    r"(?:please )?(?:end|stop|finish) (?:the |this |our )?call|"
    r"(?:that(?:'s| is)|that was) (?:all|everything)|"
    r"(?:there(?:'s| is) )?nothing (?:else|more)|i (?:do not|don't) have anything else|i(?:'m| am) (?:done|finished))"
    r"(?:[ ,]*(?:please|thanks|thank you|for now|goodbye|bye))*",
    re.IGNORECASE,
)


def wants_end(text: str) -> bool:
    """Recognizes a whole-message request to finish rather than a health topic."""
    normalized = ' '.join(text.replace('’', "'").split()).strip(' .!?')
    return _END.fullmatch(normalized) is not None
