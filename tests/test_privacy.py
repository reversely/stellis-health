"""Identifiers leave for the hosted model as placeholders and come back restored."""

import asyncio
import json
import unittest

import httpx

from stellis import privacy

REPORT = ("Patient: Maya Chen | MRN: SYN-MAYA-001 | DOB / age 1993-03-14 | Provider: Dr Neri Solan\n"
          "Ferritin 11 ug/L on 2022-03-04. Call 416-555-0199 or maya.chen@example.com.")


def _sent_and_reply(reply_body: bytes, content_type: str = "application/json", url="https://api.tokenfactory.nebius.com/v1/chat/completions"):
    seen = {}

    def model(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, headers={"content-type": content_type}, content=reply_body)

    transport = privacy.Transport()
    transport.inner = httpx.MockTransport(model)
    with httpx.Client(transport=transport) as client:
        reply = client.post(url, json={"model": "nemotron", "messages": [
            {"role": "system", "content": "Answer from the records."},
            {"role": "user", "content": REPORT}]})
        return seen, reply


class PrivacyTest(unittest.TestCase):
    def test_direct_identifiers_never_reach_the_model(self) -> None:
        seen, _ = _sent_and_reply(b'{"choices": []}')
        sent = seen["body"]
        for identifier in ("Maya", "Chen", "SYN-MAYA-001", "1993-03-14", "Solan", "416-555-0199", "maya.chen@example.com"):
            self.assertNotIn(identifier, sent)
        # Clinical facts and their dates stay; answers need them.
        self.assertIn("Ferritin 11 ug/L on 2022-03-04", sent)
        self.assertIn('"model": "nemotron"', sent)

    def test_words_after_a_record_label_are_not_identifiers(self) -> None:
        # #258: "A record that fits" became "A record [ID_1] fits", and sorting replies came back as
        # placeholders that matched no record.
        prose = "A record that fits only loosely goes in the general notebook; the chart showing iron."
        redacted, mapping = privacy.pseudonymize({"messages": [{"role": "user", "content": prose}]}, names=[])
        self.assertEqual(redacted["messages"][0]["content"], prose)
        self.assertEqual(mapping.back, {})
        text = privacy.pseudonymize({"messages": [{"role": "user", "content": "Record: SYN-MAYA-001, MRN 48213"}]}, names=[])[0]
        self.assertNotIn("SYN-MAYA-001", json.dumps(text))
        self.assertNotIn("48213", json.dumps(text))

    def test_placeholders_in_a_reply_are_restored(self) -> None:
        seen, _ = _sent_and_reply(b'{}')
        mapping = privacy.pseudonymize(json.loads(seen["body"]), [])[1]
        sent = json.loads(seen["body"])["messages"][1]["content"]
        person = sent.split("Patient: ")[1].split(" |")[0]
        reply = json.dumps({"choices": [{"message": {"content": f"{person}'s ferritin was low."}}]}).encode()
        _, response = _sent_and_reply(reply)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Maya Chen's ferritin was low.")
        del mapping

    def test_a_placeholder_split_across_streamed_chunks_is_restored(self) -> None:
        seen, _ = _sent_and_reply(b"{}")
        person = json.loads(seen["body"])["messages"][1]["content"].split("Patient: ")[1].split(" |")[0]
        pieces = ["Hello ", person[:4], person[4:], ", your ferritin", " was low."]
        events = b"".join(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": p}}]}).encode() + b"\n\n"
                          for p in pieces) + b"data: [DONE]\n\n"
        _, response = _sent_and_reply(events, "text/event-stream")
        text = "".join(json.loads(line[5:])["choices"][0]["delta"].get("content", "")
                       for line in response.text.splitlines()
                       if line.startswith("data:") and line[5:].strip() != "[DONE]")
        self.assertEqual(text, "Hello Maya Chen, your ferritin was low.")

    def test_a_held_placeholder_prefix_is_released_at_the_end(self) -> None:
        events = (b'data: {"choices": [{"index": 0, "delta": {"content": "See note [PERS"}}]}\n\n'
                  b'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n')
        _, response = _sent_and_reply(events, "text/event-stream")
        text = "".join(json.loads(line[5:])["choices"][0]["delta"].get("content", "")
                       for line in response.text.splitlines() if line.startswith("data:"))
        self.assertEqual(text, "See note [PERS")

    def test_local_models_and_other_requests_are_untouched(self) -> None:
        seen, _ = _sent_and_reply(b"{}", url="http://127.0.0.1:11434/v1/chat/completions")
        self.assertIn("Maya Chen", seen["body"])

    def test_the_async_transport_matches(self) -> None:
        seen = {}

        def model(request):
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        async def run():
            transport = privacy.AsyncTransport()
            transport.inner = httpx.MockTransport(model)
            async with httpx.AsyncClient(transport=transport) as client:
                return await client.post("https://api.tokenfactory.nebius.com/v1/chat/completions",
                                         json={"messages": [{"role": "user", "content": REPORT}]})

        response = asyncio.run(run())
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "ok")
        self.assertNotIn("Maya", seen["body"])


if __name__ == "__main__":
    unittest.main()
