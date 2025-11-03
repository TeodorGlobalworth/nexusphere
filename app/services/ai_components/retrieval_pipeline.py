from __future__ import annotations

import hashlib
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask import current_app

from app.services.ai_providers import OpenAIProvider
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService

from .usage_tracker import UsageTracker


@dataclass
class RetrievalOverrides:
    vector_top_k: Optional[int] = None
    retrieval_threshold: Optional[float] = None
    multi_query_mode: Optional[str] = None
    multi_query_model: Optional[str] = None
    multi_query_variants: Optional[int] = None
    multi_query_aggregate_top_k: Optional[int] = None
    rerank_provider: Optional[str] = None
    rerank_model: Optional[str] = None
    rerank_top_k: Optional[int] = None
    rerank_threshold: Optional[float] = None
    prefetch_limit: Optional[int] = None
    colbert_candidates: Optional[int] = None
    rrf_k: Optional[int] = None
    rrf_weights: Optional[Dict[str, Any]] = None
    # Legacy overrides preserved for backwards compatibility
    hybrid_per_vector_limit: Optional[int] = None
    hybrid_rrf_k: Optional[int] = None


@dataclass
class RetrievalArtifacts:
    documents: List[Dict[str, Any]]
    context_file_ids: List[int]
    multi_query_used: bool
    multi_query_variants: List[str]
    multi_query_variant_count: int
    multi_query_mode: str
    multi_query_model: Optional[str]
    multi_query_usage: Optional[Dict[str, Any]]
    multi_query_aggregate_limit: Optional[int]
    rerank_usage: Optional[Dict[str, Any]]
    rerank_settings: Dict[str, Any]
    rerank_query: Optional[str]
    retrieval_limit: int
    retrieval_threshold: Optional[float]
    prefetch_limit: int
    colbert_candidates: int
    rrf_k: int
    rrf_weights: Dict[str, float]
    vector_debug_logs: List[Dict[str, Any]]
    search_steps: List[Dict[str, Any]]


class RetrievalPipeline:
    """Handles vector retrieval, multi-query orchestration, and reranking."""

    def __init__(
        self,
        *,
        vector_service: VectorService,
        rerank_service: RerankService,
        openai_provider: OpenAIProvider,
        usage_tracker: UsageTracker,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.vector_service = vector_service
        self.rerank_service = rerank_service
        self.openai_provider = openai_provider
        self.usage_tracker = usage_tracker
        self.logger = logger or logging.getLogger(__name__)

        self._last_multi_query_usage: Optional[Dict[str, Any]] = None
        self._last_rerank_usage: Optional[Dict[str, Any]] = None
        self._last_rerank_settings: Dict[str, Any] = {}
        self._last_rerank_query: Optional[str] = None
        self._extra_debug_logs: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def reset_state(self) -> None:
        self._last_multi_query_usage = None
        self._last_rerank_usage = None
        self._last_rerank_settings = {}
        self._last_rerank_query = None
        self._extra_debug_logs.clear()
        with suppress(Exception):
            self.vector_service.clear_debug_logs()

    @property
    def last_multi_query_usage(self) -> Optional[Dict[str, Any]]:
        return self._last_multi_query_usage

    @property
    def last_rerank_usage(self) -> Optional[Dict[str, Any]]:
        return self._last_rerank_usage

    @property
    def last_rerank_settings(self) -> Dict[str, Any]:
        return dict(self._last_rerank_settings)

    # ------------------------------------------------------------------

    def collect(
        self,
        *,
        project_id: int,
        email_content: str,
        overrides: RetrievalOverrides,
        max_context_docs: int,
    ) -> RetrievalArtifacts:
        self.reset_state()

        force_multi_query = self._resolve_force_multi_query(overrides.multi_query_mode)
        base_vector_limit = self._resolve_vector_limit(max_context_docs, overrides.vector_top_k)
        retrieval_threshold = self._float_or_none(overrides.retrieval_threshold)
        prefetch_override = overrides.prefetch_limit
        if prefetch_override is None:
            prefetch_override = overrides.hybrid_per_vector_limit
        prefetch_limit = self.vector_service.resolve_prefetch_limit(
            base_vector_limit,
            prefetch_override,
        )
        colbert_candidates = self.vector_service.resolve_colbert_candidates(overrides.colbert_candidates)
        rrf_k = self.vector_service.resolve_rrf_k(
            overrides.rrf_k if overrides.rrf_k is not None else overrides.hybrid_rrf_k
        )
        rrf_weights = self.vector_service.resolve_rrf_weights(overrides.rrf_weights)

        use_multi_query = self._should_use_multi_query(project_id, force_multi_query)
        aggregate_limit = self._resolve_multi_query_aggregate_limit(
            overrides.multi_query_aggregate_top_k,
            base_vector_limit,
        )
        aggregate_limit_effective = aggregate_limit

        search_steps: List[Dict[str, Any]] = []
        rrf_snapshot: List[Dict[str, Any]] = []

        multi_query_variants: List[str] = []
        context_docs: List[Dict[str, Any]] = []
        multi_query_used = False
        final_source_hint = ''
        rerank_query = email_content

        if use_multi_query and email_content:
            multi_query_variants, primary_query = self._generate_multi_query_variants(
                email_content,
                target=overrides.multi_query_variants,
                model_name=overrides.multi_query_model,
            )
            queries_for_search = list(multi_query_variants) if multi_query_variants else []
            if primary_query:
                rerank_query = primary_query
            if not queries_for_search and primary_query:
                queries_for_search = [primary_query.strip()]
            if queries_for_search:
                (
                    context_docs,
                    aggregate_limit_effective,
                    variant_rows,
                    rrf_snapshot,
                ) = self._collect_multi_query_context(
                    project_id=project_id,
                    variants=queries_for_search,
                    max_context_docs=max_context_docs,
                    rerank_query=rerank_query,
                    per_variant_limit=overrides.vector_top_k,
                    score_threshold=retrieval_threshold,
                    aggregate_limit=aggregate_limit,
                    rerank_provider=overrides.rerank_provider,
                    rerank_model=overrides.rerank_model,
                    rerank_top_k=overrides.rerank_top_k,
                    rerank_threshold=overrides.rerank_threshold,
                    prefetch_limit=prefetch_limit,
                    colbert_candidates=colbert_candidates,
                    rrf_k=rrf_k,
                    rrf_weights=rrf_weights,
                )
                search_steps.extend(variant_rows)
                multi_query_used = bool(context_docs)
                if context_docs:
                    final_source_hint = 'multi-query results'

        if not context_docs:
            single_label = 'single-query fallback' if multi_query_variants else 'single-query primary'
            rerank_query = email_content
            context_docs, single_rows, rrf_snapshot = self._single_query_context(
                project_id=project_id,
                email_content=email_content,
                limit=base_vector_limit,
                score_threshold=retrieval_threshold,
                rerank_provider=overrides.rerank_provider,
                rerank_model=overrides.rerank_model,
                rerank_top_k=overrides.rerank_top_k,
                rerank_threshold=overrides.rerank_threshold,
                prefetch_limit=prefetch_limit,
                colbert_candidates=colbert_candidates,
                rrf_k=rrf_k,
                rrf_weights=rrf_weights,
                label=single_label,
            )
            final_source_hint = single_label
            search_steps.extend(single_rows)
        multi_query_mode_effective = self._resolve_multi_query_mode(force_multi_query, bool(context_docs), use_multi_query)

        context_file_ids = self._extract_file_ids(context_docs, max_context_docs)
        rerank_settings = self._ensure_rerank_settings(
            overrides=overrides,
            max_context_docs=max_context_docs,
        )

        if rrf_snapshot:
            search_steps.extend(self._build_rows_from_documents(rrf_snapshot, category='RRF', stage='rrf'))

        post_threshold = None
        if isinstance(rerank_settings, dict):
            post_threshold = rerank_settings.get('threshold')
        effective_source = final_source_hint or ('multi-query results' if multi_query_used else 'single-query path')
        self._record_aggregated_snapshot(
            documents=context_docs,
            limit=max_context_docs,
            stage='post-rerank',
            multi_query=multi_query_used,
            source_hint=effective_source,
            threshold=post_threshold,
            extra_parts=[f'docs: {len(context_docs)}', f'aggregate_limit: {aggregate_limit_effective}'],
            rerank_settings=rerank_settings,
        )

        if context_docs:
            search_steps.extend(self._build_rows_from_documents(context_docs, category='Reranking', stage='reranking'))

    # Remove limit: return all search_steps
    # search_steps = self._truncate_rows(search_steps, limit=120)

        debug_logs = self.vector_service.consume_debug_logs()
        if self._extra_debug_logs:
            debug_logs.extend(self._extra_debug_logs)
            self._extra_debug_logs.clear()

        return RetrievalArtifacts(
            documents=context_docs,
            context_file_ids=context_file_ids,
            multi_query_used=multi_query_used,
            multi_query_variants=multi_query_variants,
            multi_query_variant_count=self._resolve_variant_count(overrides.multi_query_variants),
            multi_query_mode=multi_query_mode_effective,
            multi_query_model=(overrides.multi_query_model or '').strip() or None,
            multi_query_usage=self._last_multi_query_usage,
            multi_query_aggregate_limit=aggregate_limit_effective,
            rerank_usage=self._last_rerank_usage,
            rerank_settings=rerank_settings,
            rerank_query=self._last_rerank_query,
            retrieval_limit=base_vector_limit,
            retrieval_threshold=retrieval_threshold,
            prefetch_limit=prefetch_limit,
            colbert_candidates=colbert_candidates,
            rrf_k=rrf_k,
            rrf_weights=rrf_weights,
            vector_debug_logs=debug_logs,
            search_steps=search_steps,
        )

    def _create_debug_entry(
        self,
        *,
        documents: List[Dict[str, Any]],
        max_rows: int,
        label_parts: Sequence[str],
        threshold: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        if not documents:
            return None

        table = self._format_combined_table(documents, limit=max_rows)
        if not table:
            return None

        files: List[str] = []
        seen_files = set()
        for doc in documents[:max_rows]:
            metadata = doc.get('metadata') or {}
            candidate = metadata.get('filename') or metadata.get('file_name') or metadata.get('name')
            if not candidate:
                candidate = metadata.get('file_id') or metadata.get('fileId') or metadata.get('document_id')
            if candidate:
                candidate_str = str(candidate)
                if candidate_str not in seen_files:
                    files.append(candidate_str)
                    seen_files.add(candidate_str)

        label = ' | '.join(part for part in label_parts if part)

        return {
            'label': label,
            'table': table,
            'files': files,
            'threshold': threshold,
        }

    def _record_debug_entry(self, entry: Optional[Dict[str, Any]], *, category: str) -> None:
        if not entry:
            return

        recorded = False
        try:
            recorded = self.vector_service.record_custom_debug_log(
                label=entry.get('label', ''),
                table=entry.get('table', ''),
                files=entry.get('files') or [],
                threshold=entry.get('threshold'),
                category=category,
            )
        except Exception:
            recorded = False

        if not recorded:
            self._extra_debug_logs.append(entry)

    def _record_aggregated_snapshot(
        self,
        *,
        documents: List[Dict[str, Any]],
        limit: int,
        stage: str,
        multi_query: bool,
        source_hint: Optional[str],
        threshold: Optional[float],
        extra_parts: Optional[Sequence[str]] = None,
        rerank_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        label_parts: List[str] = [f"Aggregated context ({stage})"]
        label_parts.append('multi-query' if multi_query else 'single-query')
        if source_hint:
            label_parts.append(source_hint)
        if extra_parts:
            label_parts.extend(extra_parts)
        if rerank_settings:
            provider = rerank_settings.get('provider')
            if provider:
                label_parts.append(f"provider: {provider}")
            model = rerank_settings.get('model')
            if model:
                label_parts.append(f"model: {model}")
        embedding_usage = None
        with suppress(Exception):
            embedding_usage = self.usage_tracker.get('embedding')
        if embedding_usage:
            total_tokens = getattr(embedding_usage, 'total_tokens', None)
            prompt_tokens = getattr(embedding_usage, 'prompt_tokens', None)
            if total_tokens is not None or prompt_tokens is not None:
                token_label = f"embedding_tokens: {total_tokens if total_tokens is not None else prompt_tokens}"
                if prompt_tokens is not None:
                    token_label += f" (prompt: {prompt_tokens})"
                label_parts.append(token_label)

        entry = self._create_debug_entry(
            documents=documents,
            max_rows=limit,
            label_parts=label_parts,
            threshold=threshold,
        )

        category = f"aggregated_{stage.replace('-', '_')}"
        self._record_debug_entry(entry, category=category)

    def _format_combined_table(self, documents: List[Dict[str, Any]], *, limit: int) -> str:
        headers = ['#', 'Vector score', 'Rerank score', 'Matched query', 'File', 'Chunk']
        rows: List[List[str]] = []
        for idx, doc in enumerate(documents[:limit], start=1):
            metadata = doc.get('metadata') or {}
            vector_score = metadata.get('vector_score')
            rerank_score = metadata.get('rerank_score') or doc.get('score')
            matched_query = metadata.get('matched_query') or metadata.get('matchedQuery') or ''
            file_name = metadata.get('filename') or metadata.get('file_name') or metadata.get('name')
            if not file_name:
                file_name = str(metadata.get('file_id') or metadata.get('fileId') or metadata.get('document_id') or '')
            chunk = metadata.get('chunk_id') or metadata.get('chunkId') or metadata.get('chunk_index') or ''

            def _fmt_score(value):
                if value is None:
                    return '—'
                try:
                    return f"{float(value):.5f}"
                except (TypeError, ValueError):
                    return str(value)

            rows.append([
                str(idx),
                _fmt_score(vector_score),
                _fmt_score(rerank_score),
                str(matched_query or '—'),
                str(file_name or '—'),
                str(chunk or ''),
            ])

        if not rows:
            return ''

        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        def _fmt(row: List[str]) -> str:
            return ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(row))

        sep = '-+-'.join('-' * w for w in widths)
        lines = [_fmt(headers), sep]
        lines.extend(_fmt(row) for row in rows)
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _multi_query_debug_enabled(self) -> bool:
        try:
            return bool(current_app.config.get('QDRANT_DEBUG'))
        except Exception:
            return False

    def _resolve_force_multi_query(self, mode: Optional[str]) -> Optional[bool]:
        if not mode:
            return None
        normalized = mode.strip().lower()
        if normalized in {'on', 'enable', 'enabled', 'true', 'yes', 'force_on'}:
            return True
        if normalized in {'off', 'disable', 'disabled', 'false', 'no', 'force_off'}:
            return False
        return None

    def _resolve_multi_query_mode(self, forced: Optional[bool], has_docs: bool, org_pref: bool) -> str:
        if forced is True:
            return 'forced_on'
        if forced is False:
            return 'forced_off'
        return 'org_on' if org_pref else 'org_off'

    def _resolve_vector_limit(self, max_context: int, override: Optional[int]) -> int:
        if override is not None and override > 0:
            return max(override, max_context)
        try:
            configured = int(current_app.config.get('VECTOR_TOP_K_DEFAULT', max_context) or max_context)
        except Exception:
            configured = max_context
        return max(configured, max_context)

    def _float_or_none(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _should_use_multi_query(self, project_id: int, forced: Optional[bool]) -> bool:
        if forced is not None:
            return forced
        try:
            return bool(current_app.config.get('MULTI_QUERY_ENABLED', True))
        except Exception:
            return False

    def _resolve_variant_count(self, target: Optional[int]) -> int:
        try:
            if target is None:
                return int(current_app.config.get('MULTI_QUERY_DEFAULT_VARIANTS', 4))
            return int(target)
        except Exception:
            return 4

    def _resolve_multi_query_aggregate_limit(self, override: Optional[int], default_context: int) -> int:
        candidate: Optional[int] = None
        if override is not None:
            try:
                candidate = int(override)
            except (TypeError, ValueError):
                candidate = None
        if candidate is None:
            try:
                candidate = int(current_app.config.get('MULTI_QUERY_AGGREGATE_LIMIT'))
            except Exception:
                candidate = None
        if candidate is None or candidate <= 0:
            candidate = default_context
        return max(1, candidate)

    def _generate_multi_query_variants(
        self,
        content: str,
        *,
        target: Optional[int],
        model_name: Optional[str],
    ) -> Tuple[List[str], Optional[str]]:
        content = (content or '').strip()
        if not content:
            return [], None
        target_count = max(1, min(target or 4, 12))
        preview_limit = int(current_app.config.get('MULTI_QUERY_EMAIL_PREVIEW', 4000))
        variants, primary_query, usage = self.openai_provider.create_multiquery_variants(
            content=content,
            target_count=target_count,
            preview_limit=preview_limit,
            model=model_name or current_app.config.get('MULTIQUERY_MODEL_DEFAULT') or current_app.config.get('OPENAI_RESPONSES_MODEL'),
        )
        usage_info = usage.to_dict()
        self._last_multi_query_usage = usage_info
        self.usage_tracker.track('multi_query', usage)

        unique: List[str] = []
        seen = set()
        for variant in variants:
            key = (variant or '').strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(variant.strip())
            if len(unique) >= target_count:
                break

        if self._multi_query_debug_enabled():
            msg = (
                "Multi-query variant generation\n"
                f"Variants ({len(unique)}): {unique}\n"
                f"Primary query: {primary_query or '<empty>'}\n"
                f"Usage: {usage_info or '<not available>'}"
            )
            self.logger.warning(msg, extra={'event': 'multi_query_variants_debug', 'variants': unique, 'usage': usage_info})

        return unique, (primary_query.strip() if isinstance(primary_query, str) else None)

    def _collect_multi_query_context(
        self,
        *,
        project_id: int,
        variants: List[str],
        max_context_docs: int,
        rerank_query: str,
        per_variant_limit: Optional[int],
        score_threshold: Optional[float],
        aggregate_limit: int,
        rerank_provider: Optional[str],
        rerank_model: Optional[str],
        rerank_top_k: Optional[int],
        rerank_threshold: Optional[float],
        prefetch_limit: int,
        colbert_candidates: int,
        rrf_k: int,
        rrf_weights: Dict[str, float],
    ) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]], List[Dict[str, Any]]]:
        if not variants:
            return [], max(1, aggregate_limit), [], []

        per_variant_limit = self._resolve_per_variant_limit(per_variant_limit, max_context_docs)
        applied_limit = max(1, int(aggregate_limit or max_context_docs))

        aggregated: Dict[str, Dict[str, Any]] = {}
        variant_rows: List[Dict[str, Any]] = []
        for idx, variant in enumerate(variants, start=1):
            debug_label = f"multi-query variant #{idx}: {variant}" if self._multi_query_debug_enabled() else None
            results = self.vector_service.search_similar(
                project_id,
                variant,
                limit=per_variant_limit,
                debug_label=debug_label,
                ai_service=self.openai_provider,
                score_threshold=score_threshold,
                usage_tracker=self.usage_tracker,
                prefetch_limit=prefetch_limit,
                colbert_candidates=colbert_candidates,
                rrf_k_override=rrf_k,
                rrf_weight_overrides=rrf_weights,
                collect_diagnostics=True,
            ) or []
            detail = self.vector_service.pop_last_search_detail()
            if detail:
                variant_rows.extend(
                    self._build_rows_from_diagnostic(
                        detail,
                        category_label=f'multiquery_{idx}',
                        stage='multiquery',
                        query_variant=variant,
                    )
                )
            for result in results:
                doc_key = result.get('id')
                if doc_key is None:
                    doc_key = (result.get('metadata') or {}).get('document_id')
                if doc_key is None:
                    doc_key = hashlib.sha1(f"{variant}|{(result.get('content') or '')[:160]}".encode('utf-8', 'ignore')).hexdigest()
                doc_key = str(doc_key)
                score = result.get('score')
                metadata = result.get('metadata')
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata = dict(metadata)
                metadata['matched_query'] = variant
                if score is not None:
                    metadata.setdefault('vector_score', score)
                candidate = {
                    **result,
                    'score': score,
                    'metadata': metadata,
                }
                existing = aggregated.get(doc_key)
                if existing is None or (score is not None and (existing.get('score') or 0) < (score or 0)):
                    aggregated[doc_key] = candidate

        documents = list(aggregated.values())
        documents.sort(key=lambda item: (item.get('score') or 0), reverse=True)
        documents = documents[:applied_limit]

        self._record_aggregated_snapshot(
            documents=documents,
            limit=applied_limit,
            stage='pre-rerank',
            multi_query=True,
            source_hint='multi-query aggregate',
            threshold=score_threshold,
            extra_parts=[f'variants: {len(variants)}', f'aggregate_limit: {applied_limit}'],
        )

        rerank_base = (rerank_query or '').strip() or variants[0]
        reranked = self._rerank_documents(
            query_text=rerank_base,
            documents=documents,
            max_context_docs=max_context_docs,
            rerank_provider=rerank_provider,
            rerank_model=rerank_model,
            rerank_top_k=rerank_top_k,
            rerank_threshold=rerank_threshold,
        )
        return reranked, applied_limit, variant_rows, documents

    def _resolve_per_variant_limit(self, per_variant_limit: Optional[int], max_context_docs: int) -> int:
        if per_variant_limit is not None and per_variant_limit > 0:
            return per_variant_limit
        try:
            base_vector_top_k = int(current_app.config.get('VECTOR_TOP_K_DEFAULT', max_context_docs))
        except Exception:
            base_vector_top_k = max_context_docs
        per_variant_cfg = current_app.config.get('MULTI_QUERY_PER_VARIANT_LIMIT')
        try:
            per_variant_limit = int(per_variant_cfg) if per_variant_cfg else max(base_vector_top_k, max_context_docs)
        except Exception:
            per_variant_limit = max(base_vector_top_k, max_context_docs)
        return max(1, per_variant_limit)

    def _single_query_context(
        self,
        *,
        project_id: int,
        email_content: str,
        limit: int,
        score_threshold: Optional[float],
        rerank_provider: Optional[str],
        rerank_model: Optional[str],
        rerank_top_k: Optional[int],
        rerank_threshold: Optional[float],
        label: Optional[str],
        prefetch_limit: int,
        colbert_candidates: int,
        rrf_k: int,
        rrf_weights: Dict[str, float],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        context_docs = self.vector_service.search_similar(
            project_id,
            email_content,
            limit=limit,
            debug_label=label,
            ai_service=self.openai_provider,
            score_threshold=score_threshold,
            usage_tracker=self.usage_tracker,
            prefetch_limit=prefetch_limit,
            colbert_candidates=colbert_candidates,
            rrf_k_override=rrf_k,
            rrf_weight_overrides=rrf_weights,
            collect_diagnostics=True,
        ) or []
        detail = self.vector_service.pop_last_search_detail()
        variant_rows: List[Dict[str, Any]] = []
        if detail:
            variant_rows.extend(
                self._build_rows_from_diagnostic(
                    detail,
                    category_label='multiquery_1',
                    stage='multiquery',
                    query_variant=label or 'primary_query',
                )
            )
        for doc in context_docs:
            metadata = doc.get('metadata')
            if not isinstance(metadata, dict):
                metadata = {}
            metadata = dict(metadata)
            if doc.get('score') is not None:
                metadata.setdefault('vector_score', doc.get('score'))
            doc['metadata'] = metadata

        self._record_aggregated_snapshot(
            documents=context_docs,
            limit=limit,
            stage='pre-rerank',
            multi_query=False,
            source_hint=(label or 'single-query').strip() or 'single-query',
            threshold=score_threshold,
        )

        reranked = self._rerank_documents(
            query_text=email_content,
            documents=context_docs,
            max_context_docs=limit,
            rerank_provider=rerank_provider,
            rerank_model=rerank_model,
            rerank_top_k=rerank_top_k,
            rerank_threshold=rerank_threshold,
        )
        return reranked, variant_rows, context_docs

    def _rerank_documents(
        self,
        *,
        query_text: str,
        documents: List[Dict[str, Any]],
        max_context_docs: int,
        rerank_provider: Optional[str],
        rerank_model: Optional[str],
        rerank_top_k: Optional[int],
        rerank_threshold: Optional[float],
    ) -> List[Dict[str, Any]]:
        if not documents:
            self._last_rerank_settings = {
                'provider': rerank_provider or current_app.config.get('RERANK_PROVIDER', 'none'),
                'model': rerank_model or current_app.config.get('NOVITA_RERANK_MODEL') or current_app.config.get('ZEROENTROPY_MODEL'),
                'top_k': rerank_top_k or max_context_docs,
                'threshold': rerank_threshold if rerank_threshold is not None else current_app.config.get('RERANK_THRESHOLD_DEFAULT'),
            }
            self._last_rerank_query = query_text
            return []

        provider_name = (rerank_provider or current_app.config.get('RERANK_PROVIDER', 'none') or 'none').strip()
        try:
            configured_top = int(current_app.config.get('RERANK_TOP_K_DEFAULT', max_context_docs))
        except Exception:
            configured_top = max_context_docs
        top_k = rerank_top_k if rerank_top_k is not None else configured_top
        if top_k is None:
            top_k = max_context_docs
        top_k = max(1, min(max_context_docs, top_k))

        if rerank_threshold is None:
            rerank_threshold = current_app.config.get('RERANK_THRESHOLD_DEFAULT')

        reranked, usage = self.rerank_service.rerank_documents(
            query=query_text,
            documents=documents,
            provider=provider_name,
            top_k=top_k,
            threshold=rerank_threshold,
            model=rerank_model,
        )
        self.usage_tracker.track('rerank', usage)
        self._last_rerank_usage = usage.to_dict()
        self._last_rerank_settings = {
            'provider': provider_name,
            'model': rerank_model,
            'top_k': top_k,
            'threshold': rerank_threshold,
        }
        self._last_rerank_query = query_text

        if reranked:
            return reranked

        threshold_val = self._float_or_none(rerank_threshold)
        if threshold_val is not None:
            documents = [doc for doc in documents if (doc.get('score') or 0) >= threshold_val]
        documents.sort(key=lambda item: (item.get('score') or 0), reverse=True)
        return documents[:top_k]

    def _extract_file_ids(self, documents: Sequence[Dict[str, Any]], limit: int) -> List[int]:
        file_ids: List[int] = []
        for doc in documents[:limit]:
            metadata = doc.get('metadata') or {}
            fid = metadata.get('file_id') or metadata.get('fileId') or metadata.get('fileID')
            if fid and fid not in file_ids:
                file_ids.append(fid)
        return file_ids

    def _sanitize_scalar(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _normalize_metadata(self, metadata: Any) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for key, raw_value in metadata.items():
            if isinstance(raw_value, dict):
                normalized[str(key)] = self._normalize_metadata(raw_value)
            elif isinstance(raw_value, (list, tuple)):
                normalized[str(key)] = [
                    self._normalize_metadata(item) if isinstance(item, dict) else self._sanitize_scalar(item)
                    for item in list(raw_value)[:50]
                ]
            else:
                normalized[str(key)] = self._sanitize_scalar(raw_value)
        return normalized

    def _extract_file_name_from_metadata(self, metadata: Dict[str, Any]) -> Optional[str]:
        for key in ('filename', 'file_name', 'name', 'title'):
            value = metadata.get(key)
            if value:
                return str(value)
        return None

    def _extract_chunk_from_metadata(self, metadata: Dict[str, Any]) -> Optional[str]:
        for key in ('chunk_id', 'chunk_index', 'chunkId'):
            value = metadata.get(key)
            if value is not None:
                return str(value)
        return None

    def _resolve_search_source_from_metadata(self, metadata: Dict[str, Any]) -> str:
        scores = metadata.get('hybrid_scores')
        if isinstance(scores, dict):
            best_channel = None
            best_score = None
            for key, raw_score in scores.items():
                channel = 'sparse' if key == 'lexical' else str(key)
                score = self._float_or_none(raw_score)
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_score = score
                    best_channel = channel
            if best_channel:
                return best_channel
        return 'dense'

    def _make_step_row(
        self,
        *,
        category: str,
        stage: str,
        search_source: str,
        score: Any,
        metadata: Dict[str, Any],
        content: Optional[str],
        rank: int,
        query_variant: Optional[str],
        debug_label: Optional[str],
    ) -> Dict[str, Any]:
        file_name = self._extract_file_name_from_metadata(metadata)
        chunk_id = self._extract_chunk_from_metadata(metadata)
        matched_query = metadata.get('matched_query') or metadata.get('matchedQuery')
        numeric_score = self._float_or_none(score)
        # New: first 20 chars of the query variant for UI table
        variant_text = (matched_query or query_variant or '')
        variant_preview = variant_text[:20]
        return {
            'category': category,
            'stage': stage,
            'search_source': search_source,
            'score': numeric_score,
            'score_raw': score,
            'rank': rank,
            'file_name': file_name,
            'file_id': metadata.get('file_id') or metadata.get('fileId') or metadata.get('document_id'),
            'chunk_id': chunk_id,
            'content': content or '',
            'metadata': metadata,
            'query_variant': query_variant,
            'matched_query': matched_query,
            'debug_label': debug_label,
            'variant_preview': variant_preview,
        }

    def _build_rows_from_diagnostic(
        self,
        diagnostic: Dict[str, Any],
        *,
        category_label: str,
        stage: str,
        query_variant: Optional[str],
    ) -> List[Dict[str, Any]]:
        if not isinstance(diagnostic, dict):
            return []
        rows: List[Dict[str, Any]] = []
        channels = diagnostic.get('channels')
        debug_label = diagnostic.get('label') if isinstance(diagnostic.get('label'), str) else None
        if isinstance(channels, dict):
            for channel, entries in channels.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    metadata = self._normalize_metadata(entry.get('metadata'))
                    score = entry.get('score')
                    content = entry.get('content') if isinstance(entry.get('content'), str) else ''
                    rank = int(entry.get('rank') or len(rows) + 1)
                    rows.append(
                        self._make_step_row(
                            category=category_label,
                            stage=stage,
                            search_source=str(channel),
                            score=score,
                            metadata=metadata,
                            content=content,
                            rank=rank,
                            query_variant=query_variant,
                            debug_label=debug_label,
                        )
                    )
        return rows

    def _build_rows_from_documents(
        self,
        documents: Sequence[Dict[str, Any]],
        *,
        category: str,
        stage: str,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not documents:
            return rows
        for idx, doc in enumerate(documents, start=1):
            if not isinstance(doc, dict):
                continue
            metadata = self._normalize_metadata(doc.get('metadata'))
            search_source = self._resolve_search_source_from_metadata(metadata)
            if stage == 'reranking':
                score = metadata.get('rerank_score') or metadata.get('rerankScore') or doc.get('score')
            else:
                score = doc.get('score')
            content = doc.get('content') if isinstance(doc.get('content'), str) else ''
            rows.append(
                self._make_step_row(
                    category=category,
                    stage=stage,
                    search_source=search_source,
                    score=score,
                    metadata=metadata,
                    content=content,
                    rank=idx,
                    query_variant=metadata.get('matched_query') or metadata.get('matchedQuery'),
                    debug_label=None,
                )
            )
        return rows

    def _truncate_rows(self, rows: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
        if limit <= 0 or len(rows) <= limit:
            return rows
        return rows[:limit]

    def _ensure_rerank_settings(self, *, overrides: RetrievalOverrides, max_context_docs: int) -> Dict[str, Any]:
        settings = dict(self._last_rerank_settings or {})
        provider = (overrides.rerank_provider or '').strip()
        if not provider:
            provider = current_app.config.get('RERANK_PROVIDER', 'none') or 'none'
        settings.setdefault('provider', provider)
        if overrides.rerank_model:
            settings['model'] = overrides.rerank_model
        settings.setdefault('top_k', overrides.rerank_top_k or max_context_docs)
        if overrides.rerank_threshold is not None:
            settings['threshold'] = overrides.rerank_threshold
        else:
            settings.setdefault('threshold', current_app.config.get('RERANK_THRESHOLD_DEFAULT'))
        return settings
