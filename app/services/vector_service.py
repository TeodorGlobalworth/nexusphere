from __future__ import annotations

import logging
import os
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from flask import current_app

from qdrant_client import QdrantClient, models

from app.services.ai_providers.types import TokenUsage
from app.services.bge_client import BGEClient, BGEClientError, BGESparseVector

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from app.services.ai_components.usage_tracker import UsageTracker


class VectorService:
    """High level helper around Qdrant hybrid search."""

    def __init__(self) -> None:
        self._client: Optional[QdrantClient] = None
        self._bge_client: Optional[BGEClient] = None
        self._debug_logs: List[Dict[str, Any]] = []
        self._search_diagnostics: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _logger(self) -> logging.Logger:
        try:
            return current_app.logger
        except Exception:  # pragma: no cover - fallback when outside app context
            return logging.getLogger(__name__)

    def _debug_enabled(self) -> bool:
        try:
            return bool(current_app.config.get('QDRANT_DEBUG'))
        except Exception:
            return False

    def _bge(self) -> BGEClient:
        if self._bge_client is None:
            self._bge_client = BGEClient()
        return self._bge_client

    def _client_kwargs(self) -> Dict[str, Any]:
        config = getattr(current_app, 'config', {})
        host = config.get('QDRANT_HOST') or os.environ.get('QDRANT_HOST') or 'localhost'
        port = config.get('QDRANT_PORT') or os.environ.get('QDRANT_PORT') or 6333
        api_key = config.get('QDRANT_API_KEY') or os.environ.get('QDRANT_API_KEY')
        allow_insecure = bool(config.get('QDRANT_ALLOW_INSECURE_FALLBACK', False))
        prefer_grpc = bool(config.get('QDRANT_PREFER_GRPC', False))
        timeout = config.get('QDRANT_TIMEOUT') or 30.0

        kwargs: Dict[str, Any] = {
            'host': str(host),
            'port': int(port),
            'prefer_grpc': prefer_grpc,
            'timeout': float(timeout),
            'https': not allow_insecure,
        }
        if api_key:
            kwargs['api_key'] = str(api_key)
        return kwargs

    def get_client(self) -> QdrantClient:
        if self._client is None:
            try:
                self._client = QdrantClient(**self._client_kwargs())
            except Exception as exc:  # pragma: no cover - network/SDK boundary
                self._logger().error(
                    "Failed to create Qdrant client",
                    extra={'event': 'qdrant_client_init_failed', 'error': str(exc)}
                )
                raise
        return self._client

    def _collection_name(self, project_id: int | str) -> str:
        try:
            project_int = int(project_id)
        except Exception:
            project_int = abs(hash(str(project_id))) % 1_000_000
        return f"project_{project_int}"

    def _expected_collection_layout(self) -> Tuple[Dict[str, models.VectorParams], Dict[str, models.SparseVectorParams]]:
        try:
            dense_dim = int(current_app.config.get('EMBEDDING_DIM', 1024))
        except Exception:
            dense_dim = 1024
        try:
            colbert_dim = int(current_app.config.get('COLBERT_DIM', 1024))
        except Exception:
            colbert_dim = 1024

        vectors = {
            'dense': models.VectorParams(size=dense_dim, distance=models.Distance.COSINE),
            'colbert': models.VectorParams(size=colbert_dim, distance=models.Distance.COSINE),
        }
        sparse_vectors = {
            'lexical': models.SparseVectorParams(),
        }
        return vectors, sparse_vectors

    def _collection_matches_layout(
        self,
        existing: Any,
        expected_vectors: Dict[str, models.VectorParams],
        expected_sparse: Dict[str, models.SparseVectorParams],
    ) -> bool:
        def _vector_size(params: Any) -> Optional[int]:
            return getattr(params, 'size', None) or getattr(params, 'dim', None)

        vectors_actual = None
        with suppress(Exception):
            vectors_actual = getattr(existing, 'vectors', None)
        if vectors_actual is None:
            with suppress(Exception):
                vectors_actual = getattr(existing, 'config', None)
                if vectors_actual is not None:
                    vectors_actual = getattr(vectors_actual, 'vectors', None)

        if isinstance(vectors_actual, dict):
            for name, expected in expected_vectors.items():
                actual = vectors_actual.get(name)
                if actual is None:
                    return False
                if _vector_size(actual) != _vector_size(expected):
                    return False
        elif isinstance(vectors_actual, models.VectorParams):
            expected_names = list(expected_vectors.keys())
            if len(expected_names) != 1:
                return False
            expected = expected_vectors[expected_names[0]]
            if _vector_size(vectors_actual) != _vector_size(expected):
                return False

        sparse_actual = None
        with suppress(Exception):
            sparse_actual = getattr(existing, 'sparse_vectors', None)
        if sparse_actual is None:
            with suppress(Exception):
                sparse_actual = getattr(existing, 'config', None)
                if sparse_actual is not None:
                    sparse_actual = getattr(sparse_actual, 'sparse_vectors', None)

        if isinstance(sparse_actual, dict):
            for name in expected_sparse.keys():
                if name not in sparse_actual:
                    return False

        return True

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------
    def clear_debug_logs(self) -> None:
        self._debug_logs.clear()

    def consume_debug_logs(self) -> List[Dict[str, Any]]:
        logs = list(self._debug_logs)
        self._debug_logs.clear()
        return logs

    def record_custom_debug_log(
        self,
        *,
        label: str,
        table: str,
        files: List[str],
        threshold: Optional[float],
        category: Optional[str] = None,
    ) -> bool:
        if not self._debug_enabled():
            return False
        self._debug_logs.append(
            {
                'label': label,
                'table': table,
                'files': files,
                'threshold': threshold,
                'category': category or 'custom',
            }
        )
        return True

    def pop_last_search_detail(self) -> Optional[Dict[str, Any]]:
        if not self._search_diagnostics:
            return None
        return self._search_diagnostics.pop()

    def _store_search_diagnostic(self, diagnostic: Dict[str, Any]) -> None:
        if not isinstance(diagnostic, dict):
            return
        self._search_diagnostics.append(diagnostic)

    def _safe_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _sanitize_value(self, value: Any, *, max_list: int = 50) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k): self._sanitize_value(v, max_list=max_list) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_value(v, max_list=max_list) for v in list(value)[:max_list]]
        return str(value)

    def _sanitize_metadata(self, metadata: Any) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        return {str(k): self._sanitize_value(v) for k, v in metadata.items()}

    def _extract_file_name(self, metadata: Dict[str, Any]) -> Optional[str]:
        for key in ('filename', 'file_name', 'name', 'title'):
            value = metadata.get(key)
            if value:
                return str(value)
        return None

    def _extract_chunk_id(self, metadata: Dict[str, Any]) -> Optional[str]:
        for key in ('chunk_id', 'chunk_index', 'chunkId'):
            value = metadata.get(key)
            if value is not None:
                return str(value)
        return None

    def _build_entry(
        self,
        *,
        identifier: Any,
        score: Any,
        rank: int,
        search_source: str,
        metadata: Dict[str, Any],
        content: Any,
        matched_query: Any | None = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        entry = {
            'id': '' if identifier is None else str(identifier),
            'score': self._safe_float(score),
            'rank': int(rank),
            'search_source': str(search_source) if search_source is not None else 'dense',
            'file_id': metadata.get('file_id') or metadata.get('fileId') or metadata.get('document_id'),
            'file_name': self._extract_file_name(metadata),
            'chunk_id': self._extract_chunk_id(metadata),
            'content': content if isinstance(content, str) else '',
            'metadata': metadata,
            'matched_query': matched_query,
        }
        if extra:
            entry.update(extra)
        return entry

    def _serialize_point(self, point: Any, *, channel: str, rank: int) -> Dict[str, Any]:
        payload = getattr(point, 'payload', {}) or {}
        metadata_raw = payload if isinstance(payload, dict) else {}
        metadata = self._sanitize_metadata(metadata_raw)
        return self._build_entry(
            identifier=getattr(point, 'id', ''),
            score=getattr(point, 'score', None),
            rank=rank,
            search_source=channel,
            metadata=metadata,
            content=payload.get('text'),
            matched_query=metadata.get('matched_query') or metadata.get('matchedQuery'),
        )

    def _serialize_document_snapshot(self, doc: Dict[str, Any], *, rank: int) -> Dict[str, Any]:
        metadata_raw = doc.get('metadata') if isinstance(doc, dict) else {}
        metadata = self._sanitize_metadata(metadata_raw)
        hybrid_scores = metadata.get('hybrid_scores') if isinstance(metadata.get('hybrid_scores'), dict) else {}
        source = self._dominant_channel(hybrid_scores)
        rerank_score = self._safe_float(metadata.get('rerank_score') or metadata.get('rerankScore'))
        return self._build_entry(
            identifier=doc.get('id'),
            score=doc.get('score'),
            rank=rank,
            search_source=source,
            metadata=metadata,
            content=doc.get('content'),
            matched_query=metadata.get('matched_query') or metadata.get('matchedQuery'),
            extra={'rerank_score': rerank_score},
        )

    def _dominant_channel(self, scores: Dict[str, Any]) -> str:
        if not isinstance(scores, dict) or not scores:
            return 'dense'
        mapped: Dict[str, float] = {}
        for key, value in scores.items():
            channel = 'sparse' if key == 'lexical' else str(key)
            score = self._safe_float(value)
            if score is None:
                continue
            mapped[channel] = score
        if not mapped:
            return 'dense'
        return max(mapped.items(), key=lambda item: item[1])[0]

    def _build_channel_snapshots(
        self,
        hits_by_channel: Dict[str, List[Any]],
        *,
        max_entries: int = 60,
    ) -> Dict[str, List[Dict[str, Any]]]:
        snapshots: Dict[str, List[Dict[str, Any]]] = {}
        for channel, hits in hits_by_channel.items():
            if not hits:
                continue
            mapped_channel = 'sparse' if channel == 'lexical' else channel
            entries: List[Dict[str, Any]] = []
            for idx, point in enumerate(hits[:max_entries], start=1):
                entries.append(self._serialize_point(point, channel=mapped_channel, rank=idx))
            snapshots[mapped_channel] = entries
        return snapshots

    def _build_search_diagnostic(
        self,
        *,
        query: str,
        debug_label: Optional[str],
        limit: int,
        score_threshold: Optional[float],
        prefetch_limit: int,
        colbert_limit: int,
        hits_by_channel: Dict[str, List[Any]],
        documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            'query': query,
            'label': debug_label or '',
            'limit': limit,
            'score_threshold': self._safe_float(score_threshold),
            'prefetch_limit': prefetch_limit,
            'colbert_limit': colbert_limit,
            'channels': self._build_channel_snapshots(hits_by_channel),
            'rrf': [self._serialize_document_snapshot(doc, rank=idx) for idx, doc in enumerate(documents, start=1)],
        }

    def _log_debug_results(self, hits: List[Any], threshold: Optional[float], label: str) -> None:
        if not self._debug_enabled() or not hits:
            return

        headers = ['#', 'Score', 'Document', 'Chunk']
        rows: List[List[str]] = []
        files: List[str] = []
        seen_files: set[str] = set()

        for idx, point in enumerate(hits[:200], start=1):
            payload = getattr(point, 'payload', {}) or {}
            metadata = payload if isinstance(payload, dict) else {}
            text_preview = str(payload.get('text') or '')[:100].replace('\n', ' ').strip()
            score = getattr(point, 'score', None)
            file_id = metadata.get('file_id') or metadata.get('document_id')
            chunk_id = metadata.get('chunk_id') or metadata.get('chunk_index')
            if file_id is not None:
                file_str = str(file_id)
                if file_str not in seen_files:
                    files.append(file_str)
                    seen_files.add(file_str)
            rows.append(
                [
                    str(idx),
                    f"{float(score):.5f}" if isinstance(score, (int, float)) else str(score or '—'),
                    text_preview or '—',
                    str(chunk_id or '—'),
                ]
            )

        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        def _fmt(row: List[str]) -> str:
            return ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(row))

        divider = '-+-'.join('-' * w for w in widths)
        table_lines = [_fmt(headers), divider]
        table_lines.extend(_fmt(row) for row in rows)
        table = '\n'.join(table_lines)

        self._debug_logs.append(
            {
                'label': label,
                'table': table,
                'files': files,
                'threshold': threshold,
                'category': 'search-results',
            }
        )

    # ------------------------------------------------------------------
    # Resolver helpers
    # ------------------------------------------------------------------
    def resolve_prefetch_limit(self, requested_limit: int, override: Optional[int] = None) -> int:
        base = max(1, int(requested_limit or 1))
        if override is not None:
            with suppress(Exception):
                candidate = int(override)
                if candidate > 0:
                    return max(base, candidate)
        try:
            configured = int(current_app.config.get('PREFETCH_LIMIT', base))
        except Exception:
            configured = base
        return max(base, configured)

    def resolve_colbert_candidates(self, override: Optional[int] = None) -> int:
        if override is not None:
            with suppress(Exception):
                value = int(override)
                if value > 0:
                    return value
        try:
            configured = int(current_app.config.get('COLBERT_CANDIDATES', 15))
        except Exception:
            configured = 15
        return max(1, configured)

    def resolve_rrf_k(self, override: Optional[int] = None) -> int:
        if override is not None:
            with suppress(Exception):
                value = int(override)
                if value > 0:
                    return value
        try:
            configured = int(current_app.config.get('HYBRID_RRF_K', 60))
        except Exception:
            configured = 60
        return max(1, configured)

    def resolve_rrf_weights(self, override: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        def _cfg(name: str, default: float) -> float:
            try:
                return float(current_app.config.get(name, default) or default)
            except Exception:
                return default

        weights = {
            'dense': _cfg('RRF_DENSE_WEIGHT', 0.6),
            'lexical': _cfg('RRF_SPARSE_WEIGHT', 0.3),
            'colbert': _cfg('RRF_COLBERT_WEIGHT', 0.1),
        }

        if not override:
            return weights

        if isinstance(override, dict):
            mapping = {
                'dense': ['dense', 'dense_weight'],
                'lexical': ['lexical', 'sparse', 'sparse_weight'],
                'colbert': ['colbert', 'colbert_weight'],
            }
            for channel, keys in mapping.items():
                for key in keys:
                    if key not in override:
                        continue
                    with suppress(Exception):
                        weights[channel] = float(override[key])
                        break
        return weights

    def resolve_score_threshold(self, override: Optional[float]) -> float:
        if override is not None:
            with suppress(Exception):
                return float(override)
        cfg_value = current_app.config.get('QDRANT_SCORE_THRESHOLD')
        with suppress(Exception):
            if cfg_value is not None:
                return float(cfg_value)
        return 0.0

    def resolve_hybrid_per_vector_limit(self, requested_limit: int, override: Optional[int] = None) -> int:
        return self.resolve_prefetch_limit(requested_limit, override)

    def resolve_hybrid_rrf_k(self, override: Optional[int] = None) -> int:
        return self.resolve_rrf_k(override)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------
    def create_collection(self, project_id: int | str) -> bool:
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            vectors_expected, sparse_expected = self._expected_collection_layout()

            existing = None
            with suppress(Exception):
                existing = client.get_collection(collection_name)

            if existing and self._collection_matches_layout(existing, vectors_expected, sparse_expected):
                return True

            try:
                if existing is None:
                    client.create_collection(
                        collection_name=collection_name,
                        vectors_config=vectors_expected,
                        sparse_vectors_config=sparse_expected,
                    )
                else:
                    self._logger().warning(
                        "Recreating Qdrant collection to match expected layout",
                        extra={'event': 'qdrant_recreate_collection', 'collection': collection_name}
                    )
                    client.recreate_collection(
                        collection_name=collection_name,
                        vectors_config=vectors_expected,
                        sparse_vectors_config=sparse_expected,
                    )
                return True
            except Exception as setup_err:  # pragma: no cover - network boundary
                self._logger().error(
                    "Failed to ensure Qdrant collection",
                    extra={'event': 'qdrant_create_collection_error', 'collection': collection_name, 'error': str(setup_err)}
                )
                return False
        except Exception as exc:
            self._logger().error(
                "Error creating Qdrant collection",
                extra={'event': 'qdrant_create_collection_exception', 'project_id': project_id, 'error': str(exc)}
            )
            return False

    def _normalize_sparse(self, lexical: Optional[BGESparseVector | Dict[str, Any]]) -> Optional[models.SparseVector]:
        if lexical is None:
            return None
        if isinstance(lexical, BGESparseVector):
            indices = [int(i) % (2 ** 32) for i in lexical.indices]
            return models.SparseVector(indices=indices, values=list(lexical.values))
        if isinstance(lexical, dict):
            with suppress(Exception):
                indices = [int(i) % (2 ** 32) for i in lexical.get('indices', [])]
                values = [float(v) for v in lexical.get('values', [])]
                return models.SparseVector(indices=indices, values=values)
        return None

    def add_document(
        self,
        project_id: int | str,
        document_id: Any,
        text: str,
        embeddings: List[float],
        *,
        lexical: Optional[BGESparseVector | Dict[str, Any]] = None,
        colbert: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            self.create_collection(project_id)

            vectors: Dict[str, Any] = {'dense': embeddings}
            if colbert:
                vectors['colbert'] = list(colbert)

            sparse_vector = self._normalize_sparse(lexical)
            if sparse_vector:
                vectors['lexical'] = sparse_vector.dict(exclude_none=True)

            payload = {
                'text': text,
                'document_id': document_id,
            }
            if metadata:
                payload.update(metadata)

            client.upsert(
                collection_name=collection_name,
                points=[
                    models.PointStruct(
                        id=document_id,
                        vector=vectors,
                        payload=payload,
                    )
                ],
            )
            return True
        except Exception as exc:
            self._logger().error(
                "Error adding document to Qdrant",
                extra={'event': 'qdrant_add_document_error', 'project_id': project_id, 'document_id': document_id, 'error': str(exc)}
            )
            return False

    def delete_document(self, project_id: int | str, document_id: Any) -> bool:
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            from qdrant_client.http.models import PointIdsList

            client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=[document_id]),
            )
            return True
        except Exception as exc:
            self._logger().error(
                "Error deleting Qdrant document",
                extra={'event': 'qdrant_delete_document_error', 'project_id': project_id, 'document_id': document_id, 'error': str(exc)}
            )
            return False

    def fetch_chunk_by_index(self, project_id: int, file_id: Any, chunk_index: int) -> Optional[Dict[str, Any]]:
        if chunk_index is None or chunk_index < 0:
            return None
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            self.create_collection(project_id)

            try:
                base_file_id = int(file_id)
            except Exception:
                base_file_id = abs(hash(str(file_id))) % 100_000

            point_id = base_file_id * 100_000 + int(chunk_index)
            records = client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            ) or []
            if not records:
                return None

            record = records[0]
            entry = self._serialize_point(record, channel='dense', rank=1)
            return entry
        except Exception as exc:
            self._logger().debug(
                "Failed to fetch neighbor chunk",
                exc_info=False,
                extra={'event': 'qdrant_neighbor_fetch_error', 'project_id': project_id, 'file_id': file_id, 'chunk_index': chunk_index, 'error': str(exc)}
            )
            return None

    def fetch_chunk_by_reference(self, project_id: int, reference: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(reference, dict):
            return None
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            self.create_collection(project_id)

            point_id = reference.get('point_id')
            if point_id is not None:
                candidate = point_id
                try:
                    candidate = int(str(point_id))
                except Exception:
                    candidate = str(point_id)
                try:
                    records = client.retrieve(
                        collection_name=collection_name,
                        ids=[candidate],
                        with_payload=True,
                        with_vectors=False,
                    ) or []
                    if records:
                        return self._serialize_point(records[0], channel='dense', rank=1)
                except Exception:
                    pass

            file_id = reference.get('file_id')
            chunk_id = reference.get('chunk_id')
            if file_id is not None and chunk_id is not None:
                try:
                    idx = int(str(chunk_id))
                except Exception:
                    idx = None
                if idx is not None:
                    return self.fetch_chunk_by_index(project_id, file_id, idx)
        except Exception as exc:
            self._logger().debug(
                "Failed to fetch chunk by reference",
                exc_info=False,
                extra={'event': 'qdrant_reference_fetch_error', 'project_id': project_id, 'reference': reference, 'error': str(exc)}
            )
        return None

    def delete_collection(self, project_id: int | str) -> bool:
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            client.delete_collection(collection_name)
            return True
        except Exception as exc:
            self._logger().error(
                "Error deleting Qdrant collection",
                extra={'event': 'qdrant_delete_collection_error', 'collection': collection_name, 'error': str(exc)}
            )
            return False

    def delete_file_chunks(self, project_id: int, file_id: int, chunks_count: int) -> int:
        if not chunks_count or chunks_count <= 0:
            return 0
        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            from qdrant_client.http.models import PointIdsList

            try:
                base_file_id = int(file_id)
            except Exception:
                base_file_id = abs(hash(str(file_id))) % 100_000

            deleted_total = 0
            batch_size = 512
            buffer: List[int] = []
            for idx in range(int(chunks_count)):
                buffer.append(base_file_id * 100_000 + idx)
                if len(buffer) >= batch_size:
                    try:
                        client.delete(collection_name=collection_name, points_selector=PointIdsList(points=buffer))
                        deleted_total += len(buffer)
                    except Exception as batch_exc:
                        self._logger().warning(
                            "Partial failure deleting Qdrant batch",
                            extra={'event': 'qdrant_batch_delete_error', 'collection': collection_name, 'error': str(batch_exc)}
                        )
                    buffer = []
            if buffer:
                try:
                    client.delete(collection_name=collection_name, points_selector=PointIdsList(points=buffer))
                    deleted_total += len(buffer)
                except Exception as final_exc:
                    self._logger().warning(
                        "Final batch delete error",
                        extra={'event': 'qdrant_final_batch_delete_error', 'collection': collection_name, 'error': str(final_exc)}
                    )
            return deleted_total
        except Exception as exc:
            self._logger().error(
                "Error deleting Qdrant file chunks",
                extra={'event': 'qdrant_delete_file_chunks_error', 'project_id': project_id, 'file_id': file_id, 'error': str(exc)}
            )
            return 0

    def _rrf_merge(
        self,
        hits_by_channel: Dict[str, List[Any]],
        *,
        limit: int,
        rrf_k: int,
        score_threshold: Optional[float],
        weights: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        combined: Dict[str, Dict[str, Any]] = {}
        for channel, hits in hits_by_channel.items():
            if not hits:
                continue
            weight = float(weights.get(channel, 1.0)) if isinstance(weights, dict) else 1.0
            if weight <= 0:
                continue
            for rank, point in enumerate(hits, start=1):
                point_id = getattr(point, 'id', None)
                if point_id is None:
                    continue
                key = str(point_id)
                entry = combined.setdefault(
                    key,
                    {
                        'point': point,
                        'rrf': 0.0,
                        'modal_scores': {},
                    },
                )
                entry['rrf'] += weight * (1.0 / (rrf_k + rank))
                score = getattr(point, 'score', None)
                if score is not None:
                    entry['modal_scores'][channel] = float(score)

        if not combined:
            return []

        ranked = sorted(combined.values(), key=lambda item: item['rrf'], reverse=True)
        documents: List[Dict[str, Any]] = []
        for entry in ranked:
            point = entry['point']
            modal_scores = entry['modal_scores']
            if score_threshold is not None:
                best_score = None
                if modal_scores:
                    with suppress(Exception):
                        best_score = max(float(v) for v in modal_scores.values())
                if best_score is None:
                    best_score = getattr(point, 'score', None)
                try:
                    meets_threshold = (best_score is None) or (float(best_score) >= score_threshold)
                except (TypeError, ValueError):
                    meets_threshold = True
                if not meets_threshold:
                    continue
            payload = getattr(point, 'payload', {}) or {}
            if not isinstance(payload, dict):
                payload = {}
            metadata = {k: v for k, v in payload.items() if k != 'text'}
            dense_score = modal_scores.get('dense')
            if dense_score is not None:
                metadata.setdefault('vector_score', dense_score)
            metadata['hybrid_rrf'] = entry['rrf']
            metadata['hybrid_scores'] = modal_scores
            metadata['hybrid_weights'] = weights
            documents.append(
                {
                    'id': point.id,
                    'score': entry['rrf'],
                    'content': payload.get('text', ''),
                    'metadata': metadata,
                }
            )
            if len(documents) >= limit:
                break
        return documents

    def search_similar(
        self,
        project_id: int,
        query_text: str,
        limit: int = 6,
        *,
        debug_label: Optional[str] = None,
        ai_service: Any = None,
        score_threshold: Optional[float] = None,
        usage_tracker: 'UsageTracker' | None = None,
        prefetch_limit: Optional[int] = None,
        colbert_candidates: Optional[int] = None,
        rrf_k_override: Optional[int] = None,
        rrf_weight_overrides: Optional[Dict[str, Any]] = None,
        hybrid_per_vector_limit: Optional[int] = None,
        hybrid_rrf_k: Optional[int] = None,
        collect_diagnostics: bool = False,
    ) -> List[Dict[str, Any]]:
        if prefetch_limit is None and hybrid_per_vector_limit is not None:
            prefetch_limit = hybrid_per_vector_limit
        if rrf_k_override is None and hybrid_rrf_k is not None:
            rrf_k_override = hybrid_rrf_k

        try:
            client = self.get_client()
            collection_name = self._collection_name(project_id)
            self.create_collection(project_id)

            embedding_usage: Optional[TokenUsage] = None
            if ai_service is None:
                from app.services.ai_service import AIService

                ai_service = AIService()
            embedding_result = ai_service.generate_embeddings(query_text)
            if isinstance(embedding_result, tuple):
                query_embeddings = embedding_result[0]
                if len(embedding_result) > 1 and isinstance(embedding_result[1], TokenUsage):
                    embedding_usage = embedding_result[1]
            else:
                query_embeddings = embedding_result

            if usage_tracker is not None and embedding_usage is not None:
                with suppress(Exception):
                    usage_tracker.track('embedding', embedding_usage)

            if not query_embeddings:
                return []

            computed_threshold = self.resolve_score_threshold(score_threshold)
            search_threshold = None if self._debug_enabled() else computed_threshold

            per_vector_limit = self.resolve_prefetch_limit(limit, prefetch_limit)
            colbert_limit = self.resolve_colbert_candidates(colbert_candidates)
            rrf_k = self.resolve_rrf_k(rrf_k_override)
            weights = self.resolve_rrf_weights(rrf_weight_overrides)

            dense_hits = client.search(
                collection_name=collection_name,
                query_vector=models.NamedVector(name='dense', vector=query_embeddings),
                limit=per_vector_limit,
                score_threshold=search_threshold,
                with_payload=True,
                with_vectors=False,
            ) or []

            if dense_hits and self._debug_enabled():
                self._log_debug_results(dense_hits, computed_threshold, label=(debug_label or 'query') + ' [dense]')

            lexical_vector: Optional[BGESparseVector] = None
            colbert_vector: Optional[List[float]] = None
            try:
                bge_result = self._bge().encode([query_text], return_dense=False, return_colbert_vecs=True)
                lexical_vector = bge_result.first_sparse()
                colbert_vector = bge_result.first_colbert_agg()
            except (BGEClientError, ValueError) as exc:
                self._logger().warning(
                    "BGE query failed; continuing with dense-only retrieval",
                    extra={'event': 'bge_query_failed', 'error': str(exc)}
                )
            except Exception as exc:
                self._logger().error(
                    "Unexpected error calling BGE service",
                    extra={'event': 'bge_unexpected_error', 'error': str(exc)}
                )

            lexical_hits: List[Any] = []
            sparse_query = self._normalize_sparse(lexical_vector)
            if sparse_query:
                try:
                    lexical_hits = client.search(
                        collection_name=collection_name,
                        query_vector=models.NamedSparseVector(name='lexical', vector=sparse_query),
                        limit=per_vector_limit,
                        score_threshold=search_threshold,
                        with_payload=True,
                        with_vectors=False,
                    ) or []
                except Exception as exc:
                    self._logger().warning(
                        "Lexical search failed",
                        extra={'event': 'qdrant_lexical_search_failed', 'error': str(exc)}
                    )

            if lexical_hits and self._debug_enabled():
                self._log_debug_results(lexical_hits, 0.0, label=(debug_label or 'query') + ' [lexical]')

            colbert_hits: List[Any] = []
            if colbert_vector:
                try:
                    colbert_hits = client.search(
                        collection_name=collection_name,
                        query_vector=models.NamedVector(name='colbert', vector=colbert_vector),
                        limit=max(colbert_limit, limit),
                        score_threshold=search_threshold,
                        with_payload=True,
                        with_vectors=False,
                    ) or []
                except Exception as exc:
                    self._logger().warning(
                        "ColBERT search failed",
                        extra={'event': 'qdrant_colbert_search_failed', 'error': str(exc)}
                    )

            if colbert_hits and self._debug_enabled():
                self._log_debug_results(colbert_hits, 0.0, label=(debug_label or 'query') + ' [colbert]')

            hits_by_channel = {
                'dense': list(dense_hits or []),
                'lexical': list(lexical_hits or []),
                'colbert': list(colbert_hits or []),
            }

            documents = self._rrf_merge(
                hits_by_channel,
                limit=limit,
                rrf_k=rrf_k,
                score_threshold=computed_threshold,
                weights=weights,
            )

            if collect_diagnostics:
                diagnostic = self._build_search_diagnostic(
                    query=query_text,
                    debug_label=debug_label,
                    limit=limit,
                    score_threshold=computed_threshold,
                    prefetch_limit=per_vector_limit,
                    colbert_limit=colbert_limit,
                    hits_by_channel=hits_by_channel,
                    documents=documents,
                )
                self._store_search_diagnostic(diagnostic)

            return documents

        except Exception as exc:
            self._logger().error(
                "Error searching Qdrant vectors",
                extra={'event': 'qdrant_search_error', 'project_id': project_id, 'error': str(exc)}
            )
            return []

    def check_connectivity(self) -> Tuple[bool, Optional[str]]:
        try:
            client = self.get_client()
            client.get_collections()
            return True, None
        except Exception as exc:
            msg = f"Qdrant connectivity check failed: {exc}"
            self._logger().error(
                "Qdrant connectivity check failed",
                extra={'event': 'qdrant_connectivity_failed', 'error': str(exc)}
            )
            return False, msg

    def diagnostics(self) -> Dict[str, Any]:
        output: Dict[str, Any] = {
            'host': current_app.config.get('QDRANT_HOST'),
            'port': current_app.config.get('QDRANT_PORT'),
        }
        try:
            client = self.get_client()
            from time import time

            start = time()
            collections = client.get_collections()
            elapsed = time() - start
            output['collections_ok'] = True
            output['collections_time_ms'] = round(elapsed * 1000, 2)
            output['collections_count'] = len(getattr(collections, 'collections', []) or [])
        except Exception as exc:
            output['collections_ok'] = False
            output['collections_error'] = str(exc)
            try:
                host = current_app.config.get('QDRANT_HOST', 'localhost')
                port = current_app.config.get('QDRANT_PORT', 6333)
                anon = QdrantClient(host=host, port=port, prefer_grpc=False, https=not current_app.config.get('QDRANT_ALLOW_INSECURE_FALLBACK', False))
                anon.get_collections()
                output['anonymous_access_ok'] = True
            except Exception as anon_exc:
                output['anonymous_access_ok'] = False
                output['anonymous_access_error'] = str(anon_exc)
            return output

        temp_name = f"diag_{uuid.uuid4().hex[:8]}"
        try:
            client.create_collection(collection_name=temp_name, vectors_config=models.VectorParams(size=8, distance=models.Distance.COSINE))
            output['create_tmp_ok'] = True
        except Exception as exc:
            output['create_tmp_ok'] = False
            output['create_tmp_error'] = str(exc)
        try:
            client.delete_collection(temp_name)
            output['delete_tmp_ok'] = True
        except Exception as exc:
            output['delete_tmp_ok'] = False
            output['delete_tmp_error'] = str(exc)
        return output
