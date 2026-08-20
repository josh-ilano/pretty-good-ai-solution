import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generate import (
    AUTHORIZED_DESTINATION,
    DEFAULT_INDEX,
    DEFAULT_OUTPUT,
    RANDOM_TOPICS,
    choose_diverse_topic,
    generate_manual_patient_prompt,
    generate_random_scenario,
    generate_scenario,
)
from scenario_contract import (
    load_adversarial_goal,
    load_patient_prompt,
    read_patient_prompt_file,
)
from naming import build_run_directory_name
from call_controls import contains_ctrl_t


class ScenarioGeneratorTests(unittest.TestCase):
    def test_ctrl_t_detection_uses_the_terminal_control_character(self):
        self.assertTrue(contains_ctrl_t("abc\x14def"))
        self.assertFalse(contains_ctrl_t("t"))
        self.assertFalse(contains_ctrl_t("CTRL+T"))
        self.assertFalse(contains_ctrl_t("\x11"))

    def test_run_directory_name_uses_short_id_and_readable_safe_topic(self):
        name = build_run_directory_name(
            "Authorization/referral status: approval?",
            identifier="a1b2c3d4",
        )
        self.assertEqual(
            name,
            "a1b2c3d4_Authorization_referral_status_approval",
        )

    def test_run_directory_topic_has_a_bounded_length(self):
        name = build_run_directory_name("topic " * 100, identifier="12345678")
        identifier, topic = name.split("_", 1)
        self.assertEqual(identifier, "12345678")
        self.assertLessEqual(len(topic), 80)

    def test_default_scenario_destination_is_generated_prompt(self):
        self.assertEqual(
            DEFAULT_OUTPUT,
            Path(__file__).resolve().parents[1] / "generated_prompt",
        )

    def test_every_extracted_scenario_topic_generates_a_specialized_profile(self):
        expected_categories = {
            "demographic_correction",
            "minimum_necessary_forms",
            "patient_registration",
            "appointment_change",
            "double_booking_authority",
            "scheduling_triage_duration",
            "payment_policy",
            "urgent_access_payment",
            "late_arrival_no_show",
            "patient_abandonment",
            "directions",
            "general_policy",
            "clinical_question",
            "callback_followthrough",
            "results_or_clinical_advice",
            "prescription_refill",
            "authorization_referral",
            "formulary_pharmacy",
            "repeat_caller",
            "privacy_access",
            "secure_portal_access",
            "terminated_workforce_access",
            "provider_identifier_security",
            "privacy_incident_response",
            "compliance_investigator_verification",
            "privacy_complaint",
            "complaint_rescheduling_retaliation",
            "notice_of_privacy_practices",
            "revised_privacy_notice",
            "diagnosis_code_integrity",
        }
        generated_categories = {
            generate_scenario(topic)["category"] for topic in RANDOM_TOPICS
        }
        self.assertEqual(generated_categories, expected_categories)

    def test_random_topic_avoids_recent_rag_history(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            recently_used = RANDOM_TOPICS[:10]
            for position, topic in enumerate(recently_used):
                path = output / f"history-{position}.json"
                path.write_text(json.dumps({"test_topic": topic}), encoding="utf-8")

            with patch("generate.secrets.choice", side_effect=lambda values: values[0]):
                selected = choose_diverse_topic(output, recent_window=10)

            self.assertNotIn(selected, recently_used)

    def test_refill_scenario_is_grounded_and_compact(self):
        scenario = generate_scenario("prescription refill request")
        self.assertEqual(scenario["category"], "prescription_refill")
        self.assertTrue(scenario["policy_evidence"])
        self.assertEqual(scenario["authorized_destination"], AUTHORIZED_DESTINATION)
        self.assertEqual(set(scenario), {
            "schema_version", "scenario_id", "category", "test_topic",
            "authorized_destination", "data_classification",
            "patient_prompt", "violation_attempt", "expected_safe_behavior",
            "failure_conditions", "policy_evidence",
        })
        self.assertEqual(
            set(scenario["policy_evidence"][0]), {"chunk_id", "source_page", "text"}
        )
        self.assertTrue(scenario["policy_evidence"][0]["text"])
        self.assertTrue(scenario["patient_prompt"])
        self.assertIn("Make at most two polite attempts", scenario["patient_prompt"])
        self.assertIn("Use only fictional information", scenario["patient_prompt"])
        self.assertTrue(scenario["expected_safe_behavior"])
        self.assertTrue(scenario["failure_conditions"])
        self.assertTrue(all(
            isinstance(item, str) and item.strip()
            for item in scenario["expected_safe_behavior"]
        ))
        self.assertTrue(all(
            isinstance(item, str) and item.strip()
            for item in scenario["failure_conditions"]
        ))

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
            self.assertIn("patient_prompt", scenario)
            self.assertIn("expected_safe_behavior", scenario)
            self.assertIn("failure_conditions", scenario)

    def test_rag_generation_never_writes_to_custom_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_input = root / "custom_input"
            generated_prompt = root / "generated_prompt"
            custom_input.mkdir()
            with patch(
                "generate.secrets.choice",
                return_value="prescription refill request",
            ):
                generated = generate_random_scenario(
                    output_directory=generated_prompt
                )

            self.assertEqual(generated.parent, generated_prompt.resolve())
            self.assertEqual(list(custom_input.iterdir()), [])

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
            self.assertEqual(set(scenario), {
                "schema_version", "scenario_id", "category", "test_topic",
                "authorized_destination", "data_classification",
                "patient_prompt", "violation_attempt", "expected_safe_behavior",
                "failure_conditions", "policy_evidence",
            })
            self.assertEqual(scenario["policy_evidence"], [])
            self.assertTrue(scenario["expected_safe_behavior"])
            self.assertTrue(scenario["failure_conditions"])

    def test_custom_txt_flows_into_complete_generated_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_input = root / "custom_input"
            generated_prompt = root / "generated_prompt"
            custom_input.mkdir()
            source = custom_input / "my_test.txt"
            source.write_text(
                "You are a fictional caller. Ask once, then stop.\n",
                encoding="utf-8",
            )

            saved = generate_manual_patient_prompt(
                read_patient_prompt_file(source),
                output_directory=generated_prompt,
            )
            loaded_prompt, loaded_path = load_patient_prompt(saved)

            self.assertEqual(saved.parent, generated_prompt.resolve())
            self.assertTrue(saved.name.startswith("manual_patient_prompt-"))
            self.assertEqual(
                loaded_prompt,
                "You are a fictional caller. Ask once, then stop.",
            )
            self.assertEqual(loaded_path, saved)

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
