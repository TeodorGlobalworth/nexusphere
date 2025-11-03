from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import current_app

from app.services.ai_providers import OpenAIProvider
from app.services.ai_providers.types import TokenUsage

from .usage_tracker import UsageTracker


@dataclass
class GenerationResult:
    success: bool
    response_text: str
    response_json: Optional[Dict[str, Any]]
    usage: TokenUsage
    token_breakdown: Dict[str, Dict[str, int]]


class ResponseExecutor:
    """Encapsulates interaction with the OpenAI Responses API and response parsing."""

    def __init__(
        self,
        *,
        provider: OpenAIProvider,
        usage_tracker: UsageTracker,
    ) -> None:
        self.provider = provider
        self.usage_tracker = usage_tracker

    # ------------------------------------------------------------------

    def execute(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        bundle: Dict[str, Any],
        max_completion_tokens: int,
        debug: bool = False,
        out_dir: Optional[str] = None,
    ) -> GenerationResult:
        debug_out = self._resolve_debug_out(debug, out_dir)

        model_name = bundle.get('response_model') or current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini')
        bundle['response_model'] = model_name

        if debug:
            self._write_debug_inputs(debug_out, system_prompt, user_prompt, bundle.get('context_docs') or [])

        schema = current_app.config.get('EMAIL_RESPONSE_JSON_SCHEMA')
        verbosity_level = self._determine_verbosity()
        response, usage = self.provider.create_structured_response(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            model=model_name,
            max_output_tokens=current_app.config.get('MAX_OUTPUT_TOKENS', 6000),
            verbosity=verbosity_level,
            reasoning_effort='high',
            store=True,
        )

        if debug:
            self._write_debug_output(debug_out, response)

        self.usage_tracker.track('response', usage)
        bundle['token_usage_breakdown'] = self.usage_tracker.snapshot()

        response_text, response_json = self._extract_response_body(response)

        return GenerationResult(
            success=True,
            response_text=response_text,
            response_json=response_json if isinstance(response_json, dict) else None,
            usage=usage,
            token_breakdown=self.usage_tracker.snapshot(),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _determine_verbosity(self) -> str:
        allowed = {'low', 'medium', 'high'}
        config_value = (current_app.config.get('OPENAI_RESPONSES_VERBOSITY') or '').strip().lower()
        if config_value in allowed:
            return config_value
        return 'medium'

    def _resolve_debug_out(self, debug: bool, out_dir: Optional[str]) -> Optional[Path]:
        if not debug:
            return None
        if out_dir:
            p = Path(out_dir)
            p.mkdir(parents=True, exist_ok=True)
            return p
        try:
            import tempfile

            return Path(tempfile.mkdtemp(prefix='ai_debug_'))
        except Exception:
            return None

    def _write_debug_inputs(self, debug_out: Optional[Path], system_prompt: str, user_prompt: str, context_docs: Any) -> None:
        if not debug_out:
            return
        try:
            (debug_out / 'request_system_prompt.txt').write_text(system_prompt, encoding='utf-8')
            (debug_out / 'request_user_prompt.txt').write_text(user_prompt, encoding='utf-8')
            (debug_out / 'request_context.json').write_text(json.dumps(context_docs, default=str, indent=2), encoding='utf-8')
        except Exception:
            pass

    def _write_debug_output(self, debug_out: Optional[Path], response: Any) -> None:
        if not debug_out:
            return
        try:
            try:
                payload = response if isinstance(response, dict) else getattr(response, '__dict__', None)
            except Exception:
                payload = None
            serialized = payload if isinstance(payload, (dict, list)) else str(response)
            (debug_out / 'response_raw.txt').write_text(json.dumps(serialized, default=str, indent=2) if isinstance(serialized, (dict, list)) else str(serialized), encoding='utf-8')
        except Exception:
            pass

    # ------------------------------------------------------------------
    # response parsing helpers
    # ------------------------------------------------------------------

    def _extract_response_body(self, response: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
        parsed = self._extract_structured_output(response)
        if isinstance(parsed, dict):
            body = self._extract_email_body(parsed)
            if body:
                return body, parsed
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    body = self._extract_email_body(item)
                    if body:
                        return body, item

        for chunk in self._iter_response_content_chunks(response):
            parsed_chunk = getattr(chunk, 'parsed', None)
            if isinstance(parsed_chunk, dict):
                body = self._extract_email_body(parsed_chunk)
                if body or parsed_chunk:
                    return body, parsed_chunk
            elif isinstance(parsed_chunk, list):
                for item in parsed_chunk:
                    if isinstance(item, dict):
                        body = self._extract_email_body(item)
                        if body or item:
                            return body, item
            if isinstance(chunk, dict):
                parsed_dict = chunk.get('parsed')
                if isinstance(parsed_dict, dict):
                    body = self._extract_email_body(parsed_dict)
                    if body or parsed_dict:
                        return body, parsed_dict
                elif isinstance(parsed_dict, list):
                    for item in parsed_dict:
                        if isinstance(item, dict):
                            body = self._extract_email_body(item)
                            if body or item:
                                return body, item

        for text in self._iter_response_texts(response):
            parsed_dict = self._safe_json_loads(text)
            if isinstance(parsed_dict, dict):
                body = self._extract_email_body(parsed_dict)
                return body, parsed_dict
            if isinstance(parsed_dict, list):
                for item in parsed_dict:
                    if isinstance(item, dict):
                        body = self._extract_email_body(item)
                        return body, item
            stripped = text.strip()
            if stripped:
                return stripped, None

        return '', None

    def _extract_structured_output(self, response: Any) -> Any:
        parsed = getattr(response, 'output_parsed', None)
        if isinstance(parsed, (dict, list)):
            return parsed

        for chunk in self._iter_response_content_chunks(response):
            parsed_chunk = getattr(chunk, 'parsed', None)
            if isinstance(parsed_chunk, (dict, list)):
                return parsed_chunk
            if isinstance(chunk, dict):
                parsed_dict = chunk.get('parsed')
                if isinstance(parsed_dict, (dict, list)):
                    return parsed_dict
            text_chunk = getattr(chunk, 'text', None)
            if isinstance(text_chunk, str):
                parsed_dict = self._safe_json_loads(text_chunk)
                if isinstance(parsed_dict, (dict, list)):
                    return parsed_dict
            if isinstance(chunk, dict):
                parsed_dict = self._safe_json_loads(chunk.get('text'))
                if isinstance(parsed_dict, (dict, list)):
                    return parsed_dict

        if isinstance(response, dict):
            payload = response.get('output')
            if isinstance(payload, (dict, list)):
                return payload
        return None

    def _iter_response_content_chunks(self, response: Any):
        outputs = getattr(response, 'output', None)
        if isinstance(outputs, (list, tuple)):
            for item in outputs:
                content = getattr(item, 'content', None)
                if isinstance(content, (list, tuple)):
                    for chunk in content:
                        yield chunk
                elif isinstance(content, dict):
                    yield content
        if isinstance(response, dict):
            for item in response.get('output') or []:
                content = (item or {}).get('content')
                if isinstance(content, list):
                    for chunk in content:
                        yield chunk

    def _iter_response_texts(self, response: Any):
        for chunk in self._iter_response_content_chunks(response):
            text_value = getattr(chunk, 'text', None)
            if isinstance(text_value, str):
                yield text_value
            elif isinstance(chunk, dict):
                text_value = chunk.get('text')
                if isinstance(text_value, str):
                    yield text_value
        text_attr = getattr(response, 'output_text', None)
        if isinstance(text_attr, str):
            yield text_attr
        if isinstance(response, dict):
            text_attr = response.get('output_text')
            if isinstance(text_attr, str):
                yield text_attr

    def _extract_email_body(self, parsed: Any) -> str:
        if not isinstance(parsed, dict):
            return ''
        email_section = parsed.get('email')
        if isinstance(email_section, dict):
            body = email_section.get('body')
            if isinstance(body, str):
                return body.strip()
        body = parsed.get('body')
        if isinstance(body, str):
            return body.strip()
        return ''

    def _safe_json_loads(self, value: Any) -> Optional[Any]:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return json.loads(value)
        except Exception:
            return None
