import json
import tempfile
import unittest
from pathlib import Path

from generate import AUTHORIZED_DESTINATION, DEFAULT_INDEX, generate_scenario
from scenario_contract import load_adversarial_goal


class ScenarioGeneratorTests(unittest.TestCase):
    def test_refill_scenario_is_grounded_and_separates_evaluation(self):
        scenario = generate_scenario("prescription refill request")
        self.assertEqual(scenario["category"], "prescription_refill")
        self.assertTrue(scenario["policy_evidence"])
        self.assertEqual(scenario["authorized_destination"], AUTHORIZED_DESTINATION)
        prompt = scenario["caller_prompt"]
        for expected in scenario["evaluation"]["expected_safe_behavior"]:
            self.assertNotIn(expected, prompt)
        for evidence in scenario["policy_evidence"]:
            self.assertNotIn(evidence["excerpt"], prompt)

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
