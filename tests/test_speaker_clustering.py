"""Passage speaker groups come from voice embeddings, with an estimated speaker count."""

from __future__ import annotations

import unittest

import numpy as np

from stellis import speaker_clustering


def voices(pattern: list[int], seed: int = 1, spread: float = 0.15) -> np.ndarray:
    """Unit vectors around one random direction per speaker index in ``pattern``."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(max(pattern) + 1, 192))
    vectors = centers[pattern] + spread * rng.normal(size=(len(pattern), 192)) * np.linalg.norm(centers[0]) / 14
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def passages(count: int) -> list[dict[str, object]]:
    return [{"start_ms": index * 3000, "end_ms": index * 3000 + 2500, "text": "words",
             "speaker_cluster_id": f"speaker-{index + 40}", "speaker_uncertain": True} for index in range(count)]


class SpeakerClusteringTest(unittest.TestCase):
    def test_three_voices_give_three_groups_numbered_by_first_appearance(self) -> None:
        pattern = [2, 0, 1] + [index % 3 for index in range(57)]
        segments = passages(len(pattern))
        self.assertTrue(speaker_clustering.assign(segments, voices(pattern), np.ones(len(pattern), dtype=bool)))
        groups = [segment["speaker_cluster_id"] for segment in segments]
        self.assertEqual(groups[:3], ["speaker-1", "speaker-2", "speaker-3"])
        mapping = dict(zip(pattern, groups))
        self.assertEqual(len(set(mapping.values())), 3)
        self.assertTrue(all(mapping[voice] == group for voice, group in zip(pattern, groups)))
        self.assertFalse(any(segment["speaker_uncertain"] for segment in segments))
        self.assertTrue(all(segment["speaker_match_source"] == "passage_cluster" for segment in segments))

    def test_one_voice_stays_one_speaker(self) -> None:
        segments = passages(30)
        speaker_clustering.assign(segments, voices([0] * 30), np.ones(30, dtype=bool))
        self.assertEqual({segment["speaker_cluster_id"] for segment in segments}, {"speaker-1"})

    def test_passage_without_embedding_takes_its_nearest_neighbour_and_stays_uncertain(self) -> None:
        pattern = [index // 10 for index in range(40)]
        segments = passages(40)
        valid = np.ones(40, dtype=bool)
        valid[25] = False
        speaker_clustering.assign(segments, voices(pattern), valid)
        self.assertEqual(segments[25]["speaker_cluster_id"], segments[24]["speaker_cluster_id"])
        self.assertTrue(segments[25]["speaker_uncertain"])
        self.assertEqual(segments[25]["speaker_match_source"], "nearest_passage")

    def test_a_short_recording_from_one_voice_stays_one_speaker(self) -> None:
        # A one-minute note with six passages from one voice once came out as five speakers.
        segments = passages(6)
        self.assertTrue(speaker_clustering.assign(segments, voices([0] * 6, spread=0.6), np.ones(6, dtype=bool)))
        self.assertEqual({segment["speaker_cluster_id"] for segment in segments}, {"speaker-1"})

    def test_a_short_call_between_two_voices_gives_two_speakers(self) -> None:
        # The demo's twelve-passage call came out as one speaker from pyannote's fast clustering.
        pattern = [0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1]
        segments = passages(len(pattern))
        self.assertTrue(speaker_clustering.assign(segments, voices(pattern, spread=0.6), np.ones(len(pattern), dtype=bool)))
        groups = [segment["speaker_cluster_id"] for segment in segments]
        self.assertEqual(groups, ["speaker-1" if voice == 0 else "speaker-2" for voice in pattern])

    def test_too_few_embedded_passages_leave_turn_assignment_untouched(self) -> None:
        segments = passages(5)
        valid = np.array([True, True, False, False, False])
        self.assertFalse(speaker_clustering.assign(segments, voices([0, 1, 0, 1, 0]), valid))
        self.assertEqual(segments[0]["speaker_cluster_id"], "speaker-40")

    def test_clustering_is_deterministic(self) -> None:
        pattern = [index % 4 for index in range(48)]
        first = speaker_clustering.cluster(voices(pattern, spread=0.6))
        second = speaker_clustering.cluster(voices(pattern, spread=0.6))
        self.assertEqual(first.tolist(), second.tolist())


if __name__ == "__main__":
    unittest.main()
