"""A minimal scripted chat model for driving a real `create_agent` graph in tests.

Modeled on `refs/langchain/libs/langchain_v1/tests/unit_tests/agents/model.py`'s
`FakeToolCallingModel`, trimmed to what this test suite needs: implementing only
`_generate` and `bind_tools` on `BaseChatModel` is enough to get both sync `invoke` and
async `ainvoke` for free (the base class provides the async wrapper), which is what
lets Foundational and US1-US4 tests exercise the real interception path rather than a
stand-in for it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field

__all__ = ["ScriptedToolCallingModel"]


class ScriptedToolCallingModel(BaseChatModel):
    """Replays a fixed sequence of tool calls, then answers with plain content.

    `script` is a list of tool-call batches, one per model turn. Once exhausted, the
    model emits a plain `AIMessage` with no tool calls, ending the run.
    """

    script: list[list[ToolCall]] = Field(default_factory=list)
    index: int = 0

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Return the next scripted turn, or a final answer once exhausted."""
        del stop, run_manager, kwargs
        calls = self.script[self.index] if self.index < len(self.script) else []
        message = AIMessage(
            content="" if calls else "done",
            id=str(self.index),
            tool_calls=[dict(call) for call in calls],
        )
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Accept any tool set; the script decides what gets called, not binding."""
        del tools, tool_choice
        return self.bind(**kwargs)


def tool_call(name: str, args: dict[str, Any], call_id: str) -> ToolCall:
    """Build a `ToolCall` dict, the shape `ScriptedToolCallingModel.script` expects."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}
