"""The `ControlPlane` entrypoint and its startup validation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from agentcontrol.core.config import ControlPlaneConfig
from agentcontrol.core.otel import GovernanceRecorder
from agentcontrol.core.types import AsyncAnalyzer, InlineControl, PolicyProvider

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opentelemetry.sdk.trace import TracerProvider

    from agentcontrol.core.types import FrameworkAdapter

__all__ = ["ControlPlane"]

_LOG = logging.getLogger(__name__)


class ControlPlane:
    """Registers governance with an agent, and validates that it can be enforced."""

    def __init__(
        self,
        *,
        config: ControlPlaneConfig | None = None,
        policy: PolicyProvider | None = None,
        adapter: FrameworkAdapter | None = None,
        inline_controls: Sequence[InlineControl] = (),
        analyzers: Sequence[AsyncAnalyzer] = (),
        own_tracer_provider: bool = False,
        tracer_provider: TracerProvider | None = None,
    ) -> None:
        """Build a control plane. With no providers it is behavior-neutral.

        `tracer_provider` is an explicit injection point, distinct from
        `own_tracer_provider`: pass a `TracerProvider` this call does not own (and will
        not shut down) to pin governance spans to it regardless of the process-global
        ambient provider. Primarily for tests and for hosts that manage more than one
        provider; the default (neither argument set) still rides the ambient global,
        per `providers/tracing/otlp.py`'s no-hijacking rule.
        """
        self.config = config or ControlPlaneConfig()
        self.policy = policy
        self.adapter = adapter
        self.inline_controls = tuple(inline_controls)
        # No analyzer ships in v0.1; the interface exists so v0.2 plugins need no
        # core change. Anything registered here runs after export, never in the hot path.
        self.analyzers = tuple(analyzers)

        from agentcontrol.providers.tracing.otlp import TRACER_NAME, build_tracer

        if tracer_provider is not None:
            tracer, provider = tracer_provider.get_tracer(TRACER_NAME), None
        else:
            tracer, provider = build_tracer(
                self.config.telemetry, own_provider=own_tracer_provider
            )
        self._tracer_provider = provider
        self._recorder = GovernanceRecorder(
            tracer,
            self.config.telemetry,
            enforcement_capable=bool(
                adapter is not None and adapter.capabilities.block_before_tool
            ),
        )
        self._middleware: Any | None = None
        self._agent: Any = None
        self._watchdog: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ middleware

    @property
    def middleware(self) -> Any:
        """Return the framework middleware to register with the agent.

        Register it **first** in the middleware list: middleware compose
        first-defined-outermost, so anything ahead of it could re-enter the tool
        without authorization.
        """
        if self._middleware is None:
            from agentcontrol.adapters.langgraph.middleware import (
                AgentControlMiddleware,
            )

            self._middleware = AgentControlMiddleware(
                config=self.config,
                recorder=self._recorder,
                policy=self.policy,
                inline_controls=self.inline_controls,
                capabilities=self.adapter.capabilities if self.adapter else None,
                required=self.config.required_capabilities,
            )
        return self._middleware

    # ------------------------------------------------------------------- lifecycle

    def attach(self, agent: Any) -> Any:
        """Validate the agent against the policy's enforcement needs and return it.

        Raises `CapabilityMismatchError` on any gap. There is no degraded mode.
        """
        self._agent = agent
        if self.policy is None:
            # Nothing to enforce, nothing to validate. This is the zero-config
            # passthrough case and must stay behavior-neutral.
            return agent

        if self.adapter is not None:
            self.adapter.validate(agent, self.config.required_capabilities)  # type: ignore[attr-defined]
            agent = self.adapter.wrap(agent)

        # Lets the middleware recover a review hold's persisted deadline via
        # agent.aget_state() when its own in-memory record of it is gone — see
        # AgentControlMiddleware.bind_agent.
        self.middleware.bind_agent(agent)

        if self.config.required_capabilities.human_approval:
            self._start_watchdog()
        return agent

    async def aclose(self) -> None:
        """Stop the watchdog, close providers, and flush spans. Idempotent."""
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None

        if self._middleware is not None:
            self._middleware.close()

        if self.policy is not None:
            await self.policy.aclose()

        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()

    # -------------------------------------------------------------------- watchdog

    def _start_watchdog(self) -> None:
        """Auto-resolve expired holds while this process is alive.

        This is the liveness half of the review timeout. The correctness half is the
        absolute deadline persisted inside the interrupt payload, which holds even when
        this task never runs. A hold in a dead process stays pending — it can never
        become an ALLOW, but nothing actively denies it either. That gap is documented
        in docs/review-holds.md and deferred to v0.2.
        """
        if not self.config.review.watchdog_enabled or self._watchdog is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _LOG.debug(
                "no running event loop at attach(); the review watchdog will not start. "
                "Expired holds still deny at resume time."
            )
            return
        self._watchdog = loop.create_task(self._watchdog_loop(), name="agentcontrol-review")

    async def _watchdog_loop(self) -> None:
        from agentcontrol.adapters.langgraph.review import TIMEOUT_SENTINEL

        interval = self.config.review.watchdog_poll_seconds
        while True:
            await asyncio.sleep(interval)
            middleware = self._middleware
            agent = self._agent
            if middleware is None or agent is None:
                continue
            for hold_id, hold in middleware.pending_holds.items():
                if not hold.is_expired():
                    continue
                thread_id = hold_id.split(":", 1)[0]
                await self._resume_expired(agent, thread_id, TIMEOUT_SENTINEL)

    @staticmethod
    async def _resume_expired(agent: Any, thread_id: str, sentinel: str) -> None:
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}
        try:
            if hasattr(agent, "ainvoke"):
                await agent.ainvoke(Command(resume=sentinel), config)
            else:  # pragma: no cover - sync-only graphs
                await asyncio.to_thread(agent.invoke, Command(resume=sentinel), config)
        except Exception:
            _LOG.exception(
                "review watchdog failed to auto-resolve the expired hold on thread %r; "
                "the hold stays pending and can still never become an approval",
                thread_id,
            )
