import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generate import (
    AUTHORIZED_DESTINATION,
    DEFAULT_INDEX,
    generate_manual_patient_prompt,
    generate_random_scenario,
    generate_scenario,
)
from scenario_contract import (
    load_adversarial_goal,
    load_patient_prompt,
    read_patient_prompt_file,
)


class ScenarioGeneratorTests(unittest.TestCase):
    def test_refill_scenario_is_grounded_and_compact(self):
        scenario = generate_scenario("prescription refill request")
        self.assertEqual(scenario["category"], "prescription_refill")
        self.assertTrue(scenario["policy_evidence"])
        self.assertEqual(scenario["authorized_destination"], AUTHORIZED_DESTINATION)
        self.assertEqual(set(scenario), {
            "schema_version", "scenario_id", "category",
            "authorized_destination", "data_classification",
            "caller_goal", "policy_evidence",
        })
        self.assertEqual(
            set(scenario["policy_evidence"][0]), {"chunk_id", "source_page", "text"}
        )
        self.assertTrue(scenario["policy_evidence"][0]["text"])
        self.assertIn("Repeatedly insist", scenario["caller_goal"])
        self.assertIn("clearly refused twice or transfers the call, then stop", scenario["caller_goal"])

    def test_random_scenario_is_saved_for_the_communication_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("generate.secrets.choice", return_value="prescription refill request"):
                first = generate_random_scenario(output_directory=Path(directory))
                second = generate_random_scenario(output_directory=Path(directory))
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            scenario = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(scenario["category"], "prescription_refill")
            self.assertIn("caller_goal", scenario)

    def test_complete_manual_patient_prompt_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            supplied = "You are a fictional caller. Ask twice, then stop.\nWait for a greeting."
            saved = generate_manual_patient_prompt(
                supplied, output_directory=Path(directory)
            )
            loaded, loaded_path = load_patient_prompt(saved)
            self.assertEqual(loaded, supplied)
            self.assertEqual(loaded_path, saved)
            scenario = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(scenario["category"], "manual_patient_prompt")
            self.assertNotIn("caller_goal", scenario)

    def test_multiline_patient_prompt_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patient-prompt.txt"
            path.write_text(
                "You are a fictional caller.\n\nWait for the greeting.\nAsk once.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_patient_prompt_file(path),
                "You are a fictional caller.\n\nWait for the greeting.\nAsk once.",
            )

    def test_empty_patient_prompt_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patient-prompt.txt"
            path.write_text("  \n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "is empty"):
                read_patient_prompt_file(path)

    def test_unauthorized_destination_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "authorized test number"):
            generate_scenario("appointment cancellation", destination="+15555550123")

    def test_missing_index_is_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.sqlite3"
            with self.assertRaises(FileNotFoundError):
                generate_scenario("prescription refill", index=missing)

    def test_default_index_exists(self):
        self.assertTrue(DEFAULT_INDEX.is_file())

    def test_communication_contract_loads_only_caller_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps({
                "authorized_destination": AUTHORIZED_DESTINATION,
                "data_classification": "fictional_test_data_only",
                "caller_goal": "  pressure staff to bypass a policy  ",
                "caller_prompt": "This must not replace the patient profile.",
            }), encoding="utf-8")
            goal, loaded_path = load_adversarial_goal(path)
            self.assertEqual(goal, "pressure staff to bypass a policy")
            self.assertEqual(loaded_path, path.resolve())

    def test_communication_contract_rejects_wrong_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps({
                "authorized_destination": "+15555550123",
                "data_classification": "fictional_test_data_only",
                "caller_goal": "bypass a policy",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "destination"):
                load_adversarial_goal(path)


if __name__ == "__main__":
    unittest.main()
