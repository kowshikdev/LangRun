# Contract: capability manifest and startup validation

**Verified against** `refs/langchain@a6b904f` and `refs/langgraph@b2926a0`.

## Manifest

The eleven fields from root `plan.md` §5, unchanged. Declared values for the v0.1 LangGraph adapter, each with the evidence that produced it:

```python
LANGGRAPH_CAPABILITIES = CapabilityManifest(
    observe_model_calls=True,        # wrap_model_call hooks — types.py:1821-1840
    observe_tool_calls=True,         # wrap_tool_call sees every dispatched call — tool_node.py:1030-1055
    intercept_model_input=True,      # ModelRequest override — types.py:85-269
    intercept_model_output=True,     # ModelResponse return — types.py:270-325
    intercept_function_tools=True,   # client-side BaseTools are what ToolNode runs — factory.py:1055-1067
    intercept_mcp_tools=True,        # MCP tools adapt to BaseTool; no MCP branch in tool_node.py — PROVEN
    intercept_hosted_tools=False,    # provider-executed tools never enter ToolNode — factory.py:1055-1067
    block_before_tool=True,          # skipping execute is the documented short-circuit — tool_node.py:1044-1055
    modify_tool_arguments=True,      # request.override(tool_call=...) — tool_node.py:170-199
    human_approval=True,             # interrupt() propagates under default handle_tool_errors — see caveat
    streaming_interception=False,    # no per-token hook on the tool path
)
```

Two entries were open questions in root `plan.md` §8 and are now settled:

- **`intercept_hosted_tools=False` is structural, not a version artifact.** `create_agent` gives `ToolNode` only `middleware_tools + regular_tools`; provider-executed tools are excluded by construction (`factory.py:1055-1067`). No LangGraph version bump changes this — it would take a change in where hosted tools execute.
- **`streaming_interception=False`** — the tool path has no per-token hook, and `wrap_model_call` observes a completed `ModelResponse`.

**`human_approval=True` carries a caveat.** `interrupt()` raises `GraphInterrupt`, which extends `Exception`, and the ToolNode wrapper call site catches bare `Exception` without the `except GraphBubbleUp: raise` guard the tool-execution path has (`tool_node.py:1054-1067` vs `:973-983`). It works because the default `handle_tool_errors` re-raises anything that is not a `ToolInvocationError` (`tool_node.py:383-392`), and `create_agent` never overrides that default (`factory.py:1061-1064`). A host that builds its own `ToolNode` with `handle_tool_errors=True`, a string, or a non-re-raising callable would silently turn a review hold into a tool error. Startup validation covers exactly that case.

## RequiredCapabilities

What the loaded policy demands:

```python
@dataclass(frozen=True)
class RequiredCapabilities:
    block_before_tool: bool = True
    human_approval: bool = False
    modify_tool_arguments: bool = False
```

**Derivation (FR-027).** Static Rego parsing to discover which decisions a bundle can return is brittle — decisions can be computed, imported, or bundled remotely. v0.1 therefore uses **declare-and-verify**:

1. The host declares `RequiredCapabilities` in `ControlPlaneConfig`. `block_before_tool=True` is the default, because a policy provider that cannot deny is not a policy provider.
2. Startup checks the declaration against the adapter manifest.
3. At runtime, a verdict the manifest says is unsupported is **denied** and raises `CapabilityMismatchError`. A wrong declaration fails loudly on first contact instead of silently under-enforcing.

An optional `strict_policy_scan=True` runs `opa eval` against the bundle to enumerate reachable decision values and cross-checks the declaration. Off by default because it needs the `opa` binary; when on, a mismatch fails startup.

## Startup validation

Runs inside `ControlPlane.attach(agent)` before the graph can execute.

| Check | Failure |
|---|---|
| Every `RequiredCapabilities` field `True` is `True` in the manifest | `CapabilityMismatchError` naming the field, the adapter, and the policy requirement (FR-028) |
| `human_approval` required ⇒ a checkpointer is configured on the compiled graph | `CapabilityMismatchError` — `interrupt()` requires one (`types.py:830-831`) |
| `human_approval` required ⇒ `ToolNode._handle_tool_errors is _default_handle_tool_errors` | `CapabilityMismatchError` — otherwise holds silently become tool errors |
| `human_approval` required ⇒ `thread_id` resolvable from runtime config | `CapabilityMismatchError` — a hold with no thread cannot be resumed |
| Policy provider configured ⇒ `policy.url` reachable on a startup probe | warning, not failure — fail-closed already covers a provider that is down later |
| Probe response omits `decision_id` | loud warning naming decision logging (research R5) |

**No downgrade path exists** (FR-029). There is no flag, env var, or config key that turns a capability mismatch into observation-only mode. That absence is itself a test: a search of the codebase for such an escape hatch must come back empty.

Error message shape:

```
CapabilityMismatchError: policy requires 'human_approval' but adapter 'langgraph' does not provide it.
  required by: ControlPlaneConfig.required_capabilities.human_approval=True
  adapter manifest: human_approval=False
  reason: no checkpointer configured on the compiled graph; interrupt() requires one
  fix: compile the graph with a checkpointer, or set required_capabilities.human_approval=False
       if no policy in the bundle returns "review"
```

Naming the specific gap is the requirement (FR-028), and it is what SC-005 measures.

## Conformance suite (Phase 4, FR-030 / SC-008)

Every field is proven by **executing a real `create_agent` graph**, never by asserting the constant against itself. One test per field:

| Field | Proof |
|---|---|
| `observe_model_calls` | model call produces the expected observation |
| `observe_tool_calls` | every dispatched tool call reaches the middleware |
| `intercept_model_input` | overridden input reaches the model |
| `intercept_model_output` | overridden output reaches the agent |
| `intercept_function_tools` | a `@tool` function is intercepted |
| `intercept_mcp_tools` | a **real MCP-backed** tool is intercepted — proven with `tests/support/mcp_echo_server.py` (a real `FastMCP` server, subprocess) via the real `langchain-mcp-adapters` client; a fake `BaseTool` would prove nothing here |
| `intercept_hosted_tools` | a hosted-tool call is confirmed **not** intercepted; declaring `False` truthfully is the pass condition |
| `block_before_tool` | tool's side effect never happens; agent receives the denial |
| `modify_tool_arguments` | tool observes the overridden arguments |
| `human_approval` | graph interrupts, resumes on `Command(resume=…)`, and auto-denies past the deadline |
| `streaming_interception` | streaming run confirms no per-token interception; `False` is truthful |

Plus the negative case SC-005 measures: `RequiredCapabilities(human_approval=True)` against a graph with no checkpointer must fail startup with the exact message above.

**Manifest drift guard**: a test enumerates `CapabilityManifest.__dataclass_fields__` and fails if any field lacks a corresponding conformance test. Adding a twelfth capability without proving it becomes impossible rather than merely discouraged.
