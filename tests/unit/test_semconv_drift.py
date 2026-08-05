"""Drift test: vendored gen_ai.* constants against the upstream registry.

`opentelemetry-semantic-conventions` marks its entire gen_ai surface deprecated and
moved to semantic-conventions-genai, which publishes no Python package — see
agentcontrol/core/semconv.py's module docstring. This test is the insurance policy: it
reads the actual upstream YAML (when refs/ is present; it is gitignored) and fails if a
vendored name drifts from it. Task T013.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcontrol.core import semconv

pytest.importorskip("yaml")
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY = (
    _REPO_ROOT
    / "refs"
    / "semantic-conventions-genai"
    / "model"
    / "gen-ai"
    / "registry.yaml"
)

pytestmark = pytest.mark.skipif(
    not _REGISTRY.exists(),
    reason="refs/semantic-conventions-genai is gitignored and not present in this checkout",
)

# id -> vendored constant. `gen_ai.tool.call.arguments`/`.result` are checked too even
# though they are Opt-In and off by default, because a rename there would be just as
# silent.
_VENDORED_BY_ID = {
    "gen_ai.operation.name": semconv.GEN_AI_OPERATION_NAME,
    "gen_ai.tool.name": semconv.GEN_AI_TOOL_NAME,
    "gen_ai.tool.call.id": semconv.GEN_AI_TOOL_CALL_ID,
    "gen_ai.tool.description": semconv.GEN_AI_TOOL_DESCRIPTION,
    "gen_ai.tool.type": semconv.GEN_AI_TOOL_TYPE,
    "gen_ai.tool.call.arguments": semconv.GEN_AI_TOOL_CALL_ARGUMENTS,
    "gen_ai.tool.call.result": semconv.GEN_AI_TOOL_CALL_RESULT,
    "gen_ai.agent.id": semconv.GEN_AI_AGENT_ID,
    "gen_ai.agent.name": semconv.GEN_AI_AGENT_NAME,
    "gen_ai.conversation.id": semconv.GEN_AI_CONVERSATION_ID,
}


def _registered_attribute_ids() -> set[str]:
    """Return every `key` in the registry's flat `attributes` list.

    Verified shape (refs/semantic-conventions-genai/model/gen-ai/registry.yaml): a top
    level `{"file_format": ..., "attributes": [...]}`, each entry keyed by `key`, not
    `id` — `id` is used inside enum `members`, which is a different thing.
    """
    with _REGISTRY.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return {
        attribute["key"]
        for attribute in document.get("attributes", [])
        if "key" in attribute
    }


def test_every_vendored_attribute_exists_upstream() -> None:
    registered = _registered_attribute_ids()
    missing = {
        attr_id for attr_id in _VENDORED_BY_ID if attr_id not in registered
    }
    assert not missing, (
        f"vendored gen_ai.* attributes no longer found in the upstream registry: "
        f"{sorted(missing)}. Either the pin in semconv.py is stale or the name changed."
    )


def test_vendored_names_match_their_ids() -> None:
    # The vendored constant's *value* must equal the registry id it claims to mirror —
    # this is the actual drift check, independent of whether the id still exists.
    for attr_id, vendored_value in _VENDORED_BY_ID.items():
        assert vendored_value == attr_id
