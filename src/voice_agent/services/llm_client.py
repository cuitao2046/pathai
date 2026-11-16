# -*- coding: utf-8 -*-
"""LLM 客户端（spec REQ-VOICE-06 / 决策 D15）：OpenAI 兼容 chat/completions。

- SSE 流式聚合，同时测量首 token 延迟（ttft_ms）与总耗时；
- 结构化输出支持 `json_object`（DeepSeek/Qwen 通用）与 `json_schema`（OpenAI 系）；
- 通过可注入 `httpx.BaseTransport` 实现单测零网络；
- 任何网络/协议/超时错误统一抛 `LlmError`，由调用方降级规则解析。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 3.0


class LlmError(Exception):
    """LLM 不可用（未配置/网络/超时/协议错误），调用方必须降级。"""


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    timeout_s: float = 15.0
    temperature: float = 0.0
    max_tokens: int = 900
    #: json_object | json_schema | none
    structured_mode: str = "json_object"
    #: 完整 trace（原文 prompt + 原文响应）：私有局域网调试期由 PATHAI_LLM_TRACE 打开
    trace: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass(frozen=True)
class LlmResult:
    text: str
    ttft_ms: int
    total_ms: int
    #: 单次调用 trace 标识，便于与 REQ/RAW 日志对齐
    trace_id: str = ""


class LlmClient:
    def __init__(self, config: LlmConfig, transport: httpx.BaseTransport | None = None):
        if not config.configured:
            raise LlmError("LLM 未配置（需要 base_url/api_key/model 三者）")
        self._config = config
        self._transport = transport

    def chat(
        self,
        messages: list[dict],
        response_schema: dict | None = None,
        schema_name: str = "voice_agent_output",
    ) -> LlmResult:
        payload: dict = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "stream": True,
        }
        response_format = self._response_format(response_schema, schema_name)
        if response_format is not None:
            payload["response_format"] = response_format

        trace_id = f"llm_{uuid.uuid4().hex[:12]}"
        if self._config.trace:
            # 全量 prompt：含 ASR 原文（user 消息）与静态规则/词典（system 消息）
            logger.info(
                "LLM_TRACE %s REQ model=%s messages=%s",
                trace_id,
                self._config.model,
                json.dumps(messages, ensure_ascii=False),
            )

        started = time.monotonic()
        ttft_ms: int | None = None
        chunks: list[str] = []
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        timeout = httpx.Timeout(self._config.timeout_s, connect=CONNECT_TIMEOUT_S)
        try:
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                with client.stream(
                    "POST", self._config.chat_url, json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        detail = response.read()[:200].decode("utf-8", "replace")
                        raise LlmError(f"HTTP {response.status_code}: {detail}")
                    for line in response.iter_lines():
                        piece = _delta_text(line)
                        if piece is None:
                            continue
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - started) * 1000)
                        chunks.append(piece)
        except httpx.HTTPError as err:
            raise LlmError(f"LLM 请求失败：{err}") from err

        total_ms = int((time.monotonic() - started) * 1000)
        text = "".join(chunks).strip()
        if not text:
            raise LlmError("LLM 返回空内容")
        if ttft_ms is None:
            ttft_ms = total_ms
        if self._config.trace:
            # 结构化响应原文：下游解析失败时据此判断是 JSON 截断还是语义偏差
            logger.info(
                "LLM_TRACE %s RAW model=%s ttft_ms=%d total_ms=%d text=%s",
                trace_id,
                self._config.model,
                ttft_ms,
                total_ms,
                text,
            )
        else:
            logger.info(
                "llm chat trace_id=%s model=%s ttft_ms=%d total_ms=%d chars=%d",
                trace_id, self._config.model, ttft_ms, total_ms, len(text),
            )
        return LlmResult(text=text, ttft_ms=ttft_ms, total_ms=total_ms, trace_id=trace_id)

    def _response_format(self, schema: dict | None, name: str) -> dict | None:
        mode = self._config.structured_mode
        if mode == "json_schema" and schema is not None:
            return {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None


def _delta_text(line: str) -> str | None:
    """解析一行 SSE；返回增量文本，非内容行返回 None。"""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError as err:
        raise LlmError(f"SSE 数据损坏：{data[:80]}") from err
    choices = event.get("choices") or []
    if not choices:
        return None
    choice = choices[0]
    delta = choice.get("delta") or {}
    piece = delta.get("content")
    if piece is None:
        message = choice.get("message") or {}
        piece = message.get("content")
    return piece or None
