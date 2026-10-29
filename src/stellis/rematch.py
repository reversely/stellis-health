"""Conservative acoustic speaker suggestions from saved ASR segments."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

MIN_PROTOTYPE_EXAMPLES = 12
MIN_EMBEDDING_MS = 1000
MIN_PROTOTYPE_MS = 2000
MIN_COVERAGE = 0.75
MIN_SIMILARITY = 0.55
MIN_MARGIN = 0.05


def extract_embeddings(wav_path: Path, model_path: Path, segments: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray]:
    """Compute on-device TitaNet vectors for speech-bearing ASR segments."""
    import sherpa_onnx

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=2)
    if not config.validate():
        raise RuntimeError("Speaker embedding model configuration is invalid")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    audio, sample_rate = sf.read(wav_path, dtype="float32")
    if sample_rate != 16000 or audio.ndim != 1:
        raise RuntimeError("Speaker rematch requires mono 16 kHz audio")
    vectors: list[np.ndarray] = []
    valid: list[bool] = []
    empty = np.zeros(extractor.dim, dtype=np.float32)
    for segment in segments:
        start = max(0, int(segment["start_ms"]) * 16)
        end = min(len(audio), int(segment["end_ms"]) * 16)
        waveform = np.ascontiguousarray(audio[start:end])
        if len(waveform) < MIN_EMBEDDING_MS * 16:
            vectors.append(empty)
            valid.append(False)
            continue
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=waveform)
        stream.input_finished()
        if not extractor.is_ready(stream):
            vectors.append(empty)
            valid.append(False)
            continue
        vector = np.asarray(extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            vectors.append(empty)
            valid.append(False)
            continue
        vectors.append(vector / norm)
        valid.append(True)
    return np.stack(vectors), np.asarray(valid, dtype=bool)


def suggest_speakers(segments: list[dict[str, object]], embeddings: np.ndarray,
                     valid: np.ndarray) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Use only well-supported acoustic groups as speaker prototypes.

    Short/ambiguous voices are left unresolved (uncertain, first-pass speaker)
    instead of being folded into a dominant group. Existing segment IDs, words,
    text, and timing are retained.
    """
    if len(segments) != len(embeddings) or len(segments) != len(valid):
        raise ValueError("Embedding count does not match transcript")
    examples: dict[str, list[int]] = defaultdict(list)
    for index, segment in enumerate(segments):
        cluster = segment.get("speaker_cluster_id")
        if (cluster and valid[index] and float(segment.get("speaker_coverage", 0)) >= MIN_COVERAGE
                and int(segment["end_ms"]) - int(segment["start_ms"]) >= MIN_PROTOTYPE_MS):
            examples[str(cluster)].append(index)
    groups = [(cluster, indices) for cluster, indices in examples.items()
              if len(indices) >= MIN_PROTOTYPE_EXAMPLES]
    groups.sort(key=lambda item: -len(item[1]))
    if len(groups) < 2:
        raise RuntimeError("Not enough distinct acoustic speech to rematch speakers")
    prototypes = []
    for _, indices in groups:
        vector = embeddings[indices].mean(axis=0)
        prototypes.append(vector / (np.linalg.norm(vector) + 1e-8))
    scores = embeddings @ np.stack(prototypes).T
    sorted_indices = np.argsort(-scores, axis=1)
    suggested: list[dict[str, object]] = []
    accepted = 0
    for index, segment in enumerate(segments):
        winner = int(sorted_indices[index, 0])
        best = float(scores[index, winner])
        margin = best - float(scores[index, int(sorted_indices[index, 1])])
        supported = bool(valid[index] and best >= MIN_SIMILARITY and margin >= MIN_MARGIN)
        accepted += supported
        # An unresolved passage keeps its first-pass speaker; one that never had
        # a turn takes the closest prototype. Both stay marked uncertain.
        fallback = segment.get("speaker_cluster_id") or (groups[winner][0] if valid[index] else None)
        suggested.append({**segment,
                          "raw_speaker_cluster_id": segment.get("speaker_cluster_id"),
                          "speaker_cluster_id": groups[winner][0] if supported else fallback,
                          "speaker_uncertain": not supported,
                          "speaker_match_source": "embedding_suggestion" if supported else "unresolved",
                          "speaker_match_score": round(best, 3) if valid[index] else None,
                          "speaker_match_margin": round(margin, 3) if valid[index] else None})
    metrics = {"segment_count": len(segments), "prototype_count": len(groups),
               "suggested_segment_count": accepted,
               "suggested_segment_fraction": round(accepted / len(segments), 3) if segments else 0,
               "unresolved_segment_count": len(segments) - accepted,
               "similarity_threshold": MIN_SIMILARITY, "margin_threshold": MIN_MARGIN}
    return suggested, metrics
