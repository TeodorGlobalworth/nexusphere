from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app import db
# DISABLED: Email language detection removed
# from app.utils.language import detect_language, lang_to_english_name

from .retrieval_pipeline import RetrievalArtifacts, RetrievalOverrides, RetrievalPipeline
from .usage_tracker import UsageTracker


@dataclass
class PromptBuildOptions:
    style_hint: str = 'standard'
    explicit_language_code: Optional[str] = None
    max_context_docs: int = 6
    max_chars_per_doc: int = 20000  # Increased to avoid truncation
    max_chars_per_example: int = 800
    vector_top_k: Optional[int] = None
    multi_query_mode: Optional[str] = None
    multi_query_model: Optional[str] = None
    multi_query_variants: Optional[int] = None
    multi_query_aggregate_top_k: Optional[int] = None
    rerank_provider: Optional[str] = None
    rerank_model: Optional[str] = None
    rerank_top_k: Optional[int] = None
    rerank_threshold: Optional[float] = None
    retrieval_threshold: Optional[float] = None
    response_model: Optional[str] = None
    prefetch_limit: Optional[int] = None
    colbert_candidates: Optional[int] = None
    rrf_k: Optional[int] = None
    rrf_weights: Optional[Dict[str, Any]] = None
    hybrid_per_vector_limit: Optional[int] = None
    hybrid_rrf_k: Optional[int] = None
    include_neighbor_chunks: bool = False


@dataclass
class PromptBundle:
    data: Dict[str, Any]


class PromptBuilder:
    """Produces the structured prompt bundle used by AIService."""

    def __init__(
        self,
        *,
        retrieval_pipeline: RetrievalPipeline,
        usage_tracker: UsageTracker,
    ) -> None:
        self.retrieval_pipeline = retrieval_pipeline
        self.usage_tracker = usage_tracker

    def build(
        self,
        *,
        project_id: int,
        email_content: str,
        options: PromptBuildOptions,
    ) -> PromptBundle:
        project = self._get_project(project_id)

        style_key = self._resolve_style(options.style_hint, project)
        developer_prompt = (current_app.config.get('EMAIL_DEVELOPER_PROMPT') or '').strip()
        project_context_prompt = self._resolve_project_context_prompt(project)

        target_language_code, target_language_name, detection_confidence = self._resolve_language(
            email_content,
            options.explicit_language_code,
        )

        retrieval_artifacts = self.retrieval_pipeline.collect(
            project_id=project_id,
            email_content=email_content,
            overrides=RetrievalOverrides(
                vector_top_k=options.vector_top_k,
                retrieval_threshold=options.retrieval_threshold,
                multi_query_mode=options.multi_query_mode,
                multi_query_model=options.multi_query_model,
                multi_query_variants=options.multi_query_variants,
                multi_query_aggregate_top_k=options.multi_query_aggregate_top_k,
                rerank_provider=options.rerank_provider,
                rerank_model=options.rerank_model,
                rerank_top_k=options.rerank_top_k,
                rerank_threshold=options.rerank_threshold,
                prefetch_limit=options.prefetch_limit,
                colbert_candidates=options.colbert_candidates,
                rrf_k=options.rrf_k,
                rrf_weights=options.rrf_weights,
                hybrid_per_vector_limit=options.hybrid_per_vector_limit,
                hybrid_rrf_k=options.hybrid_rrf_k,
            ),
            max_context_docs=options.max_context_docs,
        )

        context_docs = list(retrieval_artifacts.documents or [])
        neighbors_applied = False

        include_neighbors = bool(getattr(options, 'include_neighbor_chunks', False))
        if include_neighbors and not retrieval_artifacts.multi_query_used and len(context_docs) == 1:
            doc = context_docs[0]
            meta = (doc.get('metadata') or {}).copy()
            file_id = meta.get('file_id') or meta.get('document_id')
            chunk_id_raw = meta.get('chunk_id') or meta.get('chunk_index')
            try:
                chunk_idx = int(chunk_id_raw)
            except Exception:
                chunk_idx = None

            if file_id is not None and chunk_idx is not None:
                vector_service = self.retrieval_pipeline.vector_service
                neighbors: List[Tuple[int, Dict[str, Any]]] = []

                prev_chunk = vector_service.fetch_chunk_by_index(
                    project_id=int(project_id),
                    file_id=file_id,
                    chunk_index=chunk_idx - 1,
                ) if chunk_idx > 0 else None
                next_chunk = vector_service.fetch_chunk_by_index(
                    project_id=int(project_id),
                    file_id=file_id,
                    chunk_index=chunk_idx + 1,
                )

                if prev_chunk:
                    prev_copy = {**prev_chunk, 'metadata': {**(prev_chunk.get('metadata') or {}), 'neighbor_origin': 'previous'}}
                    neighbors.append((chunk_idx - 1, prev_copy))
                if next_chunk:
                    next_copy = {**next_chunk, 'metadata': {**(next_chunk.get('metadata') or {}), 'neighbor_origin': 'next'}}
                    neighbors.append((chunk_idx + 1, next_copy))

                if neighbors:
                    combined: Dict[int, Dict[str, Any]] = {chunk_idx: doc}
                    for idx, neighbor_doc in neighbors:
                        if neighbor_doc:
                            combined.setdefault(idx, neighbor_doc)
                    ordered_docs = [combined[i] for i in sorted(combined.keys())]
                    context_docs = ordered_docs
                    neighbors_applied = True

        knowledge_entries, context_docs = self._prepare_knowledge_entries(
            context_docs,
            max_context_docs=options.max_context_docs,
            max_chars_per_doc=options.max_chars_per_doc,
        )

        context_file_ids = retrieval_artifacts.context_file_ids
        example_texts = self._collect_examples(project, options.max_chars_per_example)
        system_prompt, user_prompt = self._compose_prompts(
            developer_prompt=developer_prompt,
            project_context_prompt=project_context_prompt,
            style_key=style_key,
            language_name=target_language_name,
            language_code=target_language_code,
            knowledge_entries=knowledge_entries,
            example_texts=example_texts,
            email_content=email_content,
        )

        response_model = (options.response_model or '').strip() or current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini')

        bundle = {
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'language_instruction': f"Respond in {target_language_name}.",
            'target_language_code': target_language_code,
            'target_language_name': target_language_name,
            'resolved_style': style_key,
            'developer_prompt': developer_prompt,
            'project_context_prompt': project_context_prompt,
            'knowledge_entries': knowledge_entries,
            'context_docs': context_docs,
            'context_file_ids': context_file_ids,
            'example_texts': example_texts,
            'detection_confidence': detection_confidence,
            'multi_query_used': retrieval_artifacts.multi_query_used,
            'multi_query_variants': retrieval_artifacts.multi_query_variants,
            'multi_query_variant_count': retrieval_artifacts.multi_query_variant_count,
            'multi_query_mode': retrieval_artifacts.multi_query_mode,
            'multi_query_model': retrieval_artifacts.multi_query_model,
            'multi_query_usage': retrieval_artifacts.multi_query_usage,
            'multi_query_aggregate_limit': retrieval_artifacts.multi_query_aggregate_limit,
            'retrieval_top_k': retrieval_artifacts.retrieval_limit,
            'retrieval_threshold': retrieval_artifacts.retrieval_threshold,
            'prefetch_limit': retrieval_artifacts.prefetch_limit,
            'colbert_candidates': retrieval_artifacts.colbert_candidates,
            'rrf_k': retrieval_artifacts.rrf_k,
            'rrf_weights': retrieval_artifacts.rrf_weights,
            # legacy keys maintained for compatibility with existing consumers
            'hybrid_per_vector_limit': retrieval_artifacts.prefetch_limit,
            'hybrid_rrf_k': retrieval_artifacts.rrf_k,
            'rerank_provider': retrieval_artifacts.rerank_settings.get('provider'),
            'rerank_model': retrieval_artifacts.rerank_settings.get('model'),
            'rerank_top_k': retrieval_artifacts.rerank_settings.get('top_k'),
            'rerank_threshold': retrieval_artifacts.rerank_settings.get('threshold'),
            'rerank_settings': retrieval_artifacts.rerank_settings,
            'rerank_usage': retrieval_artifacts.rerank_usage,
            'rerank_query': retrieval_artifacts.rerank_query,
            'token_usage_breakdown': self.usage_tracker.snapshot(),
            'context_limit': options.max_context_docs,
            'response_model': response_model,
            'vector_debug_logs': retrieval_artifacts.vector_debug_logs,
            'search_steps': retrieval_artifacts.search_steps,
            'include_neighbor_chunks': include_neighbors,
            'neighbor_chunks_applied': neighbors_applied,
        }

        return PromptBundle(bundle)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_project(self, project_id: int):
        from app.models.project import Project as _Project

        return db.session.get(_Project, project_id)

    def _resolve_style(self, style_hint: str, project) -> str:
        style_prompts = {
            'formal': "Jesteś profesjonalnym asystentem korporacyjnym. Odpowiadaj bardzo formalnie, używając profesjonalnego języka i pełnych form grzecznościowych.",
            'standard': "Jesteś pomocnym asystentem biznesowym. Odpowiadaj profesjonalnie, ale w przyjazny sposób.",
            'casual': "Jesteś pomocnym asystentem. Odpowiadaj w przyjazny i swobodny sposób, ale zachowaj profesjonalizm.",
        }
        resolved = style_hint or 'standard'
        if resolved == 'default' or resolved not in style_prompts:
            resolved = getattr(project, 'response_style', None) or 'standard'
        if resolved not in style_prompts:
            resolved = 'standard'
        return resolved

    def _resolve_project_context_prompt(self, project) -> str:
        # Context prompts removed
        return ''

    def _resolve_language(self, email_content: str, explicit: Optional[str]):
        """Simplified - always use default or explicit language (email language detection removed)."""
        default_lang = (current_app.config.get('DEFAULT_RESPONSE_LANGUAGE') or 'en')
        if explicit:
            return explicit, explicit, None
        return default_lang, default_lang, None

    def _resolve_language_name(self, language_code: str) -> str:
        """Simplified - just return the language code (language name resolution removed)."""
        return language_code

    def _prepare_knowledge_entries(
        self,
        context_docs: List[Dict[str, Any]],
        *,
        max_context_docs: int,
        max_chars_per_doc: int,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        def _trim_text(text: str, limit: int) -> str:
            text = (text or '').strip()
            if limit and len(text) > limit:
                return text[:limit].rstrip() + '…'
            return text

        knowledge_entries: List[Dict[str, Any]] = []
        limited_docs = context_docs[:max_context_docs]
        for idx, doc in enumerate(limited_docs, start=1):
            meta = doc.get('metadata') or {}
            title = meta.get('title') or meta.get('name') or meta.get('filename') or meta.get('file_name')
            knowledge_entries.append({
                'index': idx,
                'title': title,
                'score': doc.get('score'),
                'content': _trim_text(doc.get('content', ''), max_chars_per_doc),
            })
        return knowledge_entries, context_docs

    def _collect_examples(self, project, max_chars: int) -> List[str]:
        examples: List[str] = []
        # Sample emails feature removed - always return empty list
        return examples

    def _compose_prompts(
        self,
        *,
        developer_prompt: str,
        project_context_prompt: str,
        style_key: str,
        language_name: str,
        language_code: str,
        knowledge_entries: List[Dict[str, Any]],
        example_texts: List[str],
        email_content: str,
    ) -> Tuple[str, str]:
        style_prompts = {
            'formal': "Jesteś profesjonalnym asystentem korporacyjnym. Odpowiadaj bardzo formalnie, używając profesjonalnego języka i pełnych form grzecznościowych.",
            'standard': "Jesteś pomocnym asystentem biznesowym. Odpowiadaj profesjonalnie, ale w przyjazny sposób.",
            'casual': "Jesteś pomocnym asystentem. Odpowiadaj w przyjazny i swobodny sposób, ale zachowaj profesjonalizm.",
        }

        if knowledge_entries:
            knowledge_blocks = []
            for entry in knowledge_entries:
                lines = []
                if entry.get('title'):
                    lines.append(f"Źródło: {entry['title']}")
                lines.append(entry.get('content') or '')
                knowledge_blocks.append(
                    f"<ENTRY id=\"{entry['index']}\">\n" + "\n".join(lines) + "\n</ENTRY>"
                )
            knowledge_block = "<KNOWLEDGE_BASE>\n" + "\n\n".join(knowledge_blocks) + "\n</KNOWLEDGE_BASE>"
        else:
            knowledge_block = "<KNOWLEDGE_BASE empty=\"true\">\nBrak dopasowanych fragmentów w bazie wiedzy. W razie potrzeby poproś o dodatkowe informacje.\n</KNOWLEDGE_BASE>"

        examples_block = ''
        if example_texts:
            blocks = []
            for idx, sample in enumerate(example_texts, start=1):
                blocks.append(f"<EXAMPLE id=\"{idx}\">\n{sample}\n</EXAMPLE>")
            examples_block = "<STYLE_EXAMPLES>\n" + "\n\n".join(blocks) + "\n</STYLE_EXAMPLES>"

        sanitized_email_content = (email_content or '').strip()

        user_sections = []
        if examples_block:
            user_sections.append(examples_block)
        user_sections.append(knowledge_block)
        user_sections.append(f"<TARGET_LANGUAGE code=\"{language_code}\">{language_name}</TARGET_LANGUAGE>")
        user_sections.append(f"<RESPONSE_STYLE>{style_key}</RESPONSE_STYLE>")
        user_sections.append("<EMAIL_MESSAGE>\n" + sanitized_email_content + "\n</EMAIL_MESSAGE>")
        user_prompt = "\n\n".join(user_sections)

        system_sections = []
        if project_context_prompt:
            system_sections.append("<PROJECT_CONTEXT_INSTRUCTIONS>\n" + project_context_prompt + "\n</PROJECT_CONTEXT_INSTRUCTIONS>")
        if developer_prompt:
            system_sections.append("<GLOBAL_INSTRUCTIONS>\n" + developer_prompt + "\n</GLOBAL_INSTRUCTIONS>")
        system_sections.append("<STYLE_GUIDELINES>\n" + style_prompts[style_key] + "\n</STYLE_GUIDELINES>")
        system_sections.append("<OUTPUT_LANGUAGE_INSTRUCTION>\nRespond in " + language_name + ".\n</OUTPUT_LANGUAGE_INSTRUCTION>")
        system_sections.append("<RESPONSE_REQUIREMENTS>\n- Wspieraj odpowiedź faktami z sekcji KNOWLEDGE_BASE.\n- Jeśli brakuje danych, jasno wskaż ograniczenia i zaproponuj kolejne kroki.\n- Utrzymaj profesjonalną strukturę, nagłówek oraz podpis zgodny z projektem.\n</RESPONSE_REQUIREMENTS>")
        system_prompt = "\n\n".join([section for section in system_sections if section.strip()])

        return system_prompt, user_prompt
