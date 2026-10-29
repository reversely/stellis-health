"""A care review reply is read even when the model wraps its JSON."""

import unittest

from stellis import routine_review


class ReplyJsonTest(unittest.TestCase):
    def test_bare_fenced_and_wrapped_replies_are_read(self) -> None:
        for reply in ('{"proposals": []}', '```json\n{"proposals": []}\n```', 'Here it is: {"proposals": []} done'):
            self.assertEqual(routine_review._reply_json(reply), {"proposals": []})

    def test_a_reply_without_json_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            routine_review._reply_json('No changes.')


class PrepareRouteTest(unittest.TestCase):
    def test_a_review_failing_its_checks_is_a_state_and_an_outage_an_error(self) -> None:
        import asyncio
        from unittest import mock
        from fastapi import HTTPException
        from stellis import agent_gateway, routine_routes

        with mock.patch.object(routine_review, 'prepare', mock.AsyncMock(side_effect=ValueError('Retry review.'))):
            self.assertEqual(asyncio.run(routine_routes.prepare('document', 'd1')),
                             {'state': 'failed', 'detail': 'Retry review.', 'proposals': []})
        with mock.patch.object(routine_review, 'prepare', mock.AsyncMock(side_effect=agent_gateway.GatewayError('down'))):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(routine_routes.prepare('document', 'd1'))
            self.assertEqual(caught.exception.status_code, 503)
