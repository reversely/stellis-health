"""Per-socket report conversation over the native voice/text transport.

The selected source is server-owned and revision checked. Replies and citations
share the ordinary report exchange store; this script has no separate clinical
state and cannot accept measurements or log goals.
"""
from stellis import documents, intake, notebook_chat, report_context
import re


class ReportCall:
    update_notes = None

    def __init__(self, document_id, map_id, region_id):
        record, mapped, selection = report_context.resolve_selection(document_id, map_id, region_id)
        self.document_id = record['id']
        self.map_id = map_id
        self.region_id = selection['id']
        self.title = record['title']
        self.quote = selection['text']
        self.page = mapped['page']
        self.question = ''
        self.turns = 0

    def load(self):
        return {}

    def opening(self):
        return 'I have the selected part of your report open. What would you like to understand?'

    def add_person_turn(self, text):
        text = str(text).strip()
        if not text or len(text) > 2000:
            raise ValueError('Enter a question of at most 2,000 characters')
        if self.turns >= 40:
            raise ValueError('This call has reached its length limit. Start another call to continue.')
        self.question = text
        self.turns += 1
        return text

    def extend_person_turn(self, text):
        # The transport drops the unsent/playing reply before extending speech.
        combined = (self.question + ' ' + str(text).strip()).strip()
        self.turns -= 1
        return self.add_person_turn(combined)

    def drop_agent_reply(self):
        # A completed, source-checked exchange remains saved even if interrupted.
        pass

    def add_agent_turn(self, text):
        # report_context.ask already persists the complete source-checked reply.
        pass

    async def reply(self, timings=None):
        try:
            result = await report_context.ask(self.document_id, self.map_id, self.region_id, self.question)
        except (ValueError, LookupError, documents.DocumentConflict, notebook_chat.ChatError) as exc:
            raise intake.IntakeError(str(exc)) from exc
        # Spoken output omits citation syntax; the UI receives the exact saved
        # answer and validated citation objects as the following action.
        yield re.sub(r'\[\[\d+\]\]|\*\*|^#{1,6}\s+', '', result['answer'], flags=re.M).strip()
        yield {'action': 'report_answer', 'exchange': result}

    def end(self):
        return {}
