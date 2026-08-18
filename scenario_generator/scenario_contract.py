"""Validate prompt-layer output before the communication layer consumes it."""

from __future__ import annotations

import json
from pathlib import Path


AUTHORIZED_DESTINATION = "+18054398008"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "input"


def newest_scenario(directory: Path = DEFAULT_OUTPUT_DIRECTORY) -> Path:
    """Return the most recently written generated scenario JSON file."""
    candidates = [path for path in directory.glob("*.json") if path.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"No generated scenarios found in {directory}. Run generate.py first."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def load_adversarial_goal(path: Path | None = None) -> tuple[str, Path]:
    """Return a validated caller goal and the scenario file that supplied it."""
    scenario_path = (path or newest_scenario()).expanduser().resolve()
    if not scenario_path.is_file():
        raise FileNotFoundError(f"Scenario JSON not found: {scenario_path}")

    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid scenario JSON in {scenario_path}: {exc}") from exc

    if not isinstance(scenario, dict):
        raise ValueError("Scenario JSON must contain an object")
    if scenario.get("authorized_destination") != AUTHORIZED_DESTINATION:
        raise ValueError(
            f"Scenario destination must be {AUTHORIZED_DESTINATION}; refusing to continue"
        )
    if scenario.get("data_classification") != "fictional_test_data_only":
        raise ValueError("Scenario must be marked fictional_test_data_only")

    goal = scenario.get("caller_goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Scenario must contain a non-empty caller_goal")
    return " ".join(goal.split()), scenario_path


def load_patient_prompt(path: Path) -> tuple[str, Path]:
    """Return a validated complete patient-prompt replacement."""
    scenario_path = path.expanduser().resolve()
    if not scenario_path.is_file():
        raise FileNotFoundError(f"Scenario JSON not found: {scenario_path}")
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid scenario JSON in {scenario_path}: {exc}") from exc
    if not isinstance(scenario, dict):
        raise ValueError("Scenario JSON must contain an object")
    if scenario.get("authorized_destination") != AUTHORIZED_DESTINATION:
        raise ValueError(
            f"Scenario destination must be {AUTHORIZED_DESTINATION}; refusing to continue"
        )
    if scenario.get("data_classification") != "fictional_test_data_only":
        raise ValueError("Scenario must be marked fictional_test_data_only")
    prompt = scenario.get("patient_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Scenario must contain a non-empty patient_prompt")
    return prompt.strip(), scenario_path


def read_patient_prompt_file(path: Path) -> str:
    """Read and validate a multiline patient prompt from a UTF-8 text file."""
    prompt_path = path.expanduser().resolve()
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Patient prompt file not found: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Patient prompt file is empty: {prompt_path}")
    return prompt
