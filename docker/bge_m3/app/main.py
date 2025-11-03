from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    from FlagEmbedding import BGEM3FlagModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("FlagEmbedding package is required to run the BGE-M3 service") from exc

logger = logging.getLogger("bge_m3_service")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def _limit_threads() -> int:
    """Limit CPU usage by capping thread pools based on fraction of available cores."""

    try:
        fraction = float(os.environ.get("BGE_MAX_CPU_FRACTION", "0.3"))
    except ValueError:
        fraction = 0.3
    fraction = min(max(fraction, 0.05), 1.0)
    cpu_total = max(os.cpu_count() or 1, 1)
    thread_cap = max(int(round(cpu_total * fraction)) or 1, 1)

    for env_key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(env_key, str(thread_cap))

    try:  # pragma: no cover - torch optional
        import torch

        torch.set_num_threads(thread_cap)
    except Exception:
        logger.debug("Torch not available for thread limiting", exc_info=False)

    return thread_cap


THREAD_LIMIT = _limit_threads()

MODEL_NAME = os.environ.get("MODEL_NAME", "BAAI/bge-m3")
USE_FP16 = os.environ.get("BGE_USE_FP16", "false").lower() in {"1", "true", "yes", "on"}

logger.info(
    "Loading BGEM3 model '%s' (fp16=%s, threads=%s)",
    MODEL_NAME,
    USE_FP16,
    THREAD_LIMIT,
)
model = BGEM3FlagModel(MODEL_NAME, use_fp16=USE_FP16)
logger.info("Model loaded successfully")

app = FastAPI(title="BGE-M3 Embedding Service", version="0.2.0")


class EncodeRequest(BaseModel):
    sentences: List[str] = Field(..., description="List of sentences/documents to encode")
    return_dense: bool = Field(default=True, description="Include dense embeddings in the response")
    return_sparse: bool = Field(default=True, description="Include sparse embeddings (indices & values)")
    return_colbert_vecs: bool = Field(default=False, description="Include token-level ColBERT vectors")


class SparseVector(BaseModel):
    indices: List[int]
    values: List[float]


class LexicalVector(BaseModel):
    tokens: List[str]
    weights: List[float]


class EncodeResponse(BaseModel):
    dense: Optional[List[List[float]]] = None
    lexical_sparse: Optional[List[SparseVector]] = Field(
        default=None, description="Sparse lexical embeddings with hashed token indices"
    )
    colbert: Optional[List[List[List[float]]]] = Field(
        default=None, description="Token-level ColBERT vectors (per sentence)"
    )
    colbert_agg: Optional[List[List[float]]] = Field(
        default=None, description="Aggregated ColBERT vectors (max pooling)"
    )
    meta: Optional[Dict[str, Any]] = None


@lru_cache(maxsize=32_768)
def _hash_token(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big", signed=False)
    # Limit to uint32 range to comply with Qdrant sparse index expectations
    return value % (2**32)


def _lexical_to_sparse(
    lexical_weights: Sequence[Dict[str, float]] | None,
) -> Tuple[List[SparseVector], Dict[str, Any]]:
    if not lexical_weights:
        return [], {"source": "none"}

    sparse_vectors: List[SparseVector] = []
    metadata: Dict[str, Any] = {
        "source": "lexical_weights",
        "rows": len(lexical_weights),
        "token_preview": [],
    }

    for row in lexical_weights:
        if not isinstance(row, dict):
            continue
        tokens = list(row.keys())
        hashed_pairs: List[Tuple[int, float, str]] = []
        for token in tokens:
            weight = float(row[token])
            hashed_pairs.append((_hash_token(token), weight, token))
        hashed_pairs.sort(key=lambda item: item[0])
        indices = [idx for idx, _, _ in hashed_pairs]
        sorted_values = [weight for _, weight, _ in hashed_pairs]
        sorted_tokens = [token for _, _, token in hashed_pairs]
        sparse_vectors.append(SparseVector(indices=indices, values=sorted_values))
        if len(metadata["token_preview"]) < 3:
            preview_row = [
                {
                    "token": token,
                    "hash": hashed,
                    "weight": weight,
                }
                for token, hashed, weight in zip(
                    sorted_tokens[:5], indices[:5], sorted_values[:5]
                )
            ]
            metadata["token_preview"].append(preview_row)

    metadata["non_empty"] = len(sparse_vectors)
    return sparse_vectors, metadata


def _aggregate_colbert(colbert_vecs: Sequence[Sequence[Sequence[float]]]) -> List[List[float]]:
    aggregated: List[List[float]] = []
    for token_matrix in colbert_vecs:
        if not token_matrix:
            aggregated.append([])
            continue
        mat = np.asarray(token_matrix, dtype=float)
        # Max pooling across token dimension emulates late interaction (MaxSim)
        pooled = np.max(mat, axis=0)
        aggregated.append(pooled.tolist())
    return aggregated


def _head_tail(values: Sequence[Any] | None, size: int = 3) -> Dict[str, List[Any]]:
    seq = list(values) if values is not None else []
    if not seq:
        return {"head": [], "tail": []}
    head = seq[:size]
    tail = seq[-size:] if len(seq) > size else seq[:]
    return {"head": head, "tail": tail}


@app.get("/healthz", tags=["health"])
def healthcheck() -> dict[str, str]:
    """Basic health endpoint to verify readiness."""
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/encode", response_model=EncodeResponse, tags=["encode"])
def encode(payload: EncodeRequest) -> EncodeResponse:
    if not payload.sentences:
        raise HTTPException(status_code=400, detail="'sentences' must contain at least one element")

    try:
        result = model.encode(
            payload.sentences,
            return_dense=payload.return_dense,
            return_sparse=payload.return_sparse,
            return_colbert_vecs=payload.return_colbert_vecs,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("Model encoding failed")
        raise HTTPException(status_code=500, detail=f"Model encoding failed: {exc}") from exc

    response = EncodeResponse()

    if payload.return_dense:
        dense_vecs = result.get("dense_vecs")
        if dense_vecs is not None:
            response.dense = np.asarray(dense_vecs).astype(float).tolist()

    lexical_meta: Dict[str, Any] = {}
    lexical_vectors: List[SparseVector] = []
    if payload.return_sparse:
        lexical_raw = result.get("lexical_weights")
        lexical_vectors, lexical_meta = _lexical_to_sparse(lexical_raw)
        response.lexical_sparse = lexical_vectors if lexical_vectors else None
    else:
        lexical_meta["requested"] = False

    if payload.return_colbert_vecs:
        colbert_vecs = result.get("colbert_vecs")
        if colbert_vecs is not None:
            colbert_payload: List[List[List[float]]] = []
            for sentence_vec in colbert_vecs:
                arr = np.asarray(sentence_vec).astype(float)
                colbert_payload.append(arr.tolist())
            response.colbert = colbert_payload

            try:
                response.colbert_agg = _aggregate_colbert(colbert_payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to aggregate ColBERT vectors: %s", exc)

    response.meta = {
        "lexical": lexical_meta,
        "dense_count": len(response.dense or []),
        "colbert_count": len(response.colbert or []),
        "lexical_non_empty": len(response.lexical_sparse or []),
    }

    try:
        dense_sample = response.dense[0] if response.dense else []
        lexical_sample = lexical_vectors[0] if lexical_vectors else None
        colbert_sample = response.colbert_agg[0] if response.colbert_agg else []
        summary: Dict[str, Any] = {
            "sentences": len(payload.sentences),
            "dense": {
                "count": len(response.dense or []),
                "dim": len(dense_sample) if dense_sample else 0,
                **_head_tail(dense_sample),
            },
            "lexical": {
                "count": len(lexical_vectors),
                "non_empty": len(response.lexical_sparse or []),
                "indices": _head_tail(lexical_sample.indices if lexical_sample else []),
                "values": _head_tail(lexical_sample.values if lexical_sample else []),
            },
            "colbert_agg": {
                "count": len(response.colbert_agg or []),
                "dim": len(colbert_sample) if colbert_sample else 0,
                **_head_tail(colbert_sample),
            },
        }
        if response.colbert:
            first_colbert = response.colbert[0]
            summary["colbert_tokens"] = {
                "token_rows": len(first_colbert or []),
                "token_dim": len(first_colbert[0]) if first_colbert else 0,
            }
        logger.info("Encode output summary: %s", summary, extra={"event": "bge_encode_summary"})
    except Exception:  # pragma: no cover - logging best-effort
        logger.debug("Failed to summarise encode output", exc_info=True)

    return response
