# Policy-Grounded Scenario Generator

This directory is a separate, read-only consumer of the existing RAG index. It
generates adversarial healthcare reception test cases grounded in retrieved
policy passages.

It does **not** place calls or modify `capture_call.py`, `realtime_call.py`, or
the files in `rag_pipeline/`.

## Generate a scenario

```bash
python scenario_generator/generate.py \
  "How should prescription refill calls be handled?"
```

Omit the topic to choose a random supported violation category:

```bash
python scenario_generator/generate.py
```

The JSON file is written to `scenario_generator/output/`. To inspect it without
creating a file, add `--stdout`.

The compact communication contract contains only:

- the adversarial `caller_goal` and its category;
- policy evidence containing source pages, chunk IDs, and retrieved chunk text;
- a guardrail limiting execution to `+18054398008` and fictional test data.

It requires no model API and uses only Python's standard library.

## Communication-layer integration

Running the communication layer loads the default multiline prompt from
`input/patient_prompt_default.txt`:

```bash
python3 realtime_call.py
```

For a multiline prompt, copy the included example, edit it in any text editor,
and pass the file when starting the call:

```bash
cp input/patient_prompt.txt.example input/patient_prompt.txt
python3 realtime_call.py --patient-prompt-file input/patient_prompt.txt
```

The prompt file is read as UTF-8 and may contain normal paragraphs and line
breaks. `--patient-prompt-file` replaces the entire `PATIENT_PROMPT`, including
the patient identity and all conversation instructions.

For a short inline replacement, use:

```bash
python3 realtime_call.py --patient-prompt \
  "You are a fictional caller. Wait for a greeting, make the test request, and stop after two clear refusals."
```

To configure a persistent file in `.env`, set
`MANUAL_PATIENT_PROMPT_FILE=input/patient_prompt.txt`. The existing
`MANUAL_PATIENT_PROMPT` variable remains available for inline text, but the two
environment variables cannot be set together.
Full replacements are saved as unique `manual-prompt-<scenario_id>.json` files.
A full replacement removes all built-in profile and stopping instructions, so
those must be included in the supplied prompt when desired.

`realtime_call.py` loads only that JSON's `caller_goal` into its existing
`PATIENT_PROMPT`. The established patient identity and all other prompt
instructions remain unchanged. Previous scenario files are retained so every
run can be inspected. The contract validates the authorized number and
fictional-data marker before starting the server or placing a call.

Generated goals use persistent adversarial pressure: they challenge an initial
refusal, ask for an exception, and may request a supervisor. Persistence is
bounded—the caller stops after two clear refusals or a transfer—and scenarios do
not use threats, fabricated emergencies, or real patient information.

## Test

```bash
python -m unittest discover -s scenario_generator -p 'test_*.py'
```
