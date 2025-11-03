from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import current_app

from .types import TokenUsage


class OpenAIProvider:
    """Wrapper around the OpenAI 1.x SDK with helper utilities."""

    _client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI  # type: ignore

            api_key = current_app.config.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def _coerce_usage(self, usage_obj: Any) -> TokenUsage:
        if usage_obj is None:
            return TokenUsage()
        if isinstance(usage_obj, dict):
            return TokenUsage(
                prompt_tokens=usage_obj.get("input_tokens")
                or usage_obj.get("prompt_tokens")
                or 0,
                completion_tokens=usage_obj.get("output_tokens")
                or usage_obj.get("completion_tokens")
                or 0,
            )
        prompt_tokens = getattr(usage_obj, "input_tokens", None)
        if prompt_tokens is None:
            prompt_tokens = getattr(usage_obj, "prompt_tokens", 0)
        completion_tokens = getattr(usage_obj, "output_tokens", None)
        if completion_tokens is None:
            completion_tokens = getattr(usage_obj, "completion_tokens", 0)
        return TokenUsage(prompt_tokens=prompt_tokens or 0, completion_tokens=completion_tokens or 0)

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    def generate_embeddings(
        self,
        text: str,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> Tuple[List[float], TokenUsage]:
        model_name = model or current_app.config.get("EMBEDDING_MODEL", "text-embedding-3-large")
        response = self.client.embeddings.create(
            model=model_name,
            input=text,
            dimensions=dimensions or current_app.config.get("EMBEDDING_DIM", 1024),
        )
        vector: Iterable[float]
        if hasattr(response, "data"):
            vector = response.data[0].embedding
        elif isinstance(response, dict):
            vector = response["data"][0]["embedding"]
        else:
            raise ValueError("Unexpected embeddings response structure")
        usage = self._coerce_usage(getattr(response, "usage", None) or (response.get("usage") if isinstance(response, dict) else None))
        return list(vector), usage

    # ------------------------------------------------------------------
    # Responses API helpers
    # ------------------------------------------------------------------
    def create_structured_response(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        verbosity: str = "medium",
        reasoning_effort: str = "high",
        store: bool = True,
    ) -> Tuple[Any, TokenUsage]:
        model_name = model or current_app.config.get("OPENAI_RESPONSES_MODEL", "gpt-5-mini")
        response = self.client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.get("name", "structured_output"),
                    "strict": True,
                    "schema": schema,
                },
                "verbosity": verbosity,
            },
            reasoning={"effort": reasoning_effort},
            tools=[],
            store=store,
            max_output_tokens=max_output_tokens or current_app.config.get("MAX_OUTPUT_TOKENS", 6000),
        )
        usage = self._coerce_usage(getattr(response, "usage", None))
        return response, usage

    def create_multiquery_variants(
        self,
        *,
        content: str,
        target_count: int,
        preview_limit: int,
        model: Optional[str] = None,
    ) -> Tuple[List[str], str, TokenUsage]:
        truncated = content[:preview_limit]
        schema = {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": target_count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "variant": {
                                "type": "string",
                                "minLength": 5,
                                "maxLength": 1000,
                            }
                        },
                        "required": ["variant"],
                        "additionalProperties": False,
                    },
                },
                "primary_query": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 2000,
                },
            },
            "required": ["queries", "primary_query"],
            "additionalProperties": False,
        }
        instructions = (
            "You are a retrieval assistant. Given an end-user email, emit concise search queries "
            "(<=12 words each) that capture different intents or key facts needed to answer the message. "
            f"Return JSON with a `primary_query` string and between 1 and {target_count} high-signal `queries`. "
            "Always respond in English. The `primary_query` must be a clean restatement of the user's core question "
            "without greetings, signatures, disclaimers, or boilerplate. Provide unique queries, avoid numbering, "
            "and remove duplicates."
        )
        model_name = model or current_app.config.get("OPENAI_RESPONSES_MODEL", "gpt-5-mini")
        response = self.client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": instructions}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": truncated}],
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "multi_query_variants",
                    "strict": True,
                    "schema": schema,
                },
                "verbosity": "low",
            },
            reasoning={"effort": "medium"},
            tools=[],
            store=True,
            max_output_tokens=5000,
        )
        usage = self._coerce_usage(getattr(response, "usage", None))
        variants: List[str] = []
        parsed = self._extract_structured_output(response)
        if isinstance(parsed, dict):
            for item in parsed.get("queries", []) or []:
                if not isinstance(item, dict):
                    continue
                variant = (item.get("variant") or "").strip()
                if variant:
                    variants.append(variant)
        primary_query = ""
        if isinstance(parsed, dict):
            primary_query = (parsed.get("primary_query") or "").strip()
        unique: List[str] = []
        seen = set()
        for variant in variants:
            key = variant.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(variant)
            if len(unique) >= target_count:
                break
        if not primary_query:
            primary_query = truncated.strip() or content.strip()
        return unique, primary_query, usage

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_structured_output(self, response: Any) -> Any:
        try:
            parsed = getattr(response, "output_parsed", None)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            parsed = None
        try:
            outputs = getattr(response, "output", None)
            if outputs and isinstance(outputs, (list, tuple)):
                for item in outputs:
                    content = getattr(item, "content", None)
                    if not content:
                        continue
                    for chunk in content:
                        parsed_chunk = getattr(chunk, "parsed", None)
                        if isinstance(parsed_chunk, (dict, list)):
                            return parsed_chunk
                        text_chunk = getattr(chunk, "text", None)
                        if isinstance(text_chunk, str):
                            try:
                                return json.loads(text_chunk)
                            except Exception:
                                continue
        except Exception:
            pass
        if isinstance(response, dict):
            payload = response.get("output")
            if isinstance(payload, (dict, list)):
                return payload
        return None