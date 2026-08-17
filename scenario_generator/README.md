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

Running the communication layer automatically generates a random,
policy-grounded scenario and saves it under a unique category-and-ID filename in
`scenario_generator/output/`:

```bash
python realtime_call.py
```

`realtime_call.py` loads only that JSON's `caller_goal` into its existing
`PATIENT_PROMPT`. The established patient identity and all other prompt
instructions remain unchanged. Previous scenario files are retained so every
run can be inspected. The contract validates the authorized number and
fictional-data marker before starting the server or placing a call.

## Test

```bash
python -m unittest discover -s scenario_generator -p 'test_*.py'
```
