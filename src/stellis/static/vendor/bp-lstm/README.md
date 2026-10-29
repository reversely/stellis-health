# Blood pressure LSTM (#231)

`bp-estimate.js` runs this network on the camera pulse waveform to show a blood pressure estimate. The estimate is shown and never saved. The owner approved the licence terms below on 2026-10-27.

- **Network and PPG weights:** `lstm_ppg_nonmixed.h5` from Schrumpf F, Frenzel P, Aust C, Osterhoff G, Fuchs M, *Assessment of Non-Invasive Blood Pressure Prediction from PPG and rPPG Signals Using Deep Learning*, Sensors 2021, 21(18):6022 ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC8472879/)). The weights are on Zenodo, [10.5281/zenodo.5590603](https://doi.org/10.5281/zenodo.5590603), under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The layers before the output are unchanged.
- **Output layer:** refitted by [`scripts/bp-estimate/refit.py`](../../../../../scripts/bp-estimate/refit.py) as a ridge regression (λ = 100) on `rPPG-BP-UKL_rppg_7s.h5`, the camera recordings in the authors' [repository](https://github.com/Fabian-Sc85/non-invasive-bp-estimation-using-deep-learning) (GPL-3.0). Because it is derived from that data, the output layer in `weights.bin` is distributed under [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html).
- **Input:** the last unbroken 7 s of waveform, resampled to 125 Hz and scaled to zero mean and unit spread. The prediction averages the waveform and its mirror image.
- **Files:** `weights.bin` holds little-endian float32 tensors at the offsets in `manifest.json`. `head` is 65 × 2: 64 weights, then the bias, for systolic and diastolic.

## Measured error

| Model | Systolic / diastolic MAE |
| --- | --- |
| Published weights, before camera training | 33.5 / 12.4 mmHg (this port reproduces the paper's Table 2 figure exactly) |
| Paper, LSTM after camera training | 13.6 / 10.3 mmHg |
| This refit, each of the 17 patients left out in turn | 11.5 / 10.5 mmHg, half within 10 mmHg |
| Guessing the other patients' average | 12.7 / 10.3 mmHg |

The training patients averaged 143/66 mmHg, and the estimates' spread is small (5.7 / 2.3 mmHg standard deviation), so estimates stay near that average. The paper's authors state that no result met the BHS or AAMI standards.
