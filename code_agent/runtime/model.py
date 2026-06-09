# code_agent/runtime/model.py

from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic


@dataclass
class ModelResponse:
    content: Any
    stop_reason: str

"""
Protocol 来自 Python 的类型系统
任何对象，只要有 generate 这个方法，并且方法签名符合要求，就可以被当成 ModelProvider 使用。类似于接口
"""
class ModelProvider(Protocol):
    def generate(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> ModelResponse:
        ...


class AnthropicProvider:
    def __init__(self, client: Anthropic, model_id: str):
        self.client = client
        self.model_id = model_id

    def generate(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> ModelResponse:
        response = self.client.messages.create(
            model=self.model_id,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

        return ModelResponse(
            content=response.content,
            stop_reason=response.stop_reason,
        )