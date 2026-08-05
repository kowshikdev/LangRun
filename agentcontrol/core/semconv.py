"""Semantic-convention attribute names.

The `gen_ai.*` constants here are **vendored**, not imported. Every gen_ai constant in
`opentelemetry-semantic-conventions` now carries "Deprecated: Moved to the OpenTelemetry
GenAI semantic conventions repository", and that successor repository publishes YAML
models and Weaver templates but no Python package. There is nothing to depend on, so the
names are frozen here and checked against the upstream registry by
`tests/unit/test_semconv_drift.py`.

Pinned to:
  - semantic-conventions-genai @ 9af08349db7e70b2528accde90bae81d4ebcfa1e (2026-08-02)
  - which itself pins SEMCONV_VERSION=v1.43.0 (versions.env)

Source of record for the execute-tool span shape:
  refs/semantic-conventions-genai/docs/gen-ai/gen-ai-spans.md (Execute tool span)
  refs/semantic-conventions-genai/model/gen-ai/registry.yaml

The `agentcontrol.*` namespace is ours. It is stable for consumers within a major
version regardless of upstream convention churn — that stability is the entire reason
the namespace exists.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "AC_ACTION_RESOURCE",
    "AC_CAPABILITY_ENFORCEMENT",
    "AC_CONTEXT_SOURCE",
    "AC_CONTEXT_TRUST",
    "AC_CORRELATION_ORPHAN",
    "AC_EVIDENCE_PREFIX",
    "AC_POLICY_DECISION",
    "AC_POLICY_DECISION_ID",
    "AC_POLICY_FAIL_MODE",
    "AC_POLICY_ID",
    "AC_POLICY_LATENCY_MS",
    "AC_POLICY_PROVIDER",
    "AC_POLICY_REASON",
    "AC_POLICY_UNAVAILABLE",
    "AC_REVIEW_DEADLINE",
    "AC_REVIEW_HOLD_ID",
    "AC_REVIEW_REPLAY",
    "AC_REVIEW_STATE",
    "ERROR_TYPE",
    "GENAI_CONVENTIONS_COMMIT",
    "GEN_AI_AGENT_ID",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_TOOL_CALL_ARGUMENTS",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_CALL_RESULT",
    "GEN_AI_TOOL_DESCRIPTION",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_TYPE",
    "OPERATION_EXECUTE_TOOL",
    "SEMCONV_VERSION",
    "evidence_attribute",
    "span_name",
]

GENAI_CONVENTIONS_COMMIT: Final = "9af08349db7e70b2528accde90bae81d4ebcfa1e"
SEMCONV_VERSION: Final = "v1.43.0"

# --------------------------------------------------------------------- gen_ai.*

GEN_AI_OPERATION_NAME: Final = "gen_ai.operation.name"
GEN_AI_TOOL_NAME: Final = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
GEN_AI_TOOL_DESCRIPTION: Final = "gen_ai.tool.description"
GEN_AI_TOOL_TYPE: Final = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ARGUMENTS: Final = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT: Final = "gen_ai.tool.call.result"
GEN_AI_AGENT_ID: Final = "gen_ai.agent.id"
GEN_AI_AGENT_NAME: Final = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID: Final = "gen_ai.conversation.id"

#: `gen_ai.operation.name` value for a tool execution.
OPERATION_EXECUTE_TOOL: Final = "execute_tool"

#: Stable across the split, so this one is a genuine upstream constant rather than a
#: vendored name. Kept here so callers have a single import site.
ERROR_TYPE: Final = "error.type"

# --------------------------------------------------------------- agentcontrol.*

AC_POLICY_DECISION: Final = "agentcontrol.policy.decision"
AC_POLICY_PROVIDER: Final = "agentcontrol.policy.provider"
AC_POLICY_ID: Final = "agentcontrol.policy.id"
AC_POLICY_DECISION_ID: Final = "agentcontrol.policy.decision_id"
AC_POLICY_REASON: Final = "agentcontrol.policy.reason"
AC_POLICY_UNAVAILABLE: Final = "agentcontrol.policy.unavailable"
AC_POLICY_FAIL_MODE: Final = "agentcontrol.policy.fail_mode"
AC_POLICY_LATENCY_MS: Final = "agentcontrol.policy.latency_ms"

AC_CONTEXT_TRUST: Final = "agentcontrol.context.trust"
AC_CONTEXT_SOURCE: Final = "agentcontrol.context.source"
AC_ACTION_RESOURCE: Final = "agentcontrol.action.resource"
AC_CAPABILITY_ENFORCEMENT: Final = "agentcontrol.capability.enforcement"
AC_CORRELATION_ORPHAN: Final = "agentcontrol.correlation.orphan"

AC_REVIEW_STATE: Final = "agentcontrol.review.state"
AC_REVIEW_DEADLINE: Final = "agentcontrol.review.deadline"
AC_REVIEW_HOLD_ID: Final = "agentcontrol.review.hold_id"
AC_REVIEW_REPLAY: Final = "agentcontrol.review.replay"

AC_EVIDENCE_PREFIX: Final = "agentcontrol.evidence"

#: Error-type values AgentControl reports.
ERROR_TYPE_DENIED: Final = "agentcontrol.denied"
ERROR_TYPE_POLICY_UNAVAILABLE: Final = "agentcontrol.policy_unavailable"
ERROR_TYPE_REVIEW_TIMEOUT: Final = "agentcontrol.review_timeout"
ERROR_TYPE_REVIEW_REJECTED: Final = "agentcontrol.review_rejected"


def evidence_attribute(collector: str, signal: str) -> str:
    """Return the namespaced attribute key for one evidence signal."""
    return f"{AC_EVIDENCE_PREFIX}.{collector}.{signal}"


def span_name(tool: str) -> str:
    """Return the span name for a tool execution.

    Upstream: "Span name SHOULD be `execute_tool {gen_ai.tool.name}`".
    """
    return f"{OPERATION_EXECUTE_TOOL} {tool}"
