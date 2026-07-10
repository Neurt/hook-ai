"""LLM client.

`OpenRouterLLM` is the real client (OpenAI SDK pointed at OpenRouter).
`FakeLLM` is an offline stand-in for tests/smoke runs (no network, no key).
Both implement the small `LLM` interface so specialists are provider-agnostic.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings


class LLMError(RuntimeError):
    pass


class LLMParseError(LLMError):
    """The model responded, but not with parseable JSON. Retryable — a fresh
    completion usually parses. API/auth/model errors are NOT this and must not
    be retried (each retry is a paid call that will fail identically)."""


class LLM:
    """Minimal interface the specialists depend on."""

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, retries: int = 2, **kw: Any) -> Any:
        """Ask for JSON and parse it, retrying ONLY on malformed output. Some models
        (e.g. Gemini) occasionally emit invalid JSON even in json mode; a fresh
        completion almost always parses. API errors (auth, bad model, rate limit)
        propagate immediately — retrying those just repeats a paid, identical failure."""
        err: LLMParseError | None = None
        for _ in range(max(retries, 0) + 1):
            text = self.chat(system, user, json_mode=True, **kw)  # API errors raise here
            try:
                return extract_json(text)
            except LLMParseError as exc:
                err = exc
        assert err is not None
        raise err

    def read_image(self, image_data_url: str, prompt: str, **kw: Any) -> str:
        """Transcribe/describe an image (needs a vision-capable model)."""
        raise NotImplementedError("This LLM does not support image input.")


class OpenRouterLLM(LLM):
    """OpenAI-compatible client targeting OpenRouter."""

    def __init__(self, settings: Settings):
        self.settings = settings
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise LLMError(
                "The 'openai' package is required for live calls. "
                "Run: pip install -r requirements.txt"
            ) from e
        self._client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.request_timeout,
        )

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.referer:
            headers["HTTP-Referer"] = self.settings.referer
        if self.settings.title:
            headers["X-Title"] = self.settings.title
        return headers

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = dict(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.settings.temperature if temperature is None else temperature,
            max_tokens=self.settings.max_tokens if max_tokens is None else max_tokens,
        )
        headers = self._extra_headers()
        if headers:
            kwargs["extra_headers"] = headers
        if json_mode:
            # Honored by many OpenRouter models; ignored harmlessly by others.
            kwargs["response_format"] = {"type": "json_object"}
        # Disable provider reasoning/"thinking": these are deterministic JSON/format
        # tasks, so thinking only eats the max_tokens budget (truncating output) and
        # adds cost. Ignored by models without reasoning.
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # surface model-slug / auth errors clearly
            raise LLMError(
                f"OpenRouter request failed (model={self.settings.model!r}): {e}"
            ) from e
        return (resp.choices[0].message.content or "").strip()

    def read_image(self, image_data_url: str, prompt: str, max_tokens: int = 2500) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    }
                ],
                max_tokens=max_tokens,
                extra_body={"reasoning": {"enabled": False}},
            )
        except Exception as e:  # vision unsupported model / API error
            raise LLMError(
                f"OpenRouter vision request failed (model={self.settings.model!r}): {e}"
            ) from e
        return (resp.choices[0].message.content or "").strip()


class FakeLLM(LLM):
    """Offline stand-in. Returns a canned response whose key (a task tag) appears
    in the system prompt. Used by smoke_test.py so the pipeline runs with no key."""

    def __init__(self, scripted: dict[str, str] | None = None, default: str = "{}"):
        self.scripted = scripted or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, **kw: Any) -> str:
        self.calls.append((system, user))
        for tag, response in self.scripted.items():
            if tag in system:
                return response
        return self.default


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating code fences and surrounding prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise LLMParseError(f"Expected JSON but got:\n{text[:500]}")
