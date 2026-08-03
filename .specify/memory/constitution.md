<!--
SYNC IMPACT REPORT
Version change: (unratified template) → 1.0.0
Rationale: MAJOR/initial. First ratification. The file was the unmodified Spec Kit template with
every principle still a placeholder, so this is an initial adoption rather than an amendment.

Principles added (6):
  [PRINCIPLE_1_NAME] → I. Fail-Closed by Default
  [PRINCIPLE_2_NAME] → II. No Silent Degradation
  [PRINCIPLE_3_NAME] → III. Upstream Interfaces Are Verified, Never Remembered
  [PRINCIPLE_4_NAME] → IV. Capabilities Are Proven, Not Declared
  [PRINCIPLE_5_NAME] → V. Authorization Thresholds Live Only in Policy
  (new)              → VI. Standard Telemetry, No Parallel Schema

Sections filled:
  [SECTION_2_NAME]  → Enforcement & Security Constraints
  [SECTION_3_NAME]  → Development Workflow & Quality Gates
  Governance        → amendment procedure, versioning policy, compliance review

Removed sections: none.

Follow-up TODOs: none. RATIFICATION_DATE set to the adoption date rather than deferred, since
this project's first commit is 2026-08-03 and no earlier adoption exists.

Downstream note: specs/001-agentcontrol-runtime-governance/plan.md recorded its Constitution
Check as "VACANT — no gates enforced". That plan predates this file and should be re-checked
against Principles I-VI. Known tension to resolve, not to dilute: the plan's declare-and-verify
capability model (RequiredCapabilities) must be reconciled with Principle IV.
-->

# AgentControl Constitution

## Core Principles

### I. Fail-Closed by Default

When the authorization path cannot produce a trustworthy answer, the answer is `DENY`.

- Any policy-provider failure — unreachable, timed out, non-2xx, unparseable body, absent
  result document, or a verdict outside the defined set — MUST resolve to `DENY`.
- Provider failure MUST be recorded distinctly from a policy-authored denial. Conflating the
  two makes an outage indistinguishable from a rule, and destroys the audit trail's meaning.
- A fail-open mode MAY exist, but MUST be off by default, MUST be enabled explicitly per
  deployment, and MUST be logged loudly on every single use — never once at startup.
- An unanswered human-review hold MUST NOT resolve to approval under any circumstance,
  including process restart, timer loss, or checkpoint replay.

**Rationale**: This library exists to sit between an agent and a consequential action. A
governance layer that opens the gate when it is confused is worse than no governance layer,
because the operator believes they are protected.

### II. No Silent Degradation

A capability the system cannot deliver MUST be a loud, immediate failure.

- A mismatch between what the loaded policy requires and what the active adapter provides MUST
  be a hard startup failure naming the specific missing capability and the fix.
- The system MUST NOT fall back to an observation-only, reduced, or best-effort mode.
- No flag, environment variable, or configuration key may convert a capability mismatch into a
  warning. The absence of such an escape hatch is itself testable, and MUST be tested.
- Degraded operation discovered at runtime MUST deny the affected action, not permit it.

**Rationale**: False assurance is this product's worst possible failure. An operator who
believes enforcement is active while it silently is not is in a strictly worse position than
one who knows they have none.

### III. Upstream Interfaces Are Verified, Never Remembered

Every third-party call signature, attribute name, protocol field, and behavioral assumption MUST
be verified against a real source before use.

- Verification order: the vendored read-only clones in `refs/`, then the project's documentation
  MCP servers, then official published documentation.
- Any design document or code comment that asserts an upstream behavior MUST cite where it was
  verified — `refs/<path>:<line>` or the doc source.
- An unverified upstream detail in a planning artifact is a planning defect, not a detail to be
  settled during implementation.
- Deprecated upstream surfaces MUST NOT be built upon. When a needed convention has moved and
  has no consumable package, vendor the constants and add a drift test against the source.

**Rationale**: This project composes four fast-moving upstream projects whose pre-stable APIs
change between releases. Code written from memory compiles, runs, and is wrong.

### IV. Capabilities Are Proven, Not Declared

A declared capability is a claim. Only an executed test is evidence.

- Every field of every adapter capability manifest MUST be proven by a test that exercises the
  real runtime and observes the real effect.
- Asserting a declared value against itself is not a test and MUST NOT count toward coverage.
- Negative declarations (`False`) MUST be proven too: the test passes by confirming the runtime
  genuinely cannot do the thing.
- Adding a manifest field without a corresponding proof MUST fail the test suite.

**Rationale**: Every capability claim in this system is load-bearing for an authorization
decision. A manifest that is merely aspirational produces enforcement that is merely
aspirational.

### V. Authorization Thresholds Live Only in Policy

Any value that changes an authorization outcome MUST live in the versioned policy bundle.

- Numeric cutoffs, allowlists, denylists, and review windows MUST be defined in policy, reviewed
  and versioned like any other policy change.
- Provider and application configuration MAY control how a signal is produced. They MUST NOT
  control the consequence of that signal.
- A default in application configuration is permissible only as a fallback for a policy that is
  silent, never as an override of a policy that is not.

**Rationale**: A threshold that can be changed outside policy review is a policy change with no
policy review. Splitting the cutoff from the rule splits accountability from the decision.

### VI. Standard Telemetry, No Parallel Schema

Governance evidence rides on OpenTelemetry, extending the GenAI semantic conventions.

- Standard `gen_ai.*` attributes MUST be used wherever the conventions define them.
- Governance-specific fields MUST live under the `agentcontrol.*` namespace, which is stable for
  consumers within a major version regardless of upstream convention churn.
- The system MUST NOT define an independent event schema, and MUST NOT require a
  vendor-specific integration to read its output.
- Correlation MUST use the W3C trace context already propagated by OpenTelemetry. No separate
  product-specific run identifier may be minted.
- Governance records MUST NOT be lost to sampling. A sampled-away denial is an unauditable
  denial.

**Rationale**: The value of this layer is that existing tracing, eval, and policy tools reason
about the same execution. A bespoke schema would rebuild the fragmentation the project exists to
remove.

## Enforcement & Security Constraints

- **Single authorization authority.** Evidence collectors produce structured evidence and never
  a verdict. Exactly one policy provider authorizes, denies, or holds a request. Multiple
  independently-voting providers are prohibited — the resulting behavior is undefined by
  construction.
- **Hot path is bounded.** Anything that can block a live tool call MUST have an explicit
  timeout budget. Anything without one runs on the asynchronous path, after export, and can
  never block execution.
- **Missing evidence is missing, not passing.** A collector that fails or exceeds its budget
  drops its signal. The signal MUST NOT be defaulted to a value that permits an action; the
  policy decides what absence means.
- **Sensitive payloads are opt-in.** Tool arguments and results MUST default to absent from
  telemetry. A decision record MUST remain fully explainable without them.
- **Trust is never assumed.** Unsupplied or unrecognized context trust resolves to `unknown`,
  never to `trusted`.
- **Errors do not escape the hot path.** Provider failures become verdicts, not exceptions.
  An exception escaping the interception layer can be reinterpreted by the host framework as an
  ordinary tool error, silently converting a governance failure into a retryable one.

## Development Workflow & Quality Gates

- **Phase gates are sequential.** A milestone's acceptance criteria MUST pass before work on the
  next milestone begins. Gates are defined in the feature's plan, not negotiated during
  implementation.
- **Tests precede implementation** for any behavior with a stated acceptance criterion. The test
  MUST fail first; a test that never failed has proven nothing.
- **Traceability.** Every task MUST cite the requirement, research decision, or contract that
  produced it. A task with no anchor is scope creep and MUST be removed or justified.
- **Spec, plan, and tasks stay reconciled.** When verified reality contradicts the
  specification, the specification MUST be amended. Implementing the plan while quietly failing
  a literal reading of the spec is prohibited.
- **Public API stability.** Within `0.x`, the documented public surface may change only with an
  explicit note. Signature changes to exported symbols MUST be called out in review.
- **Type hints and return types are required** on all public functions, with Google-style
  docstrings.

## Governance

This constitution supersedes ad-hoc practice. Where a plan, task list, or review comment
conflicts with a principle here, the principle wins and the conflicting artifact is amended.

**Amendment procedure.** Amendments MUST be proposed as a documented change to this file,
stating the principle affected, the rationale, and the migration impact on existing specs, plans,
and code. An amendment is adopted when merged. Principles MUST NOT be diluted, reinterpreted, or
silently ignored to unblock work in progress — if a principle is genuinely wrong, amend it
explicitly.

**Versioning policy.** Semantic versioning applies to this document:
- **MAJOR** — a principle is removed or redefined in a backward-incompatible way.
- **MINOR** — a principle or section is added, or guidance is materially expanded.
- **PATCH** — clarification, wording, or typo fixes with no semantic change.

**Compliance review.** Every plan MUST record a Constitution Check before research and re-check
it after design, naming the principles it was evaluated against. A violation MUST be justified in
the plan's Complexity Tracking table or the design MUST change; an empty justification is not a
pass. Reviews MUST verify compliance, and complexity MUST be justified rather than assumed.

**Version**: 1.0.0 | **Ratified**: 2026-08-04 | **Last Amended**: 2026-08-04
