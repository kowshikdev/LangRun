"""The interception point.

Implemented as an `AgentMiddleware` subclass carrying **both** `wrap_tool_call` and
`awrap_tool_call`. The `@wrap_tool_call` decorator is the obvious API and the wrong one
here: it inspects `iscoroutinefunction` and installs exactly one hook (verified:
refs/langchain/libs/langchain_v1/langchain/agents/middleware/types.py:2105-2157), after
which the other raises `NotImplementedError` (:732-742). A decorator-based async
middleware therefore breaks every host that calls `agent.invoke()`.

Blocking is achieved by simply not calling the supplied handler
(refs/langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py:1044-1055).
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from agentcontrol.adapters.langgraph import review as review_mod
from agentcontrol.adapters.langgraph.intent import build_action_intent
from agentcontrol.core.errors import CapabilityMismatchError
from agentcontrol.core.types import (
    ActionIntent,
    ControlResult,
    Evidence,
    RequiredCapabilities,
    ReviewState,
    Verdict,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentcontrol.core.config import ControlPlaneConfig
    from agentcontrol.core.otel import GovernanceRecorder
    from agentcontrol.core.types import CapabilityManifest, InlineControl, PolicyProvider

__all__ = ["AgentControlMiddleware"]

_LOG = logging.getLogger(__name__)


class _BackgroundLoop:
    """A dedicated event loop for driving async providers from a sync hook.

    Keeps one code path for sync and async agents. Started lazily so a purely async
    deployment never pays for a thread.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run a coroutine on the background loop and wait for its result."""
        loop = self._ensure_loop()
        future: concurrent.futures.Future[Any] = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="agentcontrol-policy", daemon=True
            )
            thread.start()
            self._loop, self._thread = loop, thread
            atexit.register(self.close)
            return loop

    def close(self) -> None:
        """Stop the background loop if one was started."""
        with self._lock:
            loop, self._loop = self._loop, None
            thread, self._thread = self._thread, None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)


class AgentControlMiddleware(AgentMiddleware):
    """Authorizes every tool call before it executes."""

    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        recorder: GovernanceRecorder,
        policy: PolicyProvider | None = None,
        inline_controls: Sequence[InlineControl] = (),
        capabilities: CapabilityManifest | None = None,
        required: RequiredCapabilities | None = None,
    ) -> None:
        """Wire the middleware to its providers."""
        super().__init__()
        self._config = config
        self._recorder = recorder
        self._policy = policy
        self._inline_controls = tuple(inline_controls)
        self._capabilities = capabilities
        self._required = required
        self._loop = _BackgroundLoop()
        #: hold_id -> (intent, result, hold). Lost when this middleware instance is
        #: replaced; `_recover_hold_from_state` is what survives that.
        self._pending: dict[str, tuple[ActionIntent, ControlResult, review_mod.ReviewHold]] = {}
        self._pending_lock = threading.Lock()
        #: Set by `bind_agent`. Used only to query persisted interrupt state when the
        #: in-memory `_pending` record for a hold is gone; never used to mutate the
        #: graph.
        self._agent: Any = None

    @property
    def pending_holds(self) -> dict[str, review_mod.ReviewHold]:
        """Return a snapshot of holds this process is currently tracking."""
        with self._pending_lock:
            return {key: value[2] for key, value in self._pending.items()}

    def bind_agent(self, agent: Any) -> None:
        """Give the middleware a handle to the compiled graph for hold recovery.

        Called by `ControlPlane.attach()`. Enables `_recover_hold_from_state` to read
        back a review hold's persisted deadline via `agent.aget_state()` when this
        middleware's own `_pending` record for it is gone — the graph's checkpoint
        already has the answer; this closes the gap where a lost `_pending` entry
        would otherwise let `_run_review` compute a fresh, wrong deadline.
        """
        self._agent = agent

    # --------------------------------------------------------------- LangChain hooks

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        """Synchronous interception."""
        return self._govern(request, handler, is_async=False)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Asynchronous interception."""
        return await self._agovern(request, handler)

    # ------------------------------------------------------------------- governance

    def _govern(self, request: Any, handler: Callable[[Any], Any], *, is_async: bool) -> Any:
        del is_async
        if self._policy is None:
            return handler(request)

        intent = build_action_intent(request, self._config.resolvers)
        outcome = self._loop.run(self._decide(intent, _runtime_config(request)))
        return self._act(intent, request, handler, outcome, is_coroutine=False)

    async def _agovern(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if self._policy is None:
            return await handler(request)

        intent = build_action_intent(request, self._config.resolvers)
        outcome = await self._decide(intent, _runtime_config(request))
        return await self._aact(intent, request, handler, outcome)

    async def _decide(self, intent: ActionIntent, config: Any) -> _Decision:
        """Authorize, or recover an in-flight review hold without re-authorizing."""
        replayed = self._lookup_pending(intent.hold_id)
        if replayed is not None:
            _, result, hold = replayed
            return _Decision(result=result, hold=hold, latency_ms=None, state_recovered=False)

        recovered_hold = await self._recover_hold_from_state(intent, config)

        started = time.perf_counter()
        evidence = await self._collect_evidence(intent)
        assert self._policy is not None
        result = await self._policy.authorize(intent, evidence)
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._enforce_capability(result)

        if recovered_hold is not None:
            # A prior interrupt for this exact hold exists in the graph's own
            # checkpoint. Re-authorization is safe (a policy is a pure function of
            # its input) but its deadline is not: use the one that was actually
            # persisted, never one computed fresh at recovery time.
            return _Decision(
                result=result, hold=recovered_hold, latency_ms=latency_ms, state_recovered=True
            )
        return _Decision(result=result, hold=None, latency_ms=latency_ms, state_recovered=False)

    async def _recover_hold_from_state(
        self, intent: ActionIntent, config: Any
    ) -> review_mod.ReviewHold | None:
        """Recover a hold's persisted deadline from the graph's own checkpoint.

        This is what makes deadline durability real beyond this middleware instance's
        own memory: `StateSnapshot.interrupts` carries back exactly the value passed to
        `interrupt()` originally (verified:
        refs/langgraph/libs/langgraph/langgraph/pregel/main.py:1436, `aget_state`),
        which for a review hold is `hold.to_payload()` — deadline included. Returns
        `None` when there is nothing to recover (no bound agent, no config, or no
        matching interrupt — the ordinary case for a genuine first pass).
        """
        if self._agent is None or not config:
            return None
        thread_config = _thread_level_config(config)
        if thread_config is None:
            return None
        try:
            snapshot = await self._agent.aget_state(thread_config)
        except Exception:
            _LOG.warning(
                "could not query graph state to recover hold %r; falling back to a "
                "freshly computed deadline",
                intent.hold_id,
                exc_info=True,
            )
            return None
        for pending_interrupt in snapshot.interrupts:
            candidate = review_mod.ReviewHold.from_payload(pending_interrupt.value)
            if candidate is not None and candidate.hold_id == intent.hold_id:
                return candidate
        return None

    async def _collect_evidence(self, intent: ActionIntent) -> tuple[Evidence, ...]:
        """Run inline collectors concurrently within their budget.

        A collector that raises or times out drops its signal. The signal is never
        defaulted to a passing value; the policy decides what absence means.
        """
        if not self._inline_controls:
            return ()

        budget = self._config.inline_control_budget_ms / 1000.0
        tasks = [control.collect(intent) for control in self._inline_controls]
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=budget
            )
        except (TimeoutError, asyncio.TimeoutError):
            _LOG.warning(
                "inline evidence collection exceeded its %dms budget; "
                "proceeding with those signals absent",
                self._config.inline_control_budget_ms,
            )
            return ()

        collected: list[Evidence] = []
        for control, item in zip(self._inline_controls, gathered, strict=False):
            if isinstance(item, BaseException):
                _LOG.warning(
                    "inline control %r failed (%s); its signal is absent, not passing",
                    getattr(control, "name", control),
                    item,
                )
                continue
            collected.append(item)
        return tuple(collected)

    def _enforce_capability(self, result: ControlResult) -> None:
        """Deny and fail loudly when a verdict needs a capability we do not have."""
        if self._capabilities is None:
            return
        needed = (self._required or _DEFAULT_REQUIRED).requires_for(result.verdict)
        if needed is None or self._capabilities.supports(needed):
            return
        raise CapabilityMismatchError(
            needed,
            "langgraph",
            required_by=f"policy returned {result.verdict.value!r} at runtime",
            provided=False,
            reason=(
                "the declared required_capabilities did not include this, and the "
                "adapter cannot enforce it"
            ),
            fix=f"set required_capabilities.{needed}=True and satisfy its startup checks",
        )

    # ------------------------------------------------------------------- acting

    def _act(
        self,
        intent: ActionIntent,
        request: Any,
        handler: Callable[[Any], Any],
        decision: _Decision,
        *,
        is_coroutine: bool,
    ) -> Any:
        del is_coroutine
        result = decision.result

        if result.verdict is Verdict.REVIEW:
            resolution = self._run_review(intent, decision)
            outcome = resolution.outcome
            if outcome.state is not ReviewState.APPROVED:
                return self._deny_message(intent, outcome.result)
            with self._recorder.governed_execution(
                intent,
                outcome.result,
                review_state=ReviewState.APPROVED,
                review_deadline=resolution.hold.deadline,
                replay=resolution.replay,
            ) as span:
                output = handler(request)
                self._recorder.attach_result(span, _summarize(output))
            return output

        if result.verdict is Verdict.DENY:
            self._recorder.record_decision(intent, result, latency_ms=decision.latency_ms)
            return self._deny_message(intent, result, already_recorded=True)

        with self._recorder.governed_execution(
            intent, result, latency_ms=decision.latency_ms
        ) as span:
            output = handler(request)
            self._recorder.attach_result(span, _summarize(output))
        return output

    async def _aact(
        self,
        intent: ActionIntent,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
        decision: _Decision,
    ) -> Any:
        result = decision.result

        if result.verdict is Verdict.REVIEW:
            resolution = self._run_review(intent, decision)
            outcome = resolution.outcome
            if outcome.state is not ReviewState.APPROVED:
                return self._deny_message(intent, outcome.result)
            with self._recorder.governed_execution(
                intent,
                outcome.result,
                review_state=ReviewState.APPROVED,
                review_deadline=resolution.hold.deadline,
                replay=resolution.replay,
            ) as span:
                output = await handler(request)
                self._recorder.attach_result(span, _summarize(output))
            return output

        if result.verdict is Verdict.DENY:
            self._recorder.record_decision(intent, result, latency_ms=decision.latency_ms)
            return self._deny_message(intent, result, already_recorded=True)

        with self._recorder.governed_execution(
            intent, result, latency_ms=decision.latency_ms
        ) as span:
            output = await handler(request)
            self._recorder.attach_result(span, _summarize(output))
        return output

    # -------------------------------------------------------------------- review

    def _run_review(self, intent: ActionIntent, decision: _Decision) -> _ReviewResolution:
        """Pause for a human, then resolve against the persisted deadline.

        LangGraph re-executes the node from the start on every resume, so this method
        runs at least twice per held call no matter what. Three cases, driven by what
        `_decide` found:

        - **In-memory hit** (`decision.hold` set, `not state_recovered`): the ordinary
          case — same live process, second pass after resume. The pending span was
          already emitted in pass one; do not emit it again. `interrupt()` here just
          retrieves the human's response.
        - **Recovered from graph state** (`decision.hold` set, `state_recovered`):
          this middleware's own `_pending` record was lost, but `_decide` found the
          original persisted interrupt via `_recover_hold_from_state` and re-derived
          `hold` from it — deadline included, not recomputed. `interrupt()` is
          guaranteed to return immediately here (the graph state already proved a
          prior interrupt exists), so no first-pass exception handling is needed.
          Emit a pending span marked `replay=True` for audit continuity.
        - **Nothing found** (`decision.hold is None`): a genuine first pass. Build a
          fresh hold, emit the pending span, and let `interrupt()`'s exception
          propagate to pause the graph.
        """
        hold = decision.hold
        if hold is not None and not decision.state_recovered:
            response = interrupt(hold.to_payload())
            replay = False
        elif hold is not None:
            self._remember_pending(intent, decision.result, hold)
            response = interrupt(hold.to_payload())
            self._recorder.record_decision(
                intent,
                decision.result,
                review_state=ReviewState.PENDING,
                review_deadline=hold.deadline,
                replay=True,
            )
            replay = True
        else:
            hold = review_mod.build_hold(
                intent,
                decision.result,
                fallback_timeout_seconds=self._config.review.default_timeout_seconds,
            )
            self._remember_pending(intent, decision.result, hold)
            try:
                response = interrupt(hold.to_payload())
            except Exception:
                self._recorder.record_decision(
                    intent,
                    decision.result,
                    latency_ms=decision.latency_ms,
                    review_state=ReviewState.PENDING,
                    review_deadline=hold.deadline,
                )
                raise
            self._recorder.record_decision(
                intent,
                decision.result,
                review_state=ReviewState.PENDING,
                review_deadline=hold.deadline,
                replay=True,
            )
            replay = True

        persisted = review_mod.ReviewHold.from_payload(response) or hold
        outcome = review_mod.resolve(persisted, response, decision.result)
        self._forget_pending(hold.hold_id)

        if outcome.state is not ReviewState.APPROVED:
            self._recorder.record_decision(
                intent,
                outcome.result,
                review_state=outcome.state,
                review_deadline=hold.deadline,
                replay=replay,
            )
        return _ReviewResolution(outcome=outcome, hold=hold, replay=replay)

    def _lookup_pending(
        self, hold_id: str
    ) -> tuple[ActionIntent, ControlResult, review_mod.ReviewHold] | None:
        with self._pending_lock:
            return self._pending.get(hold_id)

    def _remember_pending(
        self, intent: ActionIntent, result: ControlResult, hold: review_mod.ReviewHold
    ) -> None:
        with self._pending_lock:
            self._pending[hold.hold_id] = (intent, result, hold)

    def _forget_pending(self, hold_id: str) -> None:
        with self._pending_lock:
            self._pending.pop(hold_id, None)

    # --------------------------------------------------------------------- output

    def _deny_message(
        self,
        intent: ActionIntent,
        result: ControlResult,
        *,
        already_recorded: bool = False,
    ) -> ToolMessage:
        """Return a denial the agent can keep reasoning about rather than crash on."""
        del already_recorded
        detail = result.reason
        if result.policy_id:
            detail = f"{detail} (policy: {result.policy_id})"
        return ToolMessage(
            content=f"AgentControl denied this tool call. {detail}",
            name=intent.tool,
            tool_call_id=intent.tool_call_id or "",
            status="error",
        )

    def close(self) -> None:
        """Release the background loop, if one was started."""
        self._loop.close()


class _Decision:
    """Internal carrier for one authorization pass."""

    __slots__ = ("hold", "latency_ms", "result", "state_recovered")

    def __init__(
        self,
        *,
        result: ControlResult,
        hold: review_mod.ReviewHold | None,
        latency_ms: int | None,
        state_recovered: bool,
    ) -> None:
        self.result = result
        self.hold = hold
        self.latency_ms = latency_ms
        #: True when `hold` was recovered from the graph's persisted interrupt state
        #: rather than found in this middleware's own in-memory `_pending` dict.
        self.state_recovered = state_recovered


class _ReviewResolution:
    """Internal carrier for the outcome of one review hold."""

    __slots__ = ("hold", "outcome", "replay")

    def __init__(
        self,
        *,
        outcome: review_mod.ReviewOutcome,
        hold: review_mod.ReviewHold,
        replay: bool,
    ) -> None:
        self.outcome = outcome
        self.hold = hold
        self.replay = replay


def _summarize(output: Any) -> Any:
    """Reduce a handler result to something safe to put on a span."""
    content = getattr(output, "content", None)
    return content if content is not None else output


def _runtime_config(request: Any) -> Any:
    """Extract the RunnableConfig from a ToolCallRequest, for `aget_state` lookups."""
    return getattr(getattr(request, "runtime", None), "config", None)


def _thread_level_config(config: Any) -> dict[str, Any] | None:
    """Reduce a tool-task's RunnableConfig to a bare thread-level config.

    `request.runtime.config` inside `ToolNode`'s push-task execution carries a nested
    `checkpoint_ns` scoped to that task (e.g. `tools:<task-id>`), not the top-level
    thread. Querying `aget_state` with the task-scoped config silently returns an
    unrelated (typically empty) snapshot rather than raising, so the pending interrupt
    at the *thread* level — where `interrupt()` actually recorded it — never surfaces.
    Stripping down to just `thread_id` is what makes the lookup land on the same state
    a human resuming via `Command(resume=...)` against the bare thread config would see.
    """
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    if thread_id is None:
        return None
    return {"configurable": {"thread_id": thread_id}}


_DEFAULT_REQUIRED = RequiredCapabilities()
