import json
import logging
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app import db
from app.models.ai_usage_log import AIUsageLog
from app.services.ai_providers import OpenAIProvider, TokenUsage
from app.services.ai_components import (
    PromptBuilder,
    PromptBuildOptions,
    ResponseExecutor,
    RetrievalPipeline,
    UsageTracker,
)
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService
import tiktoken
# from sqlalchemy import text  # no longer used here

class AIService:
    def __init__(self):
        self.openai = OpenAIProvider()
        self.client = self.openai.client
        self.vector_service = VectorService()
        self.rerank_service = RerankService()
        self.usage_tracker = UsageTracker()
        try:
            logger = current_app.logger
        except Exception:
            import logging

            logger = logging.getLogger(__name__)

        self.retrieval_pipeline = RetrievalPipeline(
            vector_service=self.vector_service,
            rerank_service=self.rerank_service,
            openai_provider=self.openai,
            usage_tracker=self.usage_tracker,
            logger=logger,
        )
        self.prompt_builder = PromptBuilder(
            retrieval_pipeline=self.retrieval_pipeline,
            usage_tracker=self.usage_tracker,
        )
        self.response_executor = ResponseExecutor(
            provider=self.openai,
            usage_tracker=self.usage_tracker,
        )
        # Initialize tokenizer for token counting with a safe fallback
        self.encoder = None
        try:
            cfg = current_app.config
        except Exception:
            cfg = None

        cfg_get = getattr(cfg, 'get', lambda *_: None)

        tokenizer_model_candidates = [
            cfg_get('OPENAI_RESPONSES_MODEL'),
            cfg_get('VISION_MODEL'),
            'gpt-5-mini',
            'gpt-5-nano',
        ]
        for candidate in tokenizer_model_candidates:
            if not candidate:
                continue
            try:
                self.encoder = tiktoken.encoding_for_model(candidate)
                break
            except Exception:
                continue

        if self.encoder is None:
            for fallback_name in ('o200k_base', 'cl100k_base'):
                try:
                    self.encoder = tiktoken.get_encoding(fallback_name)
                    break
                except Exception:
                    continue

        if self.encoder is None:
            raise RuntimeError('Unable to initialize tokenizer encoder')
        # default debug flag (methods accept per-call debug flag)
        self.debug_default = False

    @staticmethod
    def _safe_monthly_usage(project) -> int:
        with suppress(Exception):
            return project.get_monthly_tokens_used()
        return 0

    @staticmethod
    def _resolve_context_filenames(file_ids: List[int]) -> List[str]:
        if not file_ids:
            return []
        with suppress(Exception):
            from app.models.knowledge_file import KnowledgeFile as _KF

            return [row.original_filename for row in _KF.query.filter(_KF.id.in_(file_ids)).all()]
        return []
        
    def _reset_usage(self) -> None:
        """Reset usage tracking across retrieval and generation stages."""
        self.usage_tracker.reset()
        self.retrieval_pipeline.reset_state()

    def _track_usage(self, channel: str, usage: Optional[TokenUsage]) -> None:
        """Track usage for a pipeline channel via the shared tracker."""
        self.usage_tracker.track(channel, usage)

    def _usage_snapshot(self) -> Dict[str, Dict[str, int]]:
        return self.usage_tracker.snapshot()

    def build_email_prompt(
        self,
        project_id: int,
        email_content: str,
        *,
        style_hint: str = 'standard',
        explicit_language_code: Optional[str] = None,
        max_context_docs: int = 6,
        max_chars_per_doc: int = 20000,  # Increased to avoid truncation
        max_chars_per_example: int = 800,
        vector_top_k: Optional[int] = None,
        multi_query_mode: Optional[str] = None,
        multi_query_model: Optional[str] = None,
        multi_query_variants: Optional[int] = None,
        multi_query_aggregate_top_k: Optional[int] = None,
        rerank_provider: Optional[str] = None,
        rerank_model: Optional[str] = None,
        rerank_top_k: Optional[int] = None,
        rerank_threshold: Optional[float] = None,
        retrieval_threshold: Optional[float] = None,
        response_model: Optional[str] = None,
        prefetch_limit: Optional[int] = None,
        colbert_candidates: Optional[int] = None,
        rrf_k: Optional[int] = None,
        rrf_weights: Optional[Dict[str, Any]] = None,
        hybrid_per_vector_limit: Optional[int] = None,
        hybrid_rrf_k: Optional[int] = None,
        include_neighbor_chunks: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Assemble a structured prompt bundle for mailbox/email reply generation."""

        max_context_docs = max(1, int(max_context_docs or 1))
        self._reset_usage()

        # Backwards compatibility with legacy field names
        if prefetch_limit is None and hybrid_per_vector_limit is not None:
            prefetch_limit = hybrid_per_vector_limit
        if rrf_k is None and hybrid_rrf_k is not None:
            rrf_k = hybrid_rrf_k

        if include_neighbor_chunks is None:
            try:
                include_neighbor_chunks = bool(current_app.config.get('INCLUDE_NEIGHBOR_CHUNKS_DEFAULT', True))
            except Exception:
                include_neighbor_chunks = True

        options = PromptBuildOptions(
            style_hint=style_hint,
            explicit_language_code=explicit_language_code,
            max_context_docs=max_context_docs,
            max_chars_per_doc=max_chars_per_doc,
            max_chars_per_example=max_chars_per_example,
            vector_top_k=vector_top_k,
            multi_query_mode=multi_query_mode,
            multi_query_model=multi_query_model,
            multi_query_variants=multi_query_variants,
            multi_query_aggregate_top_k=multi_query_aggregate_top_k,
            rerank_provider=rerank_provider,
            rerank_model=rerank_model,
            rerank_top_k=rerank_top_k,
            rerank_threshold=rerank_threshold,
            retrieval_threshold=retrieval_threshold,
            response_model=response_model,
            prefetch_limit=prefetch_limit,
            colbert_candidates=colbert_candidates,
            rrf_k=rrf_k,
            rrf_weights=rrf_weights,
            hybrid_per_vector_limit=hybrid_per_vector_limit,
            hybrid_rrf_k=hybrid_rrf_k,
            include_neighbor_chunks=bool(include_neighbor_chunks),
        )

        bundle = self.prompt_builder.build(
            project_id=project_id,
            email_content=email_content,
            options=options,
        )

        bundle_data = bundle.data
        if 'context_file_ids' in bundle_data:
            bundle_data.setdefault('context_file_names', self._resolve_context_filenames(bundle_data.get('context_file_ids') or []))
        else:
            bundle_data['context_file_names'] = []

        return bundle_data

    def _make_debug_out(self, out_dir: str | None) -> Path | None:
        if out_dir:
            p = Path(out_dir)
            p.mkdir(parents=True, exist_ok=True)
            return p
        try:
            return Path(tempfile.mkdtemp(prefix='ai_debug_'))
        except Exception:
            return None

    def generate_response(
        self,
        project_id: int,
        email_content: str,
        temperature: str = 'standard',
        max_completion_tokens: int = 4000,
        language_instruction: Optional[str] = None,
        debug: bool = False,
        out_dir: Optional[str] = None,
        prompt_bundle: Optional[Dict[str, Any]] = None,
        response_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate AI response using GPT Responses API with structured output handling."""

        try:
            if prompt_bundle is None:
                prompt_bundle = self.build_email_prompt(
                    project_id=project_id,
                    email_content=email_content,
                    style_hint=temperature,
                    explicit_language_code=None,
                )
            else:
                existing_breakdown = prompt_bundle.get('token_usage_breakdown') or {}
                self._reset_usage()
                for channel, usage_dict in existing_breakdown.items():
                    self._track_usage(channel, TokenUsage.from_dict(usage_dict))

            model_default = current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini')
            if response_model:
                prompt_bundle['response_model'] = response_model
            else:
                prompt_bundle.setdefault('response_model', model_default)

            if language_instruction:
                prompt_bundle['language_instruction'] = language_instruction

            system_prompt = prompt_bundle.get('system_prompt') or ''
            user_prompt = prompt_bundle.get('user_prompt') or ''
            context_docs = prompt_bundle.get('context_docs') or []

            if not context_docs:
                fallback_message = (current_app.config.get('EMAIL_RESPONSE_NO_CONTEXT_MESSAGE') or "BRAK DANYCH - UZUPEŁNIJ BAZE WIEDZY").strip()
                metadata = {
                    'fallback_reason': 'no_context_documents',
                    'response_model': prompt_bundle.get('response_model'),
                    'context_used': 0,
                    'token_breakdown': self._usage_snapshot(),
                }
                self._log_usage_entry(
                    project_id=project_id,
                    source='email_analysis',
                    model=prompt_bundle.get('response_model') or model_default,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    metadata=metadata,
                )

                result: Dict[str, Any] = {
                    'success': True,
                    'response': fallback_message,
                    'response_json': {'email': {'body': fallback_message}},
                    'tokens_input': 0,
                    'tokens_output': 0,
                    'tokens_total': 0,
                    'context_used': 0,
                    'fallback_applied': True,
                    'token_usage_breakdown': self._usage_snapshot(),
                }
                self._apply_bundle_metadata(result, prompt_bundle)
                return result

            execution = self.response_executor.execute(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                bundle=prompt_bundle,
                max_completion_tokens=max_completion_tokens,
                debug=debug,
                out_dir=out_dir,
            )

            prompt_tokens = execution.usage.prompt_tokens
            completion_tokens = execution.usage.completion_tokens
            total_tokens = execution.usage.total_tokens

            metadata = {
                'context_used': len(context_docs),
                'multi_query_used': prompt_bundle.get('multi_query_used'),
                'multi_query_variants': prompt_bundle.get('multi_query_variants'),
                'multi_query_usage': prompt_bundle.get('multi_query_usage') or self.retrieval_pipeline.last_multi_query_usage,
                'multi_query_aggregate_limit': prompt_bundle.get('multi_query_aggregate_limit'),
                'multi_query_mode': prompt_bundle.get('multi_query_mode'),
                'multi_query_variant_count': prompt_bundle.get('multi_query_variant_count'),
                'multi_query_model': prompt_bundle.get('multi_query_model'),
                'retrieval_top_k': prompt_bundle.get('retrieval_top_k'),
                'retrieval_threshold': prompt_bundle.get('retrieval_threshold'),
                'rerank_usage': prompt_bundle.get('rerank_usage') or self.retrieval_pipeline.last_rerank_usage,
                'rerank_provider': prompt_bundle.get('rerank_provider'),
                'rerank_model': prompt_bundle.get('rerank_model'),
                'rerank_top_k': prompt_bundle.get('rerank_top_k'),
                'rerank_threshold': prompt_bundle.get('rerank_threshold'),
                'rerank_settings': prompt_bundle.get('rerank_settings'),
                'context_limit': prompt_bundle.get('context_limit'),
                'token_breakdown': execution.token_breakdown,
                'style': prompt_bundle.get('resolved_style'),
                'language': prompt_bundle.get('target_language_code'),
                'context_file_ids': prompt_bundle.get('context_file_ids') or [],
            }

            self._log_usage_entry(
                project_id=project_id,
                source='email_analysis',
                model=prompt_bundle.get('response_model') or model_default,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                metadata=metadata,
            )

            response_json = execution.response_json if isinstance(execution.response_json, dict) else None
            if not response_json and execution.response_text:
                response_json = {'email': {'body': execution.response_text}}

            result: Dict[str, Any] = {
                'success': True,
                'response': execution.response_text,
                'response_json': response_json,
                'tokens_input': prompt_tokens,
                'tokens_output': completion_tokens,
                'tokens_total': total_tokens,
                'context_used': len(context_docs),
                'token_usage_breakdown': execution.token_breakdown,
                'response_model': prompt_bundle.get('response_model'),
            }

            self._apply_bundle_metadata(result, prompt_bundle)
            return result

        except Exception as exc:
            return {
                'success': False,
                'error': str(exc),
            }

    def run_search_only(
        self,
        project_id: int,
        prompt_bundle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return retrieval and rerank context without final response generation."""

        context_docs = prompt_bundle.get('context_docs') or []
        token_breakdown = prompt_bundle.get('token_usage_breakdown') or self._usage_snapshot()
        multi_query_usage = prompt_bundle.get('multi_query_usage') or self.retrieval_pipeline.last_multi_query_usage
        rerank_usage = prompt_bundle.get('rerank_usage') or self.retrieval_pipeline.last_rerank_usage

        metadata_payload = {
            'mode': 'search_only',
            'context_used': len(context_docs),
            'multi_query_used': prompt_bundle.get('multi_query_used'),
            'multi_query_variants': prompt_bundle.get('multi_query_variants'),
            'multi_query_usage': multi_query_usage,
            'multi_query_aggregate_limit': prompt_bundle.get('multi_query_aggregate_limit'),
            'rerank_usage': rerank_usage,
            'retrieval_provider': prompt_bundle.get('rerank_provider'),
            'response_model': prompt_bundle.get('response_model'),
            'multi_query_mode': prompt_bundle.get('multi_query_mode'),
            'multi_query_variant_count': prompt_bundle.get('multi_query_variant_count'),
            'multi_query_model': prompt_bundle.get('multi_query_model'),
            'retrieval_top_k': prompt_bundle.get('retrieval_top_k'),
            'retrieval_threshold': prompt_bundle.get('retrieval_threshold'),
            'prefetch_limit': prompt_bundle.get('prefetch_limit'),
            'colbert_candidates': prompt_bundle.get('colbert_candidates'),
            'rrf_k': prompt_bundle.get('rrf_k'),
            'rrf_weights': prompt_bundle.get('rrf_weights'),
            # legacy keys for compatibility with earlier telemetry consumers
            'hybrid_per_vector_limit': prompt_bundle.get('prefetch_limit'),
            'hybrid_rrf_k': prompt_bundle.get('rrf_k'),
            'context_limit': prompt_bundle.get('context_limit'),
            'token_breakdown': token_breakdown,
            'style': prompt_bundle.get('resolved_style'),
            'language': prompt_bundle.get('target_language_code'),
            'context_file_ids': prompt_bundle.get('context_file_ids') or [],
            'rerank_provider': prompt_bundle.get('rerank_provider'),
            'rerank_model': prompt_bundle.get('rerank_model'),
            'rerank_top_k': prompt_bundle.get('rerank_top_k'),
            'rerank_threshold': prompt_bundle.get('rerank_threshold'),
            'rerank_settings': prompt_bundle.get('rerank_settings'),
        }

        self._log_usage_entry(
            project_id=project_id,
            source='email_analysis',
            model=prompt_bundle.get('response_model') or current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini'),
            metadata=metadata_payload,
        )

        result: Dict[str, Any] = {
            'success': True,
            'mode': 'search_only',
            'context_used': len(context_docs),
            'context_docs': context_docs,
            'context_file_ids': prompt_bundle.get('context_file_ids') or [],
            'token_usage_breakdown': token_breakdown,
            'multi_query_used': prompt_bundle.get('multi_query_used'),
            'multi_query_variants': prompt_bundle.get('multi_query_variants'),
            'multi_query_usage': multi_query_usage,
            'rerank_usage': rerank_usage,
            'tokens_input': 0,
            'tokens_output': 0,
            'tokens_total': 0,
        }

        self._apply_bundle_metadata(result, prompt_bundle)
        return result

    def _log_usage_entry(
        self,
        *,
        project_id: Optional[int],
        source: str,
        model: Optional[str],
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not project_id or not source:
            return
        model_name = model or current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini')
        with suppress(Exception):
            entry = AIUsageLog(
                project_id=project_id,
                source=source,
                model=model_name,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                total_tokens=total_tokens or ((prompt_tokens or 0) + (completion_tokens or 0)),
                metadata_json=json.dumps(metadata, default=str) if metadata else None,
            )
            db.session.add(entry)
            db.session.commit()

    def _apply_bundle_metadata(self, target: Dict[str, Any], bundle: Dict[str, Any]) -> None:
        keys_to_copy = [
            'language_instruction',
            'target_language_code',
            'target_language_name',
            'resolved_style',
            'developer_prompt',
            'project_context_prompt',
            'knowledge_entries',
            'context_docs',
            'context_file_ids',
            'context_file_names',
            'example_texts',
            'detection_confidence',
            'multi_query_used',
            'multi_query_variants',
            'multi_query_variant_count',
            'multi_query_mode',
            'multi_query_model',
            'multi_query_usage',
            'multi_query_aggregate_limit',
            'retrieval_top_k',
            'retrieval_threshold',
            'prefetch_limit',
            'colbert_candidates',
            'rrf_k',
            'rrf_weights',
            'hybrid_per_vector_limit',
            'hybrid_rrf_k',
            'rerank_provider',
            'rerank_model',
            'rerank_top_k',
            'rerank_threshold',
            'rerank_settings',
            'rerank_usage',
            'rerank_query',
            'context_limit',
            'response_model',
            'token_usage_breakdown',
            'vector_debug_logs',
            'search_steps',
        ]
        for key in keys_to_copy:
            if key in bundle and key not in target:
                target[key] = bundle[key]

        if 'context_file_ids' in target and 'context_file_names' not in target:
            target['context_file_names'] = self._resolve_context_filenames(target.get('context_file_ids') or [])

        target.setdefault('token_usage_breakdown', self._usage_snapshot())

    def _determine_verbosity(self) -> str:
        allowed = {'low', 'medium', 'high'}
        value = (current_app.config.get('OPENAI_RESPONSES_VERBOSITY') or '').strip().lower()
        if value in allowed:
            return value
        return 'medium'

    def _normalize_usage_dict(self, usage: Any) -> Dict[str, int]:
        if isinstance(usage, dict):
            return {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}
        if usage is None:
            return {}
        if hasattr(usage, 'to_dict'):
            try:
                return usage.to_dict()
            except Exception:
                pass
        prompt_tokens = getattr(usage, 'prompt_tokens', getattr(usage, 'input_tokens', 0)) or 0
        completion_tokens = getattr(usage, 'completion_tokens', getattr(usage, 'output_tokens', 0)) or 0
        total_tokens = getattr(usage, 'total_tokens', prompt_tokens + completion_tokens) or 0
        return {
            'prompt_tokens': int(prompt_tokens),
            'completion_tokens': int(completion_tokens),
            'total_tokens': int(total_tokens),
        }

    def _write_ocr_debug_artifacts(self, debug_out: Path, response: Any, page_no: int, text_out: str) -> None:
        try:
            (debug_out / f'ocr_response_raw_page_{page_no:04d}.txt').write_text(str(response), encoding='utf-8')
        except Exception:
            pass
        parsed = getattr(response, 'output_parsed', None)
        if parsed:
            try:
                (debug_out / f'ocr_response_parsed_page_{page_no:04d}.json').write_text(json.dumps(parsed, default=str, indent=2), encoding='utf-8')
            except Exception:
                pass
        if text_out:
            try:
                (debug_out / f'ocr_response_text_page_{page_no:04d}.txt').write_text(text_out, encoding='utf-8')
            except Exception:
                pass

    def _extract_ocr_text(self, response: Any) -> str:
        text = self._extract_text_from_payload(getattr(response, 'output_parsed', None))
        if text:
            return text

        for chunk in self._iterate_response_contents(response):
            parsed = getattr(chunk, 'parsed', None)
            text = self._extract_text_from_payload(parsed)
            if text:
                return text
            if isinstance(chunk, dict):
                text = self._extract_text_from_payload(chunk.get('parsed'))
                if text:
                    return text
                text = self._coerce_text_value(chunk.get('text'))
                if text:
                    return text
            text = self._coerce_text_value(getattr(chunk, 'text', None))
            if text:
                return text

        text = self._coerce_text_value(getattr(response, 'output_text', None))
        if text:
            return text

        if isinstance(response, dict):
            return self._extract_text_from_payload(response.get('output'))
        return ''

    def _extract_text_from_payload(self, payload: Any) -> str:
        if isinstance(payload, dict):
            candidate = payload.get('text')
            if isinstance(candidate, dict):
                candidate = candidate.get('text')
            if isinstance(candidate, str):
                return candidate.strip()
        elif isinstance(payload, list):
            for item in payload:
                text = self._extract_text_from_payload(item)
                if text:
                    return text
        elif isinstance(payload, str):
            return payload.strip()
        return ''

    def _iterate_response_contents(self, response: Any):
        outputs = getattr(response, 'output', None)
        if isinstance(outputs, list):
            for item in outputs:
                content = getattr(item, 'content', None)
                if isinstance(content, list):
                    for chunk in content:
                        yield chunk
                elif isinstance(content, dict):
                    yield content
        if isinstance(response, dict):
            for item in response.get('output', []) or []:
                content = item.get('content') if isinstance(item, dict) else None
                if isinstance(content, list):
                    for chunk in content:
                        yield chunk
                elif isinstance(content, dict):
                    yield content

    def _coerce_text_value(self, value: Any) -> str:
        if not isinstance(value, str):
            return ''
        stripped = value.strip()
        if not stripped:
            return ''
        try:
            loaded = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            return stripped
        if isinstance(loaded, dict):
            text = loaded.get('text')
            return text.strip() if isinstance(text, str) else ''
        if isinstance(loaded, str):
            return loaded.strip()
        return stripped

    def generate_embeddings(
        self,
        text,
        project_id: Optional[int] = None,
        *,
        debug: bool = False,
        out_dir: str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[List[float]], TokenUsage]:
        """Generate embeddings for text using OpenAI provider and return vector plus usage."""
        try:
            if debug:
                debug_out = self._make_debug_out(out_dir)
                print(f"[AIService][DEBUG] Embedding debug out: {debug_out}")
            else:
                debug_out = None
            print(f"[AIService][Embedding] START len_chars={len(text)}")

            vector, usage = self.openai.generate_embeddings(
                text=text,
                model=current_app.config.get('EMBEDDING_MODEL'),
                dimensions=current_app.config.get('EMBEDDING_DIM'),
            )
            if usage is not None and usage.total_tokens <= 0:
                approx_tokens = self.count_tokens(text)
                if approx_tokens > 0:
                    usage.prompt_tokens = approx_tokens
                    usage.completion_tokens = 0
            self._track_usage('embedding', usage)

            print(
                "[AIService][Embedding] DONE",
                f"prompt_tokens={usage.prompt_tokens}",
                f"total_tokens={usage.total_tokens}",
            )
            if debug and debug_out is not None:
                try:
                    (debug_out / 'embedding_response.json').write_text(json.dumps(usage.to_dict(), indent=2), encoding='utf-8')
                except Exception:
                    pass

            # Deferred logging to caller; return tuple for downstream accounting
            return vector, usage

        except Exception as e:
            print(f"Error generating embeddings: {str(e)}")
            return None, TokenUsage()

    def chunk_text(self, text, chunk_size=2000, overlap=200):
        """Split text into chunks by tokens (approx) respecting max token size."""
        if not text:
            return []

        encoder = self.encoder
        tokens = encoder.encode(text)
        total = len(tokens)
        if total <= chunk_size:
            return [text.strip()]

        chunks = []
        start = 0
        while start < total:
            end = min(start + chunk_size, total)
            token_slice = tokens[start:end]
            chunk_text = encoder.decode(token_slice).strip()
            chunks.append(chunk_text)
            if end == total:
                break
            # Overlap
            start = end - overlap
            if start < 0:
                start = 0
        return chunks

    def count_tokens(self, text: str) -> int:
        try:
            return len(self.encoder.encode(text or ""))
        except Exception:
            return 0

    def ocr_image_to_text(self, png_bytes: bytes, page_no: int = 1, debug: bool = False, out_dir: str | None = None) -> tuple[str, dict]:
        """Perform OCR on a single page PNG bytes via OpenAI Responses API."""
        from base64 import b64encode

        usage_dict: dict = {}
        text_out = ''
        debug_out = self._make_debug_out(out_dir) if debug else None
        if debug and debug_out is not None:
            try:
                (debug_out / f'page_{page_no:04d}.png').write_bytes(png_bytes)
            except Exception:
                pass

        model_name = current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini')
        system_prompt = (
            "Jesteś precyzyjnym silnikiem OCR. Rozpoznaj tekst z obrazu strony dokumentu PDF."
            "Zwróć dokładny tekst który jest na stronie."
            "Jeżeli w dokumencie znajdują się tabele, zwróć tabelę w formacie mardown."
            "Jeżeli w dokumencie są grafiki, obrazy lub zdjęcia, zwróć dodatkowo szczegółowy opis tych obrazu."
            "Kluczowe - Zachowaj kolejność linii i strukturę dokumentu."
        )
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

        payload = {
            "model": model_name,
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [
                    {"type": "input_text", "text": f"OCR strony PDF nr {page_no}"},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{b64encode(png_bytes).decode('utf-8')}"},
                ]},
            ],
            "text": {
                "format": {"type": "json_schema", "name": "ocr_text", "strict": True, "schema": schema},
                "verbosity": self._determine_verbosity(),
            },
            "reasoning": {"effort": "high"},
            "tools": [],
            "store": True,
            "max_output_tokens": current_app.config.get('MAX_OUTPUT_TOKENS', '6000'),
        }

        try:
            response = self.client.responses.create(**payload)
        except Exception:
            return text_out, usage_dict

        usage_dict = self._normalize_usage_dict(getattr(response, 'usage', None))
        text_out = self._extract_ocr_text(response)

        if debug and debug_out is not None:
            self._write_ocr_debug_artifacts(debug_out, response, page_no, text_out)

        return text_out, usage_dict
