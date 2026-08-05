"""Task T072: no flag, env var, or config key exists that turns a capability
mismatch into observation-only mode.

The absence of such an escape hatch is itself required by Constitution Principle II
and must be tested, not assumed.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "agentcontrol"

#: Names that would signal a downgrade escape hatch if they existed anywhere in the
#: package: a flag/setting that lets a capability mismatch become a warning instead
#: of a startup failure.
_FORBIDDEN_SUBSTRINGS = (
    "observation_only",
    "observationonly",
    "degraded_mode",
    "degradedmode",
    "skip_capability_check",
    "skipcapabilitycheck",
    "allow_mismatch",
    "allowmismatch",
    "ignore_capability",
    "ignorecapability",
    "soft_fail_capability",
    "capability_warning_only",
)


def _all_identifiers() -> set[str]:
    """Collect every identifier (names, attributes, string literals) in the package."""
    identifiers: set[str] = set()
    for path in _PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())
            elif isinstance(node, ast.arg):
                identifiers.add(node.arg.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                identifiers.add(node.value.lower())
    return identifiers


def test_no_downgrade_identifier_exists_anywhere_in_the_package() -> None:
    identifiers = _all_identifiers()
    hits = {
        forbidden
        for forbidden in _FORBIDDEN_SUBSTRINGS
        if any(forbidden in identifier for identifier in identifiers)
    }
    assert not hits, (
        f"found identifier(s) suggesting a capability-mismatch downgrade path: "
        f"{sorted(hits)}. Constitution Principle II forbids any flag, env var, or "
        f"config key that converts a capability mismatch into a warning."
    )


def test_capability_mismatch_error_always_raises_never_returns_a_status() -> None:
    """Structural check: CapabilityMismatchError is an exception, not a result type
    a caller could inspect-and-ignore.
    """
    from agentcontrol.core.errors import CapabilityMismatchError

    assert issubclass(CapabilityMismatchError, Exception)


def test_attach_has_no_boolean_parameter_that_could_suppress_validation() -> None:
    """ControlPlane.attach() takes only the agent — no `strict=`, `enforce=`, or
    similar parameter that a caller could set to False to bypass validation.
    """
    import inspect

    from agentcontrol.core.control_plane import ControlPlane

    signature = inspect.signature(ControlPlane.attach)
    params = [name for name in signature.parameters if name != "self"]
    assert params == ["agent"]
