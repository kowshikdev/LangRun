"""Open Policy Agent policy provider.

Contract: `POST {url}/v1/data/{path}` with `{"input": ...}`, response is OPA's
`DataResponseV1` — `{"decision_id": ..., "result": {...}}` (verified:
refs/opa/v1/server/types/types.go:242-282).

`decision_id` is `omitempty` and is generated only when the decision-log plugin is
configured (refs/opa/v1/runtime/runtime.go:930-938), so the rule identifier is carried
in `result.policy_id` instead. Without that, the audit id would be empty on any OPA
started without decision logging.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from agentcontrol.core.config import PolicyConfig
from agentcontrol.core.errors import PolicyUnavailableError
from agentcontrol.core.types import ActionIntent, ControlResult, Evidence, Verdict
from agentcontrol.providers.policy.base import unavailable_result

__all__ = ["OPAPolicyProvider"]

_LOG = logging.getLogger(__name__)

_ENFORCEABLE = {v.value for v in Verdict.enforceable()}


class OPAPolicyProvider:
    """Synchronous-before-execution authorization against OPA's data API."""

    name = "opa"

    def __init__(
        self,
        config: PolicyConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store config and build a pooled client unless one is supplied."""
        self._config = config
        self._url = config.decision_url
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._warned_missing_decision_id = False

    async def authorize(
        self, event: ActionIntent, evidence: Sequence[Evidence] = ()
    ) -> ControlResult:
        """Authorize an intent. Never raises; failures become unavailable results."""
        payload = {"input": event.to_policy_input(evidence)}
        try:
            response = await self._client.post(
                self._url, json=payload, timeout=self._config.timeout_seconds
            )
        except httpx.TimeoutException as exc:
            return self._unavailable(f"timeout after {self._config.timeout_ms}ms ({exc!r})")
        except httpx.HTTPError as exc:
            return self._unavailable(f"{type(exc).__name__}: {exc}")

        if response.status_code // 100 != 2:
            return self._unavailable(f"HTTP {response.status_code}")

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return self._unavailable(f"response body is not JSON: {exc}")

        if not isinstance(body, dict):
            return self._unavailable("response body is not a JSON object")

        # An undefined Rego document yields HTTP 200 with no `result`. Reading that as
        # allow would silently disable enforcement, so it is a provider failure.
        result = body.get("result")
        if not isinstance(result, dict):
            return self._unavailable(
                "policy document is undefined (no `result` in a 200 response); "
                f"check that the bundle defines `{self._config.path.replace('/', '.')}.result`"
            )

        decision = result.get("decision")
        if decision not in _ENFORCEABLE:
            return self._unavailable(
                f"policy returned decision {decision!r}, expected one of {sorted(_ENFORCEABLE)}"
            )

        decision_id = body.get("decision_id") or None
        if decision_id is None and not self._warned_missing_decision_id:
            self._warned_missing_decision_id = True
            _LOG.warning(
                "OPA response has no decision_id, so governance records will carry no "
                "provider audit id. Start OPA with a decision-log plugin configured "
                "(for example --set decision_logs.console=true) to enable it."
            )

        return self._build_result(result, evidence, decision_id)

    async def health(self) -> None:
        """Probe the provider, raising `PolicyUnavailableError` when unreachable."""
        try:
            response = await self._client.get(
                f"{self._config.url.rstrip('/')}/health",
                timeout=self._config.timeout_seconds,
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network dependent
            raise PolicyUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code // 100 != 2:  # pragma: no cover - network dependent
            raise PolicyUnavailableError(f"HTTP {response.status_code} from /health")

    async def aclose(self) -> None:
        """Close the client when this provider owns it."""
        if self._owns_client:
            await self._client.aclose()

    # ----------------------------------------------------------------- internals

    def _unavailable(self, reason: str) -> ControlResult:
        return unavailable_result(self.name, reason, fail_mode=self._config.fail_mode)

    def _build_result(
        self,
        result: dict[str, Any],
        evidence: Sequence[Evidence],
        decision_id: str | None,
    ) -> ControlResult:
        verdict = Verdict(result["decision"])
        timeout = result.get("review_timeout_seconds")
        if verdict is Verdict.REVIEW and not isinstance(timeout, int):
            return self._unavailable(
                "policy returned 'review' without an integer review_timeout_seconds; "
                "the review window must come from policy"
            )
        return ControlResult(
            verdict=verdict,
            provider=self.name,
            reason=str(result.get("reason") or f"policy returned {verdict.value}"),
            evidence=tuple(evidence),
            policy_id=_optional_str(result.get("policy_id")),
            decision_id=decision_id,
            review_timeout_seconds=timeout if verdict is Verdict.REVIEW else None,
        )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
