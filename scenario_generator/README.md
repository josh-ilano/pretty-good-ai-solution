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
4. saves the scenario JSON under the repository-level `generated_prompt/` directory; and
5. starts the guarded call with the generated prompt.

The generated category JSON is also the call's scenario record. The call runner
reuses that complete file and does not create a duplicate manual scenario record.
Standalone calls made with a manually authored `.txt` prompt create a complete
`manual_patient_prompt-*.json` record in `generated_prompt/`, using the same schema as a RAG
scenario. Because a manual prompt has no retrieved policy source, its
`policy_evidence` array is empty.

No manual prompt or topic is required. The generator now covers 31 indexed
situations across 30 categories, including demographic corrections, minimum-necessary form
data, registration and insurance verification, visit-duration errors,
double-booking authority, payment-related access, late arrivals/no-shows,
patient-abandonment safeguards, directions, clinical routing and callback
commitments, lab results, refills, referrals, formulary questions, diagnosis-code
integrity, repeat callers, portal credentials, former-worker access, provider
identifiers, privacy incidents, investigator verification, complaint retaliation,
and original or revised Notices of Privacy Practices.

Before selecting a topic, the generator reads recent JSON records in
`generated_prompt/` and excludes the ten most recently used topics when possible.
This provides diverse consecutive calls while retaining random selection across
the remaining policy-grounded scenarios. Every caller remains bounded to
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

During either a RAG or manual call, press **Ctrl+T** in the terminal to end the
active call early. The process keeps running long enough to receive and download
the finalized SignalWire recording, confirms both `transcript.txt` and
`recording.mp3`, and then shuts down.

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

The manual flow is:

1. create or edit a `.txt` prompt under `custom_input/`;
2. invoke `realtime_call.py` with that file;
3. receive a complete JSON metadata record under `generated_prompt/`; and
4. start the guarded call using that prompt.

For example:

```bash
python3 realtime_call.py --patient-prompt-file custom_input/my_test.txt
```

The application never creates `.txt` files in `custom_input/`; that directory is
reserved for user-authored prompts. Manual mode requires either
`--patient-prompt-file` or `--patient-prompt`. RAG mode writes only JSON metadata
to `generated_prompt/`.

You can persist that selection in `.env` with:

```dotenv
MANUAL_PATIENT_PROMPT_FILE=custom_input/my_test.txt
```

## RAG setup and retrieval check

If the policy index needs to be rebuilt:

```bash
python3 -m pip install -r rag_pipeline/requirements.txt
python3 rag_pipeline/ingest.py \
  rag_pipeline/documents/medical_practice_call_workflows_rag_extract.pdf
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
