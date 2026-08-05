package agentcontrol.authz_test

import data.agentcontrol.authz
import rego.v1

base_input(tool, resource, trust) := {
	"agent": {"id": "test-agent"},
	"user": {"id": "tester"},
	"task": "unit-test",
	"action": {"tool": tool, "arguments": {}, "resource": resource, "tool_type": "function"},
	"context": {"trust": trust, "source": "test"},
	"evidence": {},
	"trace": {"trace_id": "0", "span_id": "0", "thread_id": "t-1"},
}

test_default_allows_benign_tool if {
	r := authz.result with input as base_input("search", "public/docs", "trusted")
	r.decision == "allow"
	r.policy_id == "agentcontrol.authz.default_allow"
}

test_destructive_tool_denied if {
	r := authz.result with input as base_input("github.delete_repository", "x/y", "trusted")
	r.decision == "deny"
	r.policy_id == "agentcontrol.authz.deny_destructive_tool"
}

test_production_resource_reviewed if {
	r := authz.result with input as base_input("github.create_issue", "company/production", "trusted")
	r.decision == "review"
	r.review_timeout_seconds == 900
}

test_high_injection_from_untrusted_denied if {
	i := object.union(
		base_input("search", "public/docs", "untrusted"),
		{"evidence": {"nemo_injection": {"score": 0.91}}},
	)
	r := authz.result with input as i
	r.decision == "deny"
	r.policy_id == "agentcontrol.authz.deny_injection_untrusted"
}

# The v0.1 case: no collectors ship, so evidence is empty. An undefined score must not
# make the rule undefined, and must not deny on its own.
test_empty_evidence_from_untrusted_allowed if {
	r := authz.result with input as base_input("search", "public/docs", "untrusted")
	r.decision == "allow"
}

test_injection_from_trusted_context_allowed if {
	i := object.union(
		base_input("search", "public/docs", "trusted"),
		{"evidence": {"nemo_injection": {"score": 0.99}}},
	)
	r := authz.result with input as i
	r.decision == "allow"
}
