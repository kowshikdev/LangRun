package agentcontrol.authz

import rego.v1

# AgentControl reads a result *object*, not a bare decision string. The reason and the
# rule identifier have to travel with the verdict so a governance record is explainable
# without reading application logs, and the review window has to come from policy
# because a value that changes an authorization outcome may not live in provider config.

default result := {
	"decision": "allow",
	"reason": "no rule matched; default allow",
	"policy_id": "agentcontrol.authz.default_allow",
	"review_timeout_seconds": 900,
}

# Thresholds live here, versioned and reviewed like any other policy change.
injection_block_threshold := 0.8

review_window_seconds := 900

# No evidence collector ships in v0.1, so `input.evidence.nemo_injection.score` is
# undefined and a direct reference would make this whole rule undefined rather than
# false. object.get defaults it to 0, which is safe: no injection signal means no
# injection-based denial, while the trust check still applies.
result := {
	"decision": "deny",
	"reason": sprintf(
		"injection score %.2f exceeds %.2f for untrusted context",
		[score, injection_block_threshold],
	),
	"policy_id": "agentcontrol.authz.deny_injection_untrusted",
	"review_timeout_seconds": review_window_seconds,
} if {
	score := object.get(input, ["evidence", "nemo_injection", "score"], 0)
	score > injection_block_threshold
	input.context.trust == "untrusted"
}

result := {
	"decision": "deny",
	"reason": "destructive tool is blocked unconditionally",
	"policy_id": "agentcontrol.authz.deny_destructive_tool",
	"review_timeout_seconds": review_window_seconds,
} if {
	input.action.tool == "github.delete_repository"
}

result := {
	"decision": "review",
	"reason": "write against a production resource requires human approval",
	"policy_id": "agentcontrol.authz.review_production_resource",
	"review_timeout_seconds": review_window_seconds,
} if {
	input.action.resource == "company/production"
	input.action.tool != "github.delete_repository"
}
