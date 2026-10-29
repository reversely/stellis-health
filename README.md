<p align="center"><img src="assets/stellis_logo.png" alt="Stellis Health logo" width="104"></p>

# Stellis Health

Stellis is a privacy-first AI agent for your health records. Add your lab reports, letters and visit recordings, and Stellis helps you understand, log, monitor and chart your health over time.

Try it at <https://204-12-180-88.sslip.io>. **Try the demo** there walks through a fictional patient's five years of records, in a short or a full tour.

## What it does

- **Reads your reports.** Upload a lab report, letter or photo and it opens with Stellis's summary beside it. Stellis finds the date and each result, and adds them to your history when you approve.
- **Answers from your records.** Ask anything, such as how a result has changed. Each answer links to the page it came from.
- **Summarizes your visits.** Record an appointment or upload a recording. Stellis transcribes it and writes up what was decided.
- **Keeps track of medicines and goals.** Tick off doses and goals. When a new record changes your care, Stellis suggests the update for you to approve.
- **Checks your vital signs.** Pulse, breathing, heart rate variability and a blood pressure estimate from about 30 seconds of webcam video. The video stays on your device.
- **Charts your results.** Lab results and home readings appear together over time, each point linked to its record.

## Where your data goes

| Part | Where | What it does |
| --- | --- | --- |
| Browser | Your device | The app. Camera video for vital signs never leaves it |
| Stellis server | One Nebius AI Cloud virtual machine, United States | Stores each account's records encrypted (AES-256-GCM) with a key only that person's password or recovery key unlocks. Reads scanned pages and transcribes recordings itself. Removes names and ID numbers before anything goes to a model |
| Nebius Token Factory | Nebius | Runs the language models, with zero data retention |
| Web research | MedlinePlus, PubMed, Tavily | Receives only the topic of a question, never personal details |

[How Stellis works](docs/architecture.md) has the diagram, and what each part does for you.

## Run it

You need Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22 for the tests, and Tesseract with English data (`tesseract-ocr` and `tesseract-ocr-eng` on Debian or Ubuntu).

1. Download the demo's records. They are the [stellis-health-demo dataset](https://huggingface.co/datasets/reversely/stellis-health-demo), not part of this repository; without them the demo is off.

   ```sh
   python3 scripts/fetch_demo.py
   ```

2. Start the app and open <http://127.0.0.1:8765>. The first start downloads the speech models.

   ```sh
   STELLIS_RUNTIME_PROFILE=direct uv run --locked stellis serve
   ```

3. For summaries, answers and agents, set `NEBIUS_API_KEY` to a Nebius Token Factory key. Recording, transcription and reading scanned pages work without one.

## Test it

The tests use the demo's records, so fetch them first (step 1 above).

```sh
uv run --locked --with pytest python -m pytest -q tests
npm ci
node --test tests/*.cjs
```

## Versions

Stellis runs three ways, chosen by `STELLIS_RUNTIME_PROFILE`:

| Version | Where agents run | Models | Start |
| --- | --- | --- | --- |
| `direct`, the live site | On the Stellis server | Nebius Token Factory | `STELLIS_RUNTIME_PROFILE=direct uv run --locked stellis serve` |
| `cloud` | OpenClaw in a NemoClaw sandbox on Nebius | Nebius Token Factory | `uv run --locked stellis launch cloud` |
| `local` | OpenClaw in a NemoClaw sandbox on your own GPU machine | Ollama on that machine | `uv run --locked stellis launch local` |

`cloud` and `local` read their settings from `.private/cloud.env` or `.private/local.env` (or a file given with `--env-file`); start from `deploy/profiles/cloud.env.example` or `local.env.example`. Each checks its settings before starting and names anything missing.

## Releases

Publishing a release deploys the live site. `.github/workflows/deploy.yml` runs the tests, then the server builds the release with the demo revision named in `DEMO_REVISION`, starts it, and checks that the site serves it. If the new version does not start, the server puts the previous one back.

## Accuracy

Speech models are English-only. Transcripts, speaker labels and AI answers can be wrong, so check the recording or the linked page before relying on them. Stellis does not diagnose or replace a clinician.

## Licence

[Apache License 2.0](LICENSE) and [third-party notices](NOTICE).
