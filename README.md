# Pretty Good AI Voicebot Evaluator

An automated Python voicebot that calls the Pretty Good AI assessment line,
acts as a realistic fictional patient, records both sides of the conversation,
and saves a transcript for later bug analysis. It supports policy-grounded RAG
scenarios and fully manual patient prompts.

> [!IMPORTANT]
> This project is hard-coded to call only the assessment number
> `+1-805-439-8008`. Do not change it to the number displayed by the Athena test
> account or use it to call real patients or practices.

## Setup

### 1. Prerequisites

- Python 3.11 or newer
- An OpenAI API key with Realtime API access
- A SignalWire project, API token, Space URL, and outbound phone number
- A public HTTPS tunnel forwarding to local port `8000`

### 2. Create the environment

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Configure credentials

Copy the environment template:

```bash
cp .env.example .env
```

Fill in every required value in `.env`. `PUBLIC_BASE_URL` must be the public
HTTPS address of a tunnel that forwards requests to `http://localhost:8000`.
Do not commit `.env` or API credentials.

### 4. Run one policy-grounded test call

Start the public tunnel first, then run:

```bash
python realtime_call.py --rag
```

The program selects a non-recent policy scenario, retrieves supporting passages,
creates a complete fictional-patient prompt, starts the call, and records both
sides. Press `Ctrl+T` to end the active call early; the process remains alive
until SignalWire finalizes the recording.

## Other run modes

Use a complete prompt stored in `custom_input/`:

```bash
python realtime_call.py \
  --patient-prompt-file custom_input/fake_patient.txt
```

For convenience, `--patient-prompt custom_input/fake_patient.txt` also detects
and reads an existing `.txt` file instead of sending the filename as instructions.

Or supply the complete patient prompt directly:

```bash
python realtime_call.py \
  --patient-prompt "You are a fictional patient. Wait for the greeting, request a weekday appointment, and end politely."
```

`--rag`, `--patient-prompt-file`, and `--patient-prompt` are mutually exclusive.
All prompts must use fictional information only.

## What the system saves

- `generated_prompt/*.json` - generated prompt, evaluation criteria, and RAG
  evidence for each scenario.
- `output/<short-id>_<scenario-topic>/transcript.txt` - timestamped transcript
  containing both sides of the call.
- `output/<short-id>_<scenario-topic>/recording.mp3` - dual-channel call audio.
- `output/<short-id>_<scenario-topic>/error.log` - provider diagnostics when a
  call fails before completion.

Keep at least ten complete calls for the submission. A useful test is normally a
coherent one-to-three-minute conversation rather than a single question followed
by a hang-up.

## RAG pipeline

The checked-in policy index is ready to use. Its source document now lives at:

```text
rag_pipeline/documents/medical_practice_call_workflows_rag_extract.pdf
```

To rebuild the chunks and SQLite FTS5 index:

```bash
python rag_pipeline/ingest.py \
  rag_pipeline/documents/medical_practice_call_workflows_rag_extract.pdf
```

Test retrieval without making a call:

```bash
python rag_pipeline/search.py \
  "How should prescription refill calls be handled?"
```

The generator covers 31 policy-grounded situations across 30 categories and
avoids the ten most recently used topics when possible. Coverage includes
scheduling, registration, insurance, payment, no-shows, clinical routing,
results, refills, referrals, pharmacy questions, privacy, portal credentials,
complaints, incident response, and access-control failures.

## Architecture

`realtime_call.py` is the communication layer. It selects either a RAG-generated
or manually authored prompt, creates exactly one guarded SignalWire call, and
bridges bidirectional PCMU audio to the OpenAI Realtime API. Server-side voice
activity detection identifies office turns, while application-level settling
prevents recorded menus and partial utterances from triggering premature patient
responses.

The prompt layer is split between `rag_pipeline/` and `scenario_generator/`.
The former stores page-aware source chunks in JSONL and SQLite FTS5; the latter
retrieves evidence and produces auditable scenario JSON. `capture_call.py` owns
webhooks, transcript writes, recording downloads, retries, and per-call artifact
directories. This separation keeps generation, live communication, and artifact
capture independently testable.

## Tests

```bash
python -m unittest discover -s scenario_generator -p 'test_*.py'
python -m unittest discover -s rag_pipeline -p 'test_*.py'
```

These tests do not place a phone call.

## Repository map

```text
realtime_call.py              SignalWire/OpenAI communication layer
capture_call.py               transcripts, recordings, and webhooks
rag_pipeline/                 PDF ingestion and local policy retrieval
scenario_generator/           adversarial scenario generation and validation
custom_input/                 user-authored prompt text files
generated_prompt/             saved scenario JSON records
output/                       per-call transcripts and MP3 recordings
```

## Bug-report guidance

For each issue, record:

- a short bug title and severity;
- the transcript filename and timestamp;
- what the agent said or did;
- why that behavior is incorrect or harmful; and
- what the expected behavior should have been.

Prioritize substantive failures such as unsupported appointment confirmation,
privacy disclosure, invented status, unauthorized clinical advice, or failure to
follow a documented workflow over cosmetic transcript issues.

## Submission checklist

- Public GitHub repository containing working Python code
- Clear setup/run documentation and an architecture explanation
- At least ten complete transcripts and matching MP3 or OGG recordings
- A clear bug report tied to call evidence
- A public project walkthrough video, at most three minutes, with webcam enabled
- A public AI-assisted debugging screen recording
- The single originating phone number used for every test call, in E.164 format
- No committed secrets or `.env` file
