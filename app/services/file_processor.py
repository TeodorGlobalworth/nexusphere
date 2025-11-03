import logging
import os
import threading
import json

from datetime import datetime

from flask import current_app

from app import db
from app.models.ai_usage_log import AIUsageLog
from app.models.file_processing_log import FileProcessingLog
from app.models.knowledge_file import KnowledgeFile
from app.services.ai_service import AIService
from app.services.bge_client import BGEClient, BGEClientError
from app.services.vector_service import VectorService
from sqlalchemy import text

# Celery removed - processing is now synchronous or handled by external automaton
celery = None

# Local exception to signal guarded OCR blocking
class ProcessingBlocked(Exception):
    pass

class FileProcessor:
    def __init__(self, app=None):
        """Optionally pass Flask app for app_context in background thread."""
        from flask import current_app as _ca
        try:
            # Try to capture the real app object if available
            self.app = app or _ca._get_current_object()
        except Exception:
            self.app = app
        # Lazy init to avoid current_app access at import time (Celery worker startup)
        self._ai_service = None
        self._vector_service = None
        self._bge_client = None
        # Limits (can be sourced from config later)
        self.MAX_PDF_PAGES = int(os.environ.get('MAX_PDF_PAGES', 50))
        # Backward-compatible switch used by preview to skip OCR entirely when set to 0
        self.MAX_OCR_IMAGES = int(os.environ.get('MAX_OCR_IMAGES', 10))
        # New granular limits/thresholds
        self.MAX_OCR_PAGES = int(os.environ.get('MAX_OCR_PAGES', 200))
        self.MIN_TEXT_LENGTH_FOR_SKIPPING_OCR = 2000  # chars (legacy global toggle)
        # Optional PDF table extraction limits
        self.MAX_PDF_TABLES = int(os.environ.get('MAX_PDF_TABLES', 10))
        self.MAX_PDF_TABLE_ROWS = int(os.environ.get('MAX_PDF_TABLE_ROWS', 500))
        # Page classification thresholds
        self.IMAGE_COVER_THRESHOLD = float(os.environ.get('PDF_IMAGE_COVER_THRESHOLD', 0.7))
        self.MIN_TEXT_CHARS_NATIVE = int(os.environ.get('PDF_MIN_TEXT_CHARS_NATIVE', 10))
        # OCR guard
        self.OCR_GUARD_MIN_PAGES = int(os.environ.get('OCR_GUARD_MIN_PAGES', 10))
        self.OCR_GUARD_PERCENT = float(os.environ.get('OCR_GUARD_PERCENT', 0.10))
        self.RETRY_LOOKBACK_MIN = int(os.environ.get('RETRY_LOOKBACK_MIN', 120))
        # Runtime accumulator for OCR token usage per file (reset each file)
        self._ocr_tokens_total = 0
        self._fallback_logger = logging.getLogger(__name__)

    def ai_service(self):
        if self._ai_service is None:
            self._ai_service = AIService()
        return self._ai_service

    def vector_service(self):
        if self._vector_service is None:
            self._vector_service = VectorService()
        return self._vector_service

    def bge_client(self) -> BGEClient:
        if self._bge_client is None:
            self._bge_client = BGEClient()
        return self._bge_client

    def process_file_async(self, file_id, force_full_ocr: bool = False):
        """Enqueue background file processing.

        force_full_ocr: (ephemeral) if True and file is PDF every page will be OCR'd this run only.
        We deliberately do NOT persist this choice on the KnowledgeFile row anymore.
        """
        # Prefer Celery if available
        if celery is not None:
            try:
                result = process_file_task.apply_async((file_id, force_full_ocr), queue='embeddings', routing_key='embeddings.process_file')
                try:
                    tid = getattr(result, 'id', None)
                except Exception:
                    tid = None
                self._logger().info(
                    "Queued file processing task",
                    extra={'event': 'file_processor_celery_enqueued', 'file_id': file_id, 'force_full_ocr': force_full_ocr, 'task_id': tid}
                )
                return
            except Exception as e:
                self._logger().warning(
                    "Celery enqueue failed; falling back to local thread",
                    extra={'event': 'file_processor_celery_failed', 'file_id': file_id, 'error': str(e)}
                )
        # Fallback to local thread
        thread = threading.Thread(target=self._process_file, args=(file_id, force_full_ocr))
        thread.daemon = True
        thread.start()

    def _logger(self):
        if self.app is not None:
            try:
                return self.app.logger
            except Exception:
                pass
        return self._fallback_logger

    def _process_file(self, file_id, force_full_ocr: bool = False):
        """Process uploaded file and create embeddings"""
        try:
            if self.app is None:
                return
            with self.app.app_context():
                logger = self._logger()
                logger.info(
                    "Starting file processing",
                    extra={'event': 'file_processor_start', 'file_id': file_id, 'force_full_ocr': force_full_ocr}
                )
                # Reset per-file OCR token accumulator
                try:
                    self._ocr_tokens_total = 0
                except Exception:
                    self._ocr_tokens_total = 0
                # Superadmin for RLS bypass
                try:
                    db.session.execute(text("SELECT set_config('app.is_superadmin','1', true)"))
                    db.session.execute(text("SELECT set_config('app.org_id','', true)"))
                except Exception:
                    pass

                knowledge_file = db.session.get(KnowledgeFile, file_id)
                if not knowledge_file:
                    logger.warning(
                        "Knowledge file not found",
                        extra={'event': 'file_processor_missing_file', 'file_id': file_id}
                    )
                    return

                knowledge_file.status = 'processing'
                db.session.commit()
                try:
                    db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='start', message='Processing started'))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

                # --- Qdrant connectivity pre-check ---
                ok, err = self.vector_service().check_connectivity()
                if not ok:
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = 'Błąd połączenia z silnikiem wektorowym (Qdrant). Spróbuj ponownie później.'
                    try:
                        db.session.add(FileProcessingLog(
                            knowledge_file_id=knowledge_file.id,
                            project_id=knowledge_file.project_id,
                            event='error',
                            message=f"{knowledge_file.error_message} Szczegóły: {err}"[:1000]
                        ))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass
                    logger.error(
                        "Aborting processing due to Qdrant connectivity failure",
                        extra={'event': 'file_processor_qdrant_failure', 'file_id': file_id, 'project_id': knowledge_file.project_id, 'error': err}
                    )
                    return

                logger.info(
                    "Converting file",
                    extra={'event': 'file_processor_convert_start', 'file_id': file_id, 'path': knowledge_file.file_path, 'file_type': knowledge_file.file_type}
                )
                # Special handling for XLSX: custom pipeline embeds Description!A2 and adds table markdown to metadata
                try:
                    if (knowledge_file.file_type or '').lower() == 'xlsx':
                        from app.services.file_processors.xlsx_processor import convert_xlsx_to_markdown_and_embed
                        md, processed_x, tokens_x = convert_xlsx_to_markdown_and_embed(self, knowledge_file, do_embed=True)
                        if processed_x and processed_x > 0:
                            knowledge_file.status = 'processed'
                            knowledge_file.processed_at = datetime.utcnow()
                            knowledge_file.chunks_count = int(processed_x)
                            try:
                                knowledge_file.vector_collection = self.vector_service()._collection_name(knowledge_file.project_id)
                            except Exception:
                                knowledge_file.vector_collection = f"project_{knowledge_file.project_id}"
                            # Update token counters
                            if tokens_x:
                                try:
                                    from app.models.project import Project
                                    proj = db.session.get(Project, knowledge_file.project_id)
                                    if proj:
                                        proj.tokens_used_input += int(tokens_x)
                                        db.session.add(proj)
                                except Exception:
                                    pass
                            try:
                                db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='success', message=f'Processed XLSX: {processed_x} vector(s)'))
                                db.session.commit()
                            except Exception:
                                db.session.rollback()
                            # Atomic fragment increment and broadcast
                            try:
                                prev_row = db.session.execute(text("SELECT fragments_used, packages_assigned FROM project WHERE id=:pid FOR UPDATE"), { 'pid': knowledge_file.project_id }).fetchone()
                                prev_used = prev_row[0] if prev_row else 0
                                packages_assigned = prev_row[1] if prev_row else 1
                                limit_val = packages_assigned * 2000
                                updated = db.session.execute(
                                    text("""
                                        UPDATE project
                                        SET fragments_used = fragments_used + :inc
                                        WHERE id = :pid
                                        RETURNING fragments_used, (fragments_used > :limit) AS is_over
                                    """),
                                    { 'inc': int(processed_x), 'pid': knowledge_file.project_id, 'limit': limit_val }
                                ).fetchone()
                                db.session.commit()
                                if updated:
                                    new_used = updated[0]
                                    is_over = updated[1]
                                    if prev_used <= limit_val and is_over:
                                        current_app.logger.warning(f"Project {knowledge_file.project_id} entered fragment overflow (used={new_used} limit={limit_val})")
                                    try:
                                        from app.ws import broadcast_project_usage
                                        broadcast_project_usage(knowledge_file.project_id, fragments_used=new_used, fragments_limit=limit_val)
                                    except Exception:
                                        pass
                            except Exception as ie:
                                logger.warning(
                                    "Failed to update fragment usage after XLSX processing",
                                    extra={'event': 'file_processor_fragments_update_failed', 'file_id': file_id, 'error': str(ie)}
                                )
                                db.session.rollback()
                            try:
                                from app.ws import broadcast_knowledge_update
                                broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                            except Exception:
                                pass
                            return
                        else:
                            # Error state should be set inside converter; if not, add generic message
                            if not knowledge_file.error_message:
                                knowledge_file.error_message = 'Nie udało się przetworzyć pliku XLSX (brak opisu w Description!A2?)'
                            knowledge_file.status = 'error'
                            db.session.commit()
                            try:
                                from app.ws import broadcast_knowledge_update
                                broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                            except Exception:
                                pass
                            return
                except Exception as exlsx:
                    current_app.logger.exception('XLSX processing error: %s', exlsx)
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = f'Błąd XLSX: {exlsx}'
                    db.session.commit()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass
                    return
                try:
                    markdown_content = self._convert_file_to_markdown(knowledge_file, force_full_ocr=force_full_ocr)
                except ProcessingBlocked as pb:
                    # Graceful error with explicit message about retry override
                    msg = str(pb)[:1000]
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = msg
                    db.session.commit()
                    try:
                        db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='blocked', message=msg))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass
                    return
                logger.debug(
                    "Conversion completed",
                    extra={'event': 'file_processor_conversion_done', 'file_id': file_id, 'content_length': len(markdown_content) if markdown_content else 0}
                )
                if not markdown_content:
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = 'Nie udało się wyodrębnić tekstu z pliku'
                    db.session.commit()
                    try:
                        db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='error', message=knowledge_file.error_message))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return
                # Clean markdown before chunking/embedding
                try:
                    cleaned = self._clean_markdown(markdown_content)
                except Exception:
                    cleaned = markdown_content
                # Persist cleaned markdown sidecar for later fast preview (avoid re-running OCR/conversion)
                try:
                    if knowledge_file.file_path:
                        sidecar_path = f"{knowledge_file.file_path}.md"
                        with open(sidecar_path, 'w', encoding='utf-8') as _sc:
                            _sc.write(cleaned)
                        logger.debug(
                            "Wrote markdown sidecar",
                            extra={'event': 'file_processor_sidecar_written', 'file_id': file_id, 'sidecar_path': sidecar_path}
                        )
                except Exception as sc_e:
                    logger.warning(
                        "Failed writing markdown sidecar",
                        extra={'event': 'file_processor_sidecar_failed', 'file_id': file_id, 'error': str(sc_e)}
                    )
                chunks = self.ai_service().chunk_text(cleaned)
                logger.debug(
                    "Chunking completed",
                    extra={'event': 'file_processor_chunking_done', 'file_id': file_id, 'chunk_count': len(chunks)}
                )
                if not chunks:
                    logger.warning(
                        "No chunks produced after conversion",
                        extra={'event': 'file_processor_no_chunks', 'file_id': file_id}
                    )
                self.vector_service().create_collection(knowledge_file.project_id)

                processed_chunks = 0
                embedding_tokens_total = 0
                ingestion_failed = False
                ingestion_error = None
                # Monthly token enforcement context
                try:
                    from app.models.project import Project as _P
                    proj_for_limit = db.session.get(_P, knowledge_file.project_id)
                except Exception:
                    proj_for_limit = None
                monthly_used_before = 0
                monthly_add = 0
                if proj_for_limit:
                    try:
                        monthly_used_before = proj_for_limit.get_monthly_tokens_used()
                    except Exception:
                        monthly_used_before = 0
                for i, chunk in enumerate(chunks):
                    if not chunk or not chunk.strip():
                        continue
                    if i % 5 == 0:
                        logger.debug(
                            "Embedding chunk progress",
                            extra={'event': 'file_processor_chunk_progress', 'file_id': file_id, 'chunk_index': i, 'chunk_count': len(chunks), 'chunk_length': len(chunk)}
                        )
                    # Fragment limit enforcement (allow processing if already started and still within overflow policy)
                    try:
                        if proj_for_limit:
                            projected_total = proj_for_limit.fragments_used + 1  # this chunk
                            if projected_total > proj_for_limit.fragments_limit and processed_chunks == 0:
                                # Abort whole file before adding any chunks (hard block)
                                knowledge_file.status = 'error'
                                knowledge_file.error_message = 'Limit fragmentów przekroczony – nie można przetworzyć pliku.'
                                db.session.commit()
                                try:
                                    db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='error', message=knowledge_file.error_message))
                                    db.session.commit()
                                except Exception:
                                    db.session.rollback()
                                return
                            # If projected_total exceeds limit but we already processed some chunks of this file, allow overflow (single file overflow policy)
                    except Exception:
                        pass
                    # Monthly token limit enforcement (estimate tokens before calling embeddings)
                    try:
                        tokens_est = self.ai_service().count_tokens(chunk)
                    except Exception:
                        tokens_est = 0
                    logger.debug(
                        "Estimated tokens for chunk",
                        extra={'event': 'file_processor_chunk_token_estimate', 'file_id': file_id, 'chunk_index': i, 'tokens_est': tokens_est}
                    )
                    if proj_for_limit and proj_for_limit.tokens_limit and tokens_est:
                        if (monthly_used_before + monthly_add + tokens_est) > proj_for_limit.tokens_limit:
                            if processed_chunks == 0:
                                knowledge_file.status = 'error'
                                knowledge_file.error_message = 'Miesięczny limit tokenów przekroczony'
                                db.session.commit()
                                try:
                                    db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='error', message=knowledge_file.error_message))
                                    db.session.commit()
                                except Exception:
                                    db.session.rollback()
                                return
                            # Stop processing more chunks; finalize with partial progress
                            logger.info(
                                "Stopping further chunks due to monthly token limit",
                                extra={'event': 'file_processor_monthly_limit_stop', 'file_id': file_id, 'processed_chunks': processed_chunks}
                            )
                            break
                    embeddings, emb_usage = self.ai_service().generate_embeddings(chunk)
                    if not embeddings:
                        logger.warning(
                            "Failed generating embeddings for chunk",
                            extra={'event': 'file_processor_embedding_failure', 'file_id': file_id, 'chunk_index': i}
                        )
                        try:
                            db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='embedding_error', message=f'Brak embeddingu dla fragmentu {i} (len={len(chunk)})'))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        continue
                    logger.debug(
                        "Generated embeddings for chunk",
                        extra={'event': 'file_processor_embedding_generated', 'file_id': file_id, 'chunk_index': i, 'embedding_dim': len(embeddings) if hasattr(embeddings, '__len__') else None}
                    )

                    lexical_sparse = None
                    colbert_vector = None
                    try:
                        bge_result = self.bge_client().encode([chunk], return_dense=False, return_colbert_vecs=True)
                        lexical_sparse = bge_result.first_sparse()
                        colbert_vector = bge_result.first_colbert_agg()
                    except BGEClientError as exc:
                        logger.warning(
                            "BGE ingestion encode failed",
                            extra={'event': 'file_processor_bge_failed', 'file_id': file_id, 'chunk_index': i, 'error': str(exc)}
                        )
                    except Exception as exc:  # pragma: no cover - unexpected networking error
                        logger.error(
                            "Unexpected error calling BGE service during ingestion",
                            extra={'event': 'file_processor_bge_error', 'file_id': file_id, 'chunk_index': i, 'error': str(exc)}
                        )
                    # Qdrant requires point id to be unsigned int or UUID. Use a deterministic integer space per file.
                    # Reserve 100000 slots per file which is far above realistic chunk counts.
                    try:
                        base_file_id = int(file_id)
                    except Exception:
                        base_file_id = abs(hash(str(file_id))) % 100000  # fallback hashing
                    point_id = base_file_id * 100000 + i
                    success = self.vector_service().add_document(
                        project_id=knowledge_file.project_id,
                        document_id=point_id,
                        text=chunk,
                        embeddings=embeddings,
                        lexical=lexical_sparse,
                        colbert=colbert_vector,
                        metadata={
                            'file_id': file_id,
                            'filename': knowledge_file.original_filename,
                            'chunk_index': i,
                            'file_type': knowledge_file.file_type,
                            'chunk_key': f"{file_id}_{i}"
                        }
                    )
                    if not success:
                        failure_message = f'Upsert nieudany fragment {i} point_id={point_id}'
                        logger.warning(
                            "Failed to upsert chunk into vector store",
                            extra={'event': 'file_processor_vector_upsert_failed', 'file_id': file_id, 'chunk_index': i, 'point_id': point_id}
                        )
                        try:
                            db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='vector_upsert_error', message=failure_message))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        ingestion_failed = True
                        ingestion_error = failure_message
                        break
                    if success:
                        processed_chunks += 1
                        try:
                            tokens = emb_usage.total_tokens or emb_usage.prompt_tokens
                            if not tokens:
                                tokens = self.ai_service().count_tokens(chunk)
                            embedding_tokens_total += tokens or 0
                            monthly_add += tokens or 0
                            log = AIUsageLog(
                                project_id=knowledge_file.project_id,
                                source='embedding',
                                model=current_app.config.get('EMBEDDING_MODEL','text-embedding-3-large'),
                                prompt_tokens=emb_usage.prompt_tokens or tokens or 0,
                                completion_tokens=emb_usage.completion_tokens,
                                total_tokens=emb_usage.total_tokens or tokens or 0,
                                metadata_json=json.dumps({
                                    'knowledge_file_id': knowledge_file.id,
                                    'chunk_index': i,
                                    'embedding_usage': emb_usage.to_dict(),
                                })
                            )
                            db.session.add(log)
                        except Exception:
                            pass

                if ingestion_failed:
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = ingestion_error or 'Błąd podczas zapisu do silnika wektorowego'
                    logger.error(
                        "Stopping file processing due to vector upsert failure",
                        extra={'event': 'file_processor_upsert_aborted', 'file_id': file_id, 'processed_chunks': processed_chunks, 'error': knowledge_file.error_message}
                    )
                    try:
                        db.session.add(FileProcessingLog(
                            knowledge_file_id=knowledge_file.id,
                            project_id=knowledge_file.project_id,
                            event='error',
                            message=(knowledge_file.error_message or '')[:1000]
                        ))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return

                if processed_chunks > 0:
                    knowledge_file.status = 'processed'
                    knowledge_file.processed_at = datetime.utcnow()
                    knowledge_file.chunks_count = processed_chunks
                    # Use actual collection naming logic from VectorService for consistency
                    try:
                        knowledge_file.vector_collection = self.vector_service()._collection_name(knowledge_file.project_id)
                    except Exception:
                        knowledge_file.vector_collection = f"project_{knowledge_file.project_id}"
                    logger.info(
                        "File processing completed",
                        extra={'event': 'file_processor_success', 'file_id': file_id, 'processed_chunks': processed_chunks, 'project_id': knowledge_file.project_id}
                    )
                    # Update project token counters once for all embeddings
                    if embedding_tokens_total > 0:
                        try:
                            from app.models.project import Project
                            proj = db.session.get(Project, knowledge_file.project_id)
                            if proj:
                                proj.tokens_used_input += embedding_tokens_total
                                db.session.add(proj)
                        except Exception:
                            pass
                    try:
                        db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='success', message=f'Processed {processed_chunks} chunks'))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    # Increment fragment usage atomically using UPDATE ... RETURNING to avoid lost updates under concurrency
                    try:
                        # Fetch previous fragments_used + limit for transition detection
                        prev_row = db.session.execute(text("SELECT fragments_used, packages_assigned FROM project WHERE id=:pid FOR UPDATE"), { 'pid': knowledge_file.project_id }).fetchone()
                        prev_used = prev_row[0] if prev_row else 0
                        packages_assigned = prev_row[1] if prev_row else 1
                        limit_val = packages_assigned * 2000
                        updated = db.session.execute(
                            text("""
                                UPDATE project
                                SET fragments_used = fragments_used + :inc
                                WHERE id = :pid
                                RETURNING fragments_used, (fragments_used > :limit) AS is_over
                            """),
                            { 'inc': processed_chunks, 'pid': knowledge_file.project_id, 'limit': limit_val }
                        ).fetchone()
                        db.session.commit()
                        if updated:
                            new_used = updated[0]
                            is_over = updated[1]
                            logger.debug(
                                "Updated fragment usage",
                                extra={'event': 'file_processor_fragments_updated', 'project_id': knowledge_file.project_id, 'increment': processed_chunks, 'fragments_used': new_used}
                            )
                            # Transition logging: only log when crossing boundary
                            if prev_used <= limit_val and is_over:
                                current_app.logger.warning(f"Project {knowledge_file.project_id} entered fragment overflow (used={new_used} limit={limit_val})")
                            # Broadcast usage update
                            try:
                                from app.ws import broadcast_project_usage
                                broadcast_project_usage(knowledge_file.project_id, fragments_used=new_used, fragments_limit=limit_val)
                            except Exception:
                                pass
                    except Exception as ie:
                        logger.error(
                            "Atomic fragment usage update failed",
                            extra={'event': 'file_processor_fragments_update_error', 'project_id': knowledge_file.project_id, 'error': str(ie)}
                        )
                        db.session.rollback()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass
                else:
                    knowledge_file.status = 'error'
                    knowledge_file.error_message = 'Nie udało się przetworzyć żadnego fragmentu pliku'
                    logger.error(
                        "No chunks processed",
                        extra={'event': 'file_processor_zero_chunks', 'file_id': file_id, 'chunk_count': len(chunks)}
                    )
                    try:
                        db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='error', message=knowledge_file.error_message))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    try:
                        from app.ws import broadcast_knowledge_update
                        broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                    except Exception:
                        pass

                # Commit usage logs and file status
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        except Exception as e:
            try:
                if self.app is not None:
                    with self.app.app_context():
                        self._logger().exception(
                            "Unhandled exception during file processing",
                            extra={'event': 'file_processor_exception', 'file_id': file_id}
                        )
                        knowledge_file = db.session.get(KnowledgeFile, file_id)
                        if knowledge_file:
                            knowledge_file.status = 'error'
                            knowledge_file.error_message = f'Błąd przetwarzania: {str(e)}'
                            db.session.commit()
                            try:
                                db.session.add(FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=knowledge_file.project_id, event='error', message=knowledge_file.error_message))
                                db.session.commit()
                            except Exception:
                                db.session.rollback()
                            try:
                                from app.ws import broadcast_knowledge_update
                                broadcast_knowledge_update(knowledge_file.project_id, knowledge_file.id)
                            except Exception:
                                pass
            finally:
                pass

    def _convert_file_to_markdown(self, knowledge_file, force_full_ocr: bool = False):
        logger = self._logger()
        try:
            file_path = knowledge_file.file_path
            file_type = knowledge_file.file_type.lower()
            from app.services.file_processors import (
                extract_text_from_txt,
                convert_pdf_to_markdown,
                convert_docx_to_markdown,
                convert_odt_to_markdown,
                convert_rtf_to_markdown,
                convert_html_to_markdown,
                convert_eml_to_markdown,
                convert_msg_to_markdown,
                convert_image_to_markdown,
                convert_xlsx_to_markdown_and_embed,
            )
            if file_type in ['txt', 'md']:
                return extract_text_from_txt(file_path)
            if file_type == 'pdf':
                # Use ephemeral flag passed through the processing pipeline; ignore persistent DB flag
                return convert_pdf_to_markdown(self, knowledge_file, force_full_ocr=bool(force_full_ocr))
            if file_type in ['doc', 'docx']:
                return convert_docx_to_markdown(file_path)
            if file_type == 'odt':
                return convert_odt_to_markdown(file_path)
            if file_type == 'rtf':
                return convert_rtf_to_markdown(file_path)
            if file_type in ['html', 'htm']:
                return convert_html_to_markdown(file_path)
            if file_type == 'eml':
                return convert_eml_to_markdown(file_path)
            if file_type == 'msg':
                return convert_msg_to_markdown(file_path)
            if file_type in ['jpg', 'jpeg', 'png']:
                return convert_image_to_markdown(self, knowledge_file)
            if file_type in ['xlsx']:
                # For preview/conversion only: build markdown but DO NOT embed here
                md, _p, _t = convert_xlsx_to_markdown_and_embed(self, knowledge_file, do_embed=False)
                return md
            return None
        except Exception as e:
            logger.exception(
                "Error converting file to markdown",
                extra={'event': 'file_processor_markdown_convert_error', 'file_id': knowledge_file.id if knowledge_file else None, 'filename': getattr(knowledge_file, 'original_filename', None)}
            )
            return None

    def _has_recent_retry(self, knowledge_file_id: int) -> bool:
        try:
            cutoff_sql = text("SELECT now() - (:mins || ' minutes')::interval")
            cutoff = db.session.execute(cutoff_sql, {"mins": self.RETRY_LOOKBACK_MIN}).fetchone()[0]
            row = FileProcessingLog.query \
                .filter_by(knowledge_file_id=knowledge_file_id, event='retry') \
                .order_by(FileProcessingLog.created_at.desc()) \
                .first()
            return bool(row and getattr(row, 'created_at', None) and row.created_at >= cutoff)
        except Exception:
            return False

    def _ocr_png_bytes_to_text(self, png_bytes: bytes, project_id: int, page_no: int, debug: bool = False, out_dir: str | None = None) -> tuple[str, dict]:
        """Delegate OCR to AIService; retry once if empty.
        Keeps legacy signature, returning (text, usage_dict).
        Accepts debug/out_dir to forward debug artifacts from callers (e.g. PDF processor).
        """
        if getattr(self, 'MAX_OCR_IMAGES', 1) == 0:
            return '', {}
        service = self.ai_service()
        text1, usage1 = ('', {})
        try:
            text1, usage1 = service.ocr_image_to_text(png_bytes, page_no=page_no, debug=debug, out_dir=out_dir)
        except Exception:
            text1 = ''
        if text1 and text1.strip():
            # Log + accumulate usage if tokens present
            try:
                total = int(usage1.get('total_tokens') or 0)
                if total > 0:
                    log = AIUsageLog(
                        project_id=project_id,
                        source='ocr',
                        model=current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini'),
                        prompt_tokens=int(usage1.get('input_tokens') or usage1.get('prompt_tokens') or 0),
                        completion_tokens=int(usage1.get('output_tokens') or usage1.get('completion_tokens') or 0),
                        total_tokens=total
                    )
                    db.session.add(log)
                    db.session.commit()
                    self._ocr_tokens_total = getattr(self, '_ocr_tokens_total', 0) + total
            except Exception:
                db.session.rollback()
            return text1.strip(), usage1
        # Retry once
        try:
            text2, usage2 = service.ocr_image_to_text(png_bytes, page_no=page_no, debug=debug, out_dir=out_dir)
            if text2 and text2.strip():
                try:
                    total = int(usage2.get('total_tokens') or 0)
                    if total > 0:
                        log = AIUsageLog(
                            project_id=project_id,
                            source='ocr',
                            model=current_app.config.get('OPENAI_RESPONSES_MODEL', 'gpt-5-mini'),
                            prompt_tokens=int(usage2.get('input_tokens') or usage2.get('prompt_tokens') or 0),
                            completion_tokens=int(usage2.get('output_tokens') or usage2.get('completion_tokens') or 0),
                            total_tokens=total
                        )
                        db.session.add(log)
                        db.session.commit()
                        self._ocr_tokens_total = getattr(self, '_ocr_tokens_total', 0) + total
                except Exception:
                    db.session.rollback()
                return text2.strip(), usage2
            return '', usage2 or usage1
        except Exception:
            return '', usage1

    def _extract_text_from_txt(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            self._logger().warning(
                "Error reading text file",
                extra={'event': 'file_processor_txt_read_error', 'file_path': file_path, 'error': str(e)}
            )
            return ''

    def _clean_markdown(self, text: str) -> str:
        """Normalize whitespace: collapse multiple spaces/tabs and excessive blank lines."""
        if not text:
            return ''
        import re
        # Normalize line endings
        t = text.replace('\r\n', '\n').replace('\r', '\n')
        lines = []
        for ln in t.split('\n'):
            m = None
            try:
                m = __import__('re').match(r'^(\s*)(.*)$', ln)
            except Exception:
                pass
            if m:
                lead, rest = m.group(1), m.group(2)
            else:
                lead, rest = '', ln
            # Collapse tabs to spaces first
            rest = rest.replace('\t', ' ')
            # Collapse multiple spaces inside the content (preserve leading indentation)
            rest = re.sub(r'[ ]{2,}', ' ', rest)
            lines.append((lead + rest).rstrip())
        cleaned = '\n'.join(lines)
        # Collapse 3+ newlines to just 2
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _ocr_or_describe_image(self, img_bytes: bytes, project_id: int = 0, page_no: int = 1) -> str:
        """Deprecated wrapper kept for compatibility. Uses _ocr_png_bytes_to_text under the hood."""
        try:
            # If bytes are not PNG, best-effort convert using Pillow if available
            png_bytes = None
            try:
                from io import BytesIO
                from PIL import Image  # type: ignore
                with BytesIO(img_bytes) as bio:
                    im = Image.open(bio)
                    im = im.convert('RGB')
                    out = BytesIO()
                    im.save(out, format='PNG')
                    png_bytes = out.getvalue()
            except Exception:
                # Fallback: use the original bytes (the API can often handle generic image bytes)
                png_bytes = img_bytes
            text, _usage = self._ocr_png_bytes_to_text(png_bytes, project_id, page_no)
            return text or ''
        except Exception as e:
            return f"(Błąd OCR/vision: {e})"


# Celery task wrapper
if celery is not None:
    @celery.task(name='file_processor.process_file', queue='embeddings', routing_key='embeddings.process_file')
    def process_file_task(file_id, force_full_ocr=False):
        # Base task already provides app context via FlaskContextTask in celery_app
        try:
            current_app.logger.info(
                "File processor task start",
                extra={'event': 'file_processor_celery_start', 'file_id': file_id, 'force_full_ocr': force_full_ocr}
            )
        except Exception:
            pass
        fp = FileProcessor(app=current_app._get_current_object())
        fp._process_file(file_id, force_full_ocr=force_full_ocr)
        try:
            current_app.logger.info(
                "File processor task done",
                extra={'event': 'file_processor_celery_done', 'file_id': file_id, 'force_full_ocr': force_full_ocr}
            )
        except Exception:
            pass
