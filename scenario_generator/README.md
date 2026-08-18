# Policy-Grounded Voice-Agent Tests

The scenario generator retrieves relevant passages from the local policy index,
selects a bounded adversarial test, and records both the expected safe response
and the conditions that count as a failure. All scenarios use fictional test
data and are restricted to the authorized assessment destination.

## One-command RAG-to-call workflow

From the repository root, run:

```bash
python3 realtime_call.py --rag
```

This command:

1. randomly selects a supported policy-conflict scenario;
2. retrieves policy evidence from `rag_pipeline/data/policy_index.sqlite3`;
3. generates a complete adversarial patient prompt and evaluation criteria;
4. saves the scenario JSON under the repository-level `input/` directory; and
5. starts the guarded call with the generated prompt.

The generated category JSON is also the call's scenario record. The call runner
reuses that complete file and does not create a duplicate manual scenario record.
Standalone calls made with a manually authored `.txt` prompt create a complete
`manual_patient_prompt-*.json` record in `input/`, using the same schema as a RAG
scenario. Because a manual prompt has no retrieved policy source, its
`policy_evidence` array is empty.

No manual prompt or topic is required. Each run independently selects from the
indexed call-workflow and privacy scenarios: registration and insurance
verification, scheduling, payment collection, late arrivals/no-shows, directions,
office policies, clinical questions, lab results, prescription refills,
authorizations/referrals, formulary questions, repeat callers, protected-information
access, privacy complaints, and Notices of Privacy Practices. The caller prompt asks
the voice agent to test the retrieved safe workflow while remaining bounded to
fictional data and at most two attempts.

To use a custom index or evidence limit:

```bash
python3 realtime_call.py --rag \
  --rag-index rag_pipeline/data/policy_index.sqlite3 \
  --rag-evidence-limit 5
```

`--rag` cannot be combined with `--patient-prompt` or
`--patient-prompt-file`. To generate JSON without placing a call, use the
lower-level generator below.

Call artifacts are stored under `output/<short-id>_<scenario_topic>/`, for
example `output/a1b2c3d4_privacy_complaint_retaliation/`.

Each generated JSON includes:

- `patient_prompt`: the complete fictional caller instructions;
- `violation_attempt`: the behavior being tested;
- `expected_safe_behavior`: actions that indicate the agent handled the test safely;
- `failure_conditions`: observable behaviors that expose a flaw; and
- `policy_evidence`: retrieved passages with page and chunk provenance.

## Generate JSON only

The lower-level generator does not place a call:

```bash
python3 scenario_generator/generate.py \
  "How should prescription refill calls be handled?"
```

Print the JSON without saving it:

```bash
python3 scenario_generator/generate.py \
  "lab results interpretation" \
  --stdout
```

## Manual prompt workflow

Without an override, the call runner loads
`input/patient_prompt_default.txt`:

```bash
python3 realtime_call.py
```

To use an edited multiline prompt:

```bash
python3 realtime_call.py --patient-prompt-file input/patient_prompt.txt
```

You can persist that selection in `.env` with:

```dotenv
MANUAL_PATIENT_PROMPT_FILE=input/patient_prompt.txt
```

## RAG setup and retrieval check

If the policy index needs to be rebuilt:

```bash
python3 -m pip install -r rag_pipeline/requirements.txt
python3 rag_pipeline/ingest.py \
  output/pdf/medical_practice_call_workflows_rag_extract.pdf
```

Test retrieval directly with:

```bash
python3 rag_pipeline/search.py \
  "How should prescription refill calls be handled?"
```

## Tests

```bash
python3 -m unittest discover -s scenario_generator -p 'test_*.py'
```
