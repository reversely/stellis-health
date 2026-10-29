"""Refits the blood pressure LSTM's output layer on camera recordings and exports it (#231).

Inputs, downloaded by hand (see src/stellis/static/vendor/bp-lstm/README.md):
  lstm_ppg_nonmixed.h5        Zenodo 10.5281/zenodo.5590603 (CC BY 4.0)
  rPPG-BP-UKL_rppg_7s.h5      data/ of github.com/Fabian-Sc85/non-invasive-bp-estimation-using-deep-learning (GPL-3.0)

Usage: python refit.py WEIGHTS.h5 RPPG.h5 OUT_DIR
Prints the leave-one-patient-out error and the guess-the-average baseline, then writes
weights.bin and manifest.json for bp-estimate.js.
"""
import json
import sys
from pathlib import Path

import h5py
import numpy as np

from net import features, load

RIDGE = 100


def scaled(rppg):
    return (rppg - rppg.mean(1, keepdims=True)) / (rppg.std(1, keepdims=True) + 1e-9)


def batched(model, x):
    return np.concatenate([features(model, x[i:i + 512]) for i in range(0, len(x), 512)])


def fit(x, y):
    a = np.c_[x, np.ones(len(x))]
    return np.linalg.solve(a.T @ a + RIDGE * np.eye(a.shape[1]), a.T @ y)


def main(weights, data, out):
    model = load(weights)
    with h5py.File(data) as f:
        z = scaled(np.array(f['rppg']).T)
        bp = np.array(f['label']).T
        patient = np.array(f['subject_idx']).ravel()
    # The camera waveform's sign depends on the method, so both signs are averaged.
    x = (batched(model, z) + batched(model, -z)) / 2
    errors, baseline = [], []
    for p in np.unique(patient):
        train, test = patient != p, patient == p
        w = fit(x[train], bp[train])
        errors.append(np.abs(np.c_[x[test], np.ones(test.sum())] @ w - bp[test]))
        baseline.append(np.abs(bp[train].mean(0) - bp[test]))
    errors = np.concatenate(errors)
    print('leave-one-patient-out MAE', errors.mean(0).round(1),
          'within 10 mmHg', (errors <= 10).mean(0).round(2),
          'average-guess MAE', np.concatenate(baseline).mean(0).round(1))
    parts = [('conv.kernel', model['conv'][0][:, 0, :]), ('conv.bias', model['conv'][1])]
    for layer in ('l0', 'l1', 'l2'):
        for d in ('forward', 'backward'):
            for name, a in zip(('kernel', 'recurrent', 'bias'), model[layer][d]):
                parts.append((f'{layer}.{d}.{name}', a))
    parts.append(('head', fit(x, bp)))
    manifest, chunks, offset = {}, [], 0
    for name, a in parts:
        a = np.asarray(a, np.float32)
        manifest[name] = {'offset': offset, 'shape': list(a.shape)}
        chunks.append(a.ravel())
        offset += a.size
    out = Path(out)
    (out / 'weights.bin').write_bytes(np.concatenate(chunks).astype('<f4').tobytes())
    (out / 'manifest.json').write_text(json.dumps({'samples': 875, 'rate_hz': 125, 'tensors': manifest}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4])
