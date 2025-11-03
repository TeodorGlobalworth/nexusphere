from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app import db
from app.models.generation_history import GenerationHistory


def build_chunk_refs(documents: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if not documents:
        return refs
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        metadata = doc.get('metadata') or {}
        point_id = doc.get('id') or metadata.get('point_id')
        file_id = metadata.get('file_id') or metadata.get('fileId') or metadata.get('document_id')
        chunk_id = metadata.get('chunk_id') or metadata.get('chunk_index') or metadata.get('chunkId')
        if point_id is None and chunk_id is None:
            continue
        ref: Dict[str, Any] = {
            'point_id': str(point_id) if point_id is not None else None,
            'file_id': file_id,
            'chunk_id': chunk_id,
        }
        file_name = metadata.get('filename') or metadata.get('file_name') or metadata.get('name') or metadata.get('title')
        if file_name:
            ref['file_name'] = file_name
        matched_query = metadata.get('matched_query') or metadata.get('matchedQuery')
        if matched_query:
            ref['matched_query'] = matched_query
        rerank_score = metadata.get('rerank_score') or metadata.get('rerankScore')
        if rerank_score is not None:
            ref['rerank_score'] = rerank_score
        vector_score = doc.get('score')
        if vector_score is not None:
            ref['vector_score'] = vector_score
        refs.append(ref)
    return refs


def extract_chunk_filenames(chunk_refs: Optional[Iterable[Dict[str, Any]]]) -> List[str]:
    names: List[str] = []
    if not chunk_refs:
        return names
    for ref in chunk_refs:
        if not isinstance(ref, dict):
            continue
        name = ref.get('file_name')
        if not name:
            continue
        name_str = str(name)
        if name_str not in names:
            names.append(name_str)
    return names


def persist_history_entry(
    *,
    project,
    source_kind: str,
    title: str,
    response_body: str,
    total_tokens: int = 0,
    chunk_refs: Optional[List[Dict[str, Any]]] = None,
    source_address: Optional[str] = None,
    source_user: Optional[str] = None,
    spam_flag: Optional[bool] = None,
    importance_level: Optional[str] = None,
    commit: bool = True,
) -> GenerationHistory:
    entry = GenerationHistory(
        project_id=project.id,
        project_name=project.name,
        source_kind=source_kind,
        source_address=source_address,
        source_user=source_user,
        title=title,
        response_body=response_body,
        chunk_refs=chunk_refs or [],
        total_tokens=total_tokens or 0,
        spam_flag=spam_flag,
        importance_level=importance_level,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
