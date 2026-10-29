"""Provisional speakers for passages that overlap no acoustic turn.

Pyannote segmentation can skip speech that Whisper still transcribes. Those
passages get the closest voice prototype when an embedding exists, otherwise
the nearest acoustic turn in time, and always stay marked uncertain.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

PROTOTYPE_MIN_COVERAGE = 0.75
PROTOTYPE_MIN_MS = 2000
SINGLE_SPEAKER_CLUSTER = "speaker-1"


def unassigned_indices(segments: list[dict[str, object]]) -> list[int]:
    return [index for index, segment in enumerate(segments) if not segment.get("speaker_cluster_id")]


def prototype_indices(segments: list[dict[str, object]]) -> list[int]:
    """Confidently assigned passages long enough to represent their voice."""
    return [index for index, segment in enumerate(segments)
            if segment.get("speaker_cluster_id") and not segment.get("speaker_uncertain")
            and float(segment.get("speaker_coverage", 0)) >= PROTOTYPE_MIN_COVERAGE
            and int(segment["end_ms"]) - int(segment["start_ms"]) >= PROTOTYPE_MIN_MS]


def _nearest_turn(segment: dict[str, object], turns: list[dict[str, object]]) -> str | None:
    start, end = int(segment["start_ms"]), int(segment["end_ms"])
    best = min(turns, default=None,
               key=lambda turn: max(int(turn["start_ms"]) - end, start - int(turn["end_ms"]), 0))
    return str(best["speaker_cluster_id"]) if best else None


def _nearest_passage(index: int, segments: list[dict[str, object]]) -> str | None:
    for distance in range(1, len(segments)):
        for other in (index - distance, index + distance):
            if 0 <= other < len(segments) and segments[other].get("speaker_cluster_id"):
                return str(segments[other]["speaker_cluster_id"])
    return None


def fill_unassigned(segments: list[dict[str, object]], turns: list[dict[str, object]],
                    embeddings: dict[int, np.ndarray] | None = None) -> int:
    """Assign every passage without a cluster in place; returns how many changed.

    ``embeddings`` maps segment index to a unit TitaNet vector and should cover
    the prototype and unassigned passages the caller could embed.
    """
    missing = unassigned_indices(segments)
    if not missing:
        return 0
    embeddings = embeddings or {}
    groups: dict[str, list[np.ndarray]] = defaultdict(list)
    for index in prototype_indices(segments):
        if index in embeddings:
            groups[str(segments[index]["speaker_cluster_id"])].append(embeddings[index])
    names = list(groups)
    prototypes = None
    if names:
        means = np.stack([np.mean(groups[name], axis=0) for name in names])
        prototypes = means / (np.linalg.norm(means, axis=1, keepdims=True) + 1e-8)
    # Decide from the original assignments only, so one filled passage never
    # becomes the evidence for its neighbour.
    decisions: dict[int, tuple[str, str]] = {}
    for index in missing:
        segment = segments[index]
        if prototypes is not None and index in embeddings:
            decisions[index] = (names[int(np.argmax(prototypes @ embeddings[index]))], "embedding_fallback")
        elif cluster := _nearest_turn(segment, turns):
            decisions[index] = (cluster, "nearest_turn")
        elif cluster := _nearest_passage(index, segments):
            decisions[index] = (cluster, "nearest_passage")
        else:
            decisions[index] = (SINGLE_SPEAKER_CLUSTER, "single_speaker")
    for index, (cluster, source) in decisions.items():
        segments[index]["speaker_cluster_id"] = cluster
        segments[index]["speaker_uncertain"] = True
        segments[index]["speaker_match_source"] = source
    return len(decisions)
