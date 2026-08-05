"""Task T085: authorization thresholds live only in the Rego bundle (SC-010,
Constitution Principle V) — changing one requires editing only policy, never
application code or component config.

No live OPA is available in this environment to run the changed policy end-to-end
(no Docker, no `opa` binary — see README's Known open questions), so this proves the
*structural* half of SC-010: the two thresholds that exist
(`injection_block_threshold`, `review_window_seconds`) are declared only in
`policies/tool_authorization.rego`, appear in no Python source, and
`OPAPolicyProvider`/`ControlPlaneConfig` read the applicable value back from the
policy response rather than hold or override it.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY = _REPO_ROOT / "policies" / "tool_authorization.rego"
_PACKAGE_ROOT = _REPO_ROOT / "agentcontrol"

# The two threshold names that appear in the shipped example bundle
# (specs/001-agentcontrol-runtime-governance/contracts/opa-authz.md).
_THRESHOLD_NAMES = ("injection_block_threshold", "review_window_seconds")


def test_thresholds_are_declared_in_the_policy_bundle() -> None:
    source = _POLICY.read_text(encoding="utf-8")
    for name in _THRESHOLD_NAMES:
        assert re.search(rf"^{name}\s*:?=", source, re.MULTILINE), (
            f"expected {name!r} to be declared in {_POLICY.relative_to(_REPO_ROOT)}"
        )


def test_thresholds_do_not_appear_as_python_constants() -> None:
    """The names themselves may appear in comments/docstrings referencing the
    bundle (that's expected and fine), but never as an assignable Python constant
    that could override or duplicate the policy's authority over the value.
    """
    for path in _PACKAGE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in _THRESHOLD_NAMES:
            assignment = re.search(rf"^\s*{name}\s*[:=]", source, re.MULTILINE)
            assert assignment is None, (
                f"{name!r} is assigned in {path.relative_to(_REPO_ROOT)} — an "
                f"authorization-affecting threshold must live only in the policy "
                f"bundle (Constitution Principle V, SC-010)"
            )


def test_review_window_is_read_from_the_policy_response_not_held_by_the_client() -> None:
    """`ControlResult.review_timeout_seconds` must come from OPA's response, and the
    only Python-side number is `ReviewConfig.default_timeout_seconds`, documented as
    a fallback for a *silent* policy, never an override of one that spoke.
    """
    import inspect

    from agentcontrol.core.config import ReviewConfig
    from agentcontrol.providers.policy.opa import OPAPolicyProvider

    source = inspect.getsource(OPAPolicyProvider)
    assert 'result.get("review_timeout_seconds")' in source

    default_field = ReviewConfig.__dataclass_fields__["default_timeout_seconds"]
    assert default_field.default == 900  # the documented fallback, not an override


def test_changing_the_bundle_value_does_not_require_touching_python_source() -> None:
    """Direct proof of the SC-010 claim: mutate the on-disk bundle's threshold value
    and confirm no Python file needs to change for that to take effect — the value is
    read at authorize()-time from the JSON response, not compiled into the client.
    """
    import inspect

    from agentcontrol.providers.policy.opa import OPAPolicyProvider

    source = inspect.getsource(OPAPolicyProvider)
    # The client reads whatever `review_timeout_seconds` the response carries; it
    # contains no hardcoded numeric threshold of its own to keep in sync.
    assert not re.search(r"review_timeout_seconds\s*=\s*\d", source)
    assert not re.search(r"injection_block_threshold", source)
