"""Deterministic safety gates: what each one catches, and the record questions it must leave alone."""

import unittest

from stellis import safety

GATED = {
    "emergency": [
        "I have chest pain and my left arm feels numb",
        "My chest feels tight and I'm sweating",
        "I can't breathe properly",
        "my face is drooping on one side",
        "She's having a seizure right now",
        "the seizure won't stop",
        "I think I took too many pills",
        "my throat is swelling after the new antibiotic",
        "he passed out and won't wake up",
    ],
    "crisis": [
        "I don't want to be alive anymore",
        "I keep thinking about suicide",
        "I want to hurt myself",
    ],
    "worse_after_change": [
        "I'm not feeling well today. Could it be because I stopped the medication too quickly?",
        "I feel dizzy since I lowered my dose",
        "Feeling awful since I came off my meds",
        "Is this withdrawal? I feel shaky",
    ],
    "medicine_change": [
        "Is it okay to continue reducing medication?",
        "Should I stop taking my lamotrigine?",
        "Can I cut back on my pills?",
        "How do I taper off my sertraline",
        "I want to come off my medication",
        "Should I double my dose tonight?",
        "Do I want to give up on my medication reduction plan?",
        "Can I skip my metformin this week",
        "help me make a tapering plan",
        "Should I stop taking my iron?",
        "can I come off insulin for a few days",
        "is it ok to skip my birth control pill",
    ],
    "keep_from_doctor": [
        "I don't want to tell my doctor I stopped",
        "How do I hide it from my doctor",
        "Can I do this without my doctor knowing?",
    ],
}

NOT_GATED = [
    "How has my Ferritin changed since 2027?",
    "When did I stop iron?",
    "Why was my dose changed in 2030?",
    "What did my doctor say about stopping progesterone?",
    "Did my doctor reduce my dose?",
    "What does this report say?",
    "What should I ask at my next appointment?",
    "Is anything here worth following up?",
    "What does this result mean for me?",
    "My chest X-ray results from 2028",
    "I want to stop drinking coffee",
    "Can I reduce my screen time?",
    "Log my evening iron",
    "I walked 5000 steps",
    "I want to cut back on sugar",
    "Should I reduce alcohol?",
    "How do I stop snacking at night?",
]


class SafetyGateTest(unittest.TestCase):
    def test_each_gate_catches_its_messages(self) -> None:
        for name, messages in GATED.items():
            for message in messages:
                with self.subTest(message=message):
                    gate = safety.screen(message)
                    self.assertIsNotNone(gate, message)
                    self.assertEqual(gate.name, name)

    def test_record_questions_are_answered_normally(self) -> None:
        for message in NOT_GATED:
            with self.subTest(message=message):
                self.assertIsNone(safety.screen(message))

    def test_replies_name_no_phone_number(self) -> None:
        for reply in (safety.EMERGENCY, safety.CRISIS, safety.WORSE_AFTER_CHANGE, safety.MEDICINE_CHANGE,
                      safety.KEEP_FROM_DOCTOR):
            self.assertNotRegex(reply, r"\d")

    def test_answers_that_instruct_a_medicine_change_are_caught(self) -> None:
        for answer in [
            "Maintain 0 mg. You must persevere.",
            "You can stop the medication now that your levels are normal.",
            "Reduce your lamotrigine dose by 25 mg each week.",
            "Take 400 mg tonight.",
            "It's okay to skip your iron on days you feel nauseous.",
            "You should stop the sertraline now.",
            "You have absolutely no need to worry about reducing your medication.",
        ]:
            with self.subTest(answer=answer):
                self.assertTrue(safety.unsafe_answer(answer))

    def test_answers_that_report_or_refer_are_left_alone(self) -> None:
        for answer in [
            "In September 2031 your doctor planned to continue alternate-day iron through December 18, then reassess.",
            "You could ask your doctor whether you should reduce your dose.",
            "Your ferritin was 11 ug/L on 2027-01-21 and rose to 48 ug/L by December 2031.",
            "Ask your doctor whether you should continue taking the iron supplement and for how long.",
            "Reduce your salt intake and walk daily, as your doctor advised in March.",
            "You can stop worrying about this result; it is within the normal range.",
        ]:
            with self.subTest(answer=answer):
                self.assertFalse(safety.unsafe_answer(answer))


if __name__ == "__main__":
    unittest.main()
