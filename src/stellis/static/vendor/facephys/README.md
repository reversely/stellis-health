# FacePhys browser assets

Source: https://github.com/KegangWangCCNU/FacePhys-Demo
Pinned commit: `0007a97565872f5845824802eccec0258305c98d`

Model, projection model, initial recurrent state and inference worker copied from that commit. The upstream MIT privacy addendum is retained in LICENSE. Processing stays on device. Collection requires explicit consent; saving derived results and sending selected context require separate consent in the widget.

Worker modifications: all LiteRT assets are self-hosted; models are compiled from Uint8Array instead of blob URLs to preserve Stellis CSP. Low-power mode skips unused visualization projections. The recurrent tensor map and output computation are unchanged.

LiteRT core and wasm-utils 0.2.1 are Apache-2.0 (LITERT-LICENSE). `litert.js` is bundled with esbuild from `dist/index.js` of the `@litertjs/core` package, version 0.2.1; `wasm/` is copied unchanged from that package. All three WASM variants support the library's feature selection. No CDN requests occur.

The adapter uses the existing local VitalLens face boxes with the upstream Kalman crop smoothing, expanded upper-face crop, 36×36 RGB/255 preprocessing and smoothed dt clock. Actual sample arrivals also gate contiguous capture. This face detector adaptation has not been benchmarked against upstream or reference ECG; no clinical accuracy gain is claimed.
