"""Contract tests: the OPA request/response shapes match the published schemas.

Task T026. Schemas: specs/001-agentcontrol-runtime-governance/contracts/
{opa-input,opa-result}.schema.json
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from agentcontrol.core.types import ActionIntent, ContextTrust, Evidence

_CONTRACTS = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-agentcontrol-runtime-governance"
    / "contracts"
)
_INPUT_SCHEMA = json.loads((_CONTRACTS / "opa-input.schema.json").read_text(encoding="utf-8"))
_RESULT_SCHEMA = json.loads((_CONTRACTS / "opa-result.schema.json").read_text(encoding="utf-8"))


def _intent(**overrides: object) -> ActionIntent:
    defaults: dict[str, object] = {
        "agent_id": "finance-agent",
        "tool": "github.delete_repository",
        "arguments": {"repo": "company/production"},
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "resource": "company/production",
        "context_trust": ContextTrust.UNTRUSTED,
        "context_source": "sharepoint_document",
        "thread_id": "t-42",
    }
    defaults.update(overrides)
    return ActionIntent(**defaults)  # type: ignore[arg-type]


class TestInputContract:
    def test_default_intent_matches_input_schema(self) -> None:
        payload = {"input": _intent().to_policy_input()}
        jsonschema.validate(payload["input"], _INPUT_SCHEMA)

    def test_intent_with_evidence_matches_input_schema(self) -> None:
        evidence = [Evidence(collector="nemo_injection", signal="score", value=0.91)]
        payload = _intent().to_policy_input(evidence)
        jsonschema.validate(payload, _INPUT_SCHEMA)

    def test_minimal_intent_matches_input_schema(self) -> None:
        minimal = ActionIntent(
            agent_id="a",
            tool="noop",
            arguments={},
            trace_id="0" * 32,
            span_id="0" * 16,
        )
        jsonschema.validate(minimal.to_policy_input(), _INPUT_SCHEMA)


class TestResultContract:
    @pytest.mark.parametrize(
        "response",
        [
            {"result": {"decision": "allow", "reason": "fine", "policy_id": "p.allow"}},
            {
                "decision_id": "b1f2",
                "result": {"decision": "deny", "reason": "blocked", "policy_id": "p.deny"},
            },
            {
                "result": {
                    "decision": "review",
                    "reason": "hold",
                    "policy_id": "p.review",
                    "review_timeout_seconds": 900,
                }
            },
        ],
    )
    def test_valid_opa_responses_pass_schema(self, response: dict) -> None:
        jsonschema.validate(response, _RESULT_SCHEMA)

    def test_review_without_timeout_fails_schema(self) -> None:
        response = {"result": {"decision": "review", "reason": "hold", "policy_id": "p.review"}}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(response, _RESULT_SCHEMA)

    def test_missing_result_fails_schema(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"decision_id": "x"}, _RESULT_SCHEMA)
