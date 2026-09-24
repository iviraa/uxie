"""Bedrock Converse client with usage accounting. Swappable for a fake in tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3
from botocore.config import Config as BotoConfig


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add(self, raw: dict[str, Any]) -> None:
        self.input_tokens += int(raw.get("inputTokens", 0))
        self.output_tokens += int(raw.get("outputTokens", 0))
        self.cache_read_tokens += int(raw.get("cacheReadInputTokens", 0))
        self.cache_write_tokens += int(raw.get("cacheWriteInputTokens", 0))
        self.calls += 1

    def cost_usd(self, price_in: float, price_out: float) -> float:
        # cache reads bill at 0.1x input, cache writes at 1.25x
        eff_in = self.input_tokens + 0.1 * self.cache_read_tokens + 1.25 * self.cache_write_tokens
        return (eff_in * price_in + self.output_tokens * price_out) / 1_000_000


@dataclass
class Turn:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: dict[str, Any] = field(default_factory=dict)

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b["toolUse"] for b in self.content if "toolUse" in b]

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content)


class ChatModel(Protocol):
    def converse(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> Turn: ...


class BedrockModel:
    def __init__(self, model_id: str, region: str | None = None) -> None:
        self.model_id = model_id
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 8, "mode": "adaptive"}),
        )

    def converse(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> Turn:
        tool_config: dict[str, Any] = {"tools": tools}
        if tool_choice:
            tool_config["toolChoice"] = tool_choice
        resp = self._client.converse(
            modelId=self.model_id,
            system=system,
            messages=messages,
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
        return Turn(
            content=resp["output"]["message"]["content"],
            stop_reason=resp.get("stopReason", ""),
            usage=resp.get("usage", {}),
        )
