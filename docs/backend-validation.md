# Backend validation: Langfuse and Phoenix round-trip

Procedure for SC-004 (a denial is explainable from the UI alone) and SC-009 (the same governance record renders in two independent backends), per [quickstart.md Scenario 5](../specs/001-agentcontrol-runtime-governance/quickstart.md#scenario-5--explain-a-denial-from-the-backend-alone-phase-3).

**Status: procedure defined, not yet executed.** This implementation session had no Docker available, so the live round-trip into a real Phoenix or Langfuse instance has not been run. Everything up to the OTLP export boundary is unit- and integration-tested (`tests/unit/test_governance_attributes.py`, `tests/integration/test_correlation.py`, `tests/integration/test_span_cardinality.py`) against an in-memory exporter — what remains unverified is specifically whether a real backend renders the `agentcontrol.*` attributes usably, not whether AgentControl produces them correctly.

Worth keeping in mind while running this: [research.md §R10](../specs/001-agentcontrol-runtime-governance/research.md) found that AgentControl's OPA client had a real endpoint-path bug that every mocked test missed, and it only surfaced once a real `opa` server was actually run. The OTLP export path is exactly as untested against a real backend right now — treat this procedure as similarly likely to surface something a mock couldn't, not as a formality.

## Procedure

1. Bring up the stack:
   ```bash
   docker compose up -d   # OPA on :8181 (decision logging on), Phoenix on :6006 / :4318
   ```
2. Run a denial with export enabled:
   ```bash
   python examples/governed_agent.py --mode deny --export otlp
   ```
3. Open `http://localhost:6006`. Locate the `execute_tool github.delete_repository`-equivalent span for this run.
4. **SC-004 check**: hand the trace to someone unfamiliar with the codebase. They must be able to answer, from the Phoenix UI alone — no application logs, no source code — all of:
   - Which agent attempted the action, and on whose behalf (`gen_ai.agent.id`, `agentcontrol.context.trust`/`.source`)
   - What was attempted (`gen_ai.tool.name`, `agentcontrol.action.resource`)
   - Why it was blocked (`agentcontrol.policy.reason`, `agentcontrol.policy.id`)
   - Whether the runtime was capable of enforcing it at all (`agentcontrol.capability.enforcement`)
5. Repeat steps 2–4 pointed at a Langfuse instance's OTLP ingestion endpoint instead (`OTEL_EXPORTER_OTLP_ENDPOINT` pointed at Langfuse; see [Langfuse's OTel ingestion docs](https://langfuse.com/integrations/native/opentelemetry), cited in root `plan.md` §12).
6. **SC-009 check**: confirm the same span, exported from the same run, shows the same `agentcontrol.*` fields in both UIs — not just the generic `gen_ai.*` ones.

## Expected span content

Full attribute reference: [contracts/otel-attributes.md](../specs/001-agentcontrol-runtime-governance/contracts/otel-attributes.md), including the two worked examples (denied action, OPA-unreachable fallback) this procedure should reproduce visually in each backend's UI.

## Recording results

When this procedure is run, record the outcome here: which backend, which version, screenshot or export reference, and whether all SC-004 fields were locatable without leaving the UI. Until then, this file documents intent, not a completed check — do not treat SC-004/SC-009 as closed on the strength of this document alone.
