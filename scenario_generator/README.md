# Policy-Grounded Scenario Generator

This directory is a separate, read-only consumer of the existing RAG index. It
generates adversarial healthcare reception test cases: the caller asks staff to
violate a retrieved policy, while evaluation criteria describe the compliant
behavior expected from the office.

It does **not** place calls or modify `capture_call.py`, `realtime_call.py`, or
the files in `rag_pipeline/`.

## Generate a scenario

```bash
python scenario_generator/generate.py \
  "How should prescription refill calls be handled?"
```

The JSON file is written to `scenario_generator/output/`. To inspect it without
creating a file, add `--stdout`.

Each scenario contains:

- retrieved policy evidence with source pages and chunk IDs;
- a caller-only prompt that does not reveal the expected response;
- expected safe behavior and explicit failure conditions for evaluation;
- a guardrail limiting execution to `+18054398008` and fictional test data.

The generator is deterministic except for `created_at`, requires no model API,
and uses only Python's standard library.

## Communication-layer integration

`realtime_call.py` loads only the generated JSON's `caller_goal` into its
existing `PATIENT_PROMPT`. The established patient identity and all other prompt
instructions remain unchanged. Set an explicit scenario for a reproducible run:

```bash
SCENARIO_JSON_PATH=scenario_generator/output/prescription_refill-a199c151b56f.json \
  python realtime_call.py
```

When `SCENARIO_JSON_PATH` is omitted, the communication layer uses the newest
JSON file in `scenario_generator/output/`. It validates the authorized number
and fictional-data marker before starting the server or placing a call.

## Test

```bash
python -m unittest discover -s scenario_generator -p 'test_*.py'
```
