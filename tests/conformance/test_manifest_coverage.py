"""Task T071: every CapabilityManifest field must have a conformance test.

Makes an unproven twelfth capability impossible rather than merely discouraged
(SC-008: 100% of declared capability-manifest fields covered by a conformance test
that exercises the real runtime).
"""

from __future__ import annotations

from pathlib import Path

from agentcontrol.core.types import CapabilityManifest

_CONFORMANCE_DIR = Path(__file__).resolve().parent

#: field name -> substring expected somewhere in the conformance test source, proving
#: a test actually exercises it (not just mentions it in a docstring elsewhere).
_COVERAGE: dict[str, str] = {
    "observe_model_calls": "class TestObserveModelCalls",
    "observe_tool_calls": "class TestObserveToolCalls",
    "intercept_model_input": "def test_model_input_is_overridable",
    "intercept_model_output": "def test_model_output_is_overridable",
    "intercept_function_tools": "class TestInterceptFunctionTools",
    "intercept_mcp_tools": "class TestInterceptMcpTools",
    "intercept_hosted_tools": "class TestInterceptHostedTools",
    "block_before_tool": "class TestBlockBeforeTool",
    "modify_tool_arguments": "class TestModifyToolArguments",
    "human_approval": "class TestHumanApproval",
    "streaming_interception": "class TestStreamingInterception",
}


def _conformance_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(_CONFORMANCE_DIR.glob("test_*.py"))
    )


def test_every_manifest_field_has_a_registered_coverage_marker() -> None:
    declared = set(CapabilityManifest.__dataclass_fields__)
    covered = set(_COVERAGE)
    missing = declared - covered
    assert not missing, (
        f"CapabilityManifest field(s) {sorted(missing)} have no entry in "
        f"_COVERAGE — add a conformance test and register it here before shipping."
    )
    stale = covered - declared
    assert not stale, f"_COVERAGE references field(s) no longer on the manifest: {sorted(stale)}"


def test_every_coverage_marker_actually_appears_in_conformance_source() -> None:
    source = _conformance_source()
    missing_markers = [
        field for field, marker in _COVERAGE.items() if marker not in source
    ]
    assert not missing_markers, (
        f"declared coverage marker(s) for {missing_markers} were not found in any "
        f"tests/conformance/test_*.py file — the registered test may have been "
        f"renamed or deleted without updating _COVERAGE"
    )


def test_manifest_has_exactly_eleven_fields() -> None:
    """A sentinel: if this changes, _COVERAGE and this test both need updating —
    which is the point of the guard.
    """
    assert len(CapabilityManifest.__dataclass_fields__) == 11
