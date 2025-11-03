
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file, abort, g
from flask_login import login_required, current_user
from app.models.project import Project, ProjectUser
from app.models.user import User
from app.models.knowledge_file import KnowledgeFile
from app.models.file_processing_log import FileProcessingLog
from app.services.file_processor import FileProcessor
from app import db, limiter
from werkzeug.utils import secure_filename
import os
from datetime import datetime, timedelta
from itsdangerous import TimestampSigner
from sqlalchemy import or_, text
from app.models.generation_history import GenerationHistory
from app.services.generation_history import extract_chunk_filenames
import sqlalchemy.exc as sa_exc

bp = Blueprint('project', __name__)

DEFAULT_HISTORY_DAYS = 30


def _parse_iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _default_history_window():
    end = datetime.utcnow()
    start = end - timedelta(days=DEFAULT_HISTORY_DAYS)
    return start, end


def _ensure_project_on_host(project):
    # Multi-tenancy removed - no organization checks
    pass

@bp.route('/<project_public_id>')
@login_required
def dashboard(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id

    # Check if user has access to this project
    if not any(pu.project_id == project_id for pu in current_user.projects):
        flash("Brak dostępu do tego projektu.", 'error')
        return redirect(url_for('main.dashboard'))

    knowledge_files = KnowledgeFile.query.filter_by(project_id=project_id).order_by(KnowledgeFile.uploaded_at.desc()).all()
    # Recent activity: rely on durable GenerationHistory entries
    recent_activity = []
    try:
        mail_logs = (
            GenerationHistory.query.filter_by(project_id=project_id)
            .order_by(GenerationHistory.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        mail_logs = []
    # Normalize into a common shape (no KnowledgeFile merge to prevent double entries)
    items = []
    for entry in mail_logs:
        refs = entry.chunk_reference_list()
        filenames = extract_chunk_filenames(refs)
        if len(filenames) > 3:
            filenames_display = ', '.join(filenames[:3]) + '…'
        else:
            filenames_display = ', '.join(filenames)
        items.append({
            'kind': 'ai',
            'ts': entry.created_at,
            'created_at': entry.created_at,
            'title': entry.title,
            'source_kind': entry.source_kind,
            'source_address': entry.source_address,
            'filenames': filenames_display,
            'total_tokens': entry.total_tokens
        })
    # Attach a lightweight proxy object for template compatibility (dot access)
    class _Obj(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__
    recent_activity = [_Obj(**it) for it in items]

    return render_template('project/dashboard.html', 
                         project=project, 
                         knowledge_files=knowledge_files,
                         recent_activity=recent_activity)


@bp.route('/<project_public_id>/history')
@login_required
def project_history(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)

    if not any(pu.project_id == project.id for pu in current_user.projects):
        flash("Brak dostępu do tego projektu.", 'error')
        return redirect(url_for('main.dashboard'))

    default_from, default_to = _default_history_window()
    source_choices = [
        {'value': '', 'label': "Wszystkie źródła"},
        {'value': 'ui', 'label': "Generator UI"},
        {'value': 'mailbox', 'label': "Skrzynka pocztowa"},
        {'value': 'api', 'label': "API"},
    ]

    return render_template(
        'project/history.html',
        project=project,
        default_from_date=default_from.date().isoformat(),
        default_to_date=default_to.date().isoformat(),
        default_from_iso=default_from.isoformat(),
        default_to_iso=default_to.isoformat(),
        source_choices=source_choices,
    )


def _require_project_history_access(project):
    if not any(pu.project_id == project.id for pu in current_user.projects) and not getattr(current_user, 'is_superadmin', False):
        return False
    return True


def _history_source_label(kind):
    labels = {
        'ui': "Generator UI",
        'mailbox': "Skrzynka pocztowa",
        'api': "API",
    }
    return labels.get(kind, kind or '')


@bp.route('/<project_public_id>/history/data')
@login_required
def project_history_data(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)

    if not _require_project_history_access(project):
        return jsonify({'error': "Brak dostępu."}), 403

    page = max(1, request.args.get('page', 1, type=int))
    per_page = request.args.get('per_page', 25, type=int)
    per_page = max(1, min(per_page, 100))

    query = GenerationHistory.query.filter_by(project_id=project.id)

    date_from = _parse_iso_dt(request.args.get('from'))
    date_to = _parse_iso_dt(request.args.get('to'))
    if not date_from and not date_to:
        default_from, _ = _default_history_window()
        date_from = default_from
    if date_from:
        query = query.filter(GenerationHistory.created_at >= date_from)
    if date_to:
        query = query.filter(GenerationHistory.created_at <= date_to)

    source_kind = request.args.get('source_kind') or request.args.get('source')
    if source_kind in {'ui', 'mailbox', 'api'}:
        query = query.filter(GenerationHistory.source_kind == source_kind)

    spam_filter = request.args.get('spam')
    if spam_filter == 'true':
        query = query.filter(GenerationHistory.spam_flag.is_(True))
    elif spam_filter == 'false':
        query = query.filter(GenerationHistory.spam_flag.is_(False))

    importance_level = request.args.get('importance')
    if importance_level:
        query = query.filter(GenerationHistory.importance_level == importance_level)

    search_term = (request.args.get('search') or '').strip()
    if search_term:
        pattern = f"%{search_term}%"
        query = query.filter(
            or_(
                GenerationHistory.title.ilike(pattern),
                GenerationHistory.response_body.ilike(pattern),
                GenerationHistory.source_address.ilike(pattern),
                GenerationHistory.source_user.ilike(pattern),
            )
        )

    query = query.order_by(GenerationHistory.created_at.desc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    items = []
    for entry in pagination.items:
        chunk_refs = entry.chunk_reference_list()
        filenames = extract_chunk_filenames(chunk_refs)
        snippet = (entry.response_body or '')[:240]
        items.append({
            'id': entry.id,
            'created_at': entry.created_at.isoformat() if entry.created_at else None,
            'created_at_human': entry.created_at.strftime('%d.%m.%Y %H:%M') if entry.created_at else '',
            'title': entry.title,
            'source_kind': entry.source_kind,
            'source_label': _history_source_label(entry.source_kind),
            'source_address': entry.source_address,
            'source_user': entry.source_user,
            'total_tokens': entry.total_tokens or 0,
            'snippet': snippet,
            'chunk_count': len(chunk_refs),
            'filenames': filenames,
            'spam_flag': entry.spam_flag,
            'importance_level': entry.importance_level,
        })

    return jsonify({
        'items': items,
        'page': pagination.page,
        'pages': pagination.pages,
        'per_page': pagination.per_page,
        'total': pagination.total,
        'default_from': (date_from.isoformat() if date_from else None),
        'default_to': (date_to.isoformat() if date_to else None),
    })


@bp.route('/<project_public_id>/history/<int:entry_id>')
@login_required
def project_history_entry(project_public_id, entry_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)

    if not _require_project_history_access(project):
        return jsonify({'error': "Brak dostępu."}), 403

    entry = GenerationHistory.query.filter_by(project_id=project.id, id=entry_id).first_or_404()
    chunk_refs = entry.chunk_reference_list()

    return jsonify({
        'id': entry.id,
        'project_id': entry.project_id,
        'created_at': entry.created_at.isoformat() if entry.created_at else None,
        'created_at_human': entry.created_at.strftime('%d.%m.%Y %H:%M') if entry.created_at else '',
        'title': entry.title,
        'response_body': entry.response_body,
        'source_kind': entry.source_kind,
        'source_label': _history_source_label(entry.source_kind),
        'source_address': entry.source_address,
        'source_user': entry.source_user,
        'total_tokens': entry.total_tokens or 0,
        'chunk_refs': chunk_refs,
        'filenames': extract_chunk_filenames(chunk_refs),
        'spam_flag': entry.spam_flag,
        'importance_level': entry.importance_level,
    })


@bp.route('/<project_public_id>/history/<int:entry_id>/chunks/<int:ref_index>')
@login_required
def project_history_chunk_preview(project_public_id, entry_id, ref_index):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)

    if not _require_project_history_access(project):
        return jsonify({'error': "Brak dostępu."}), 403

    entry = GenerationHistory.query.filter_by(project_id=project.id, id=entry_id).first_or_404()
    chunk_refs = entry.chunk_reference_list()

    if ref_index < 0 or ref_index >= len(chunk_refs):
        return jsonify({'error': "Nie znaleziono fragmentu."}), 404

    reference = chunk_refs[ref_index]
    chunk_payload = None
    try:
        from app.services.vector_service import VectorService

        vector_service = VectorService()
        chunk_payload = vector_service.fetch_chunk_by_reference(project.id, reference)
    except Exception:
        chunk_payload = None

    if not chunk_payload:
        return jsonify({'error': "Nie udało się pobrać fragmentu. Spróbuj ponownie później.", 'reference': reference}), 502

    safe_payload = {
        'point_id': chunk_payload.get('id') or reference.get('point_id'),
        'file_id': chunk_payload.get('file_id') or reference.get('file_id'),
        'file_name': chunk_payload.get('file_name') or reference.get('file_name'),
        'chunk_id': chunk_payload.get('chunk_id') or reference.get('chunk_id'),
        'content': chunk_payload.get('content') or '',
        'vector_score': chunk_payload.get('score') or reference.get('vector_score'),
        'rerank_score': chunk_payload.get('rerank_score') or reference.get('rerank_score'),
        'matched_query': chunk_payload.get('matched_query') or reference.get('matched_query'),
    }

    return jsonify({'chunk': safe_payload})

@bp.route('/<project_public_id>/knowledge')
@login_required
def knowledge_base(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id

    if not any(pu.project_id == project_id for pu in current_user.projects):
        flash("Brak dostępu do tego projektu.", 'error')
        return redirect(url_for('main.dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    query = KnowledgeFile.query.filter_by(project_id=project_id).order_by(KnowledgeFile.uploaded_at.desc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    knowledge_files = pagination.items

    # Optional: preload recent error logs (compressed) for modal (IDs only; fetched async if desired)

    # List of processed files used by the template
    processed_files = [f for f in knowledge_files if f.status == 'processed']

    # Pass allowed extensions to template for display
    allowed_ext = sorted(list(current_app.config.get('ALLOWED_EXTENSIONS', [])))
    return render_template('project/knowledge_base.html', project=project, knowledge_files=knowledge_files, processed_files=processed_files, allowed_extensions=allowed_ext, pagination=pagination)


@bp.route('/<project_public_id>/knowledge/download/<int:file_id>')
@login_required
def download_knowledge_file(project_public_id, file_id):
    """Serve a knowledge file for download, with permission checks."""
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id

    if not any(pu.project_id == project_id for pu in current_user.projects):
        flash("Brak dostępu do tego projektu.", 'error')
        return redirect(url_for('main.dashboard'))

    knowledge_file = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first_or_404()

    # Ensure file exists on disk
    if not knowledge_file.file_path or not os.path.exists(knowledge_file.file_path):
        flash("Plik nie został znaleziony na serwerze.", 'error')
        return redirect(url_for('project.knowledge_base', project_public_id=project_public_id))

    # Prevent serving files outside of configured UPLOAD_FOLDER (defend against path traversal / tampering)
    try:
        upload_root = os.path.realpath(current_app.config.get('UPLOAD_FOLDER', ''))
        file_real = os.path.realpath(knowledge_file.file_path)
        # Require that commonpath equals the upload root to prevent prefix tricks
        if os.path.commonpath([upload_root, file_real]) != upload_root:
            flash("Nieautoryzowany plik.", 'error')
            return redirect(url_for('project.knowledge_base', project_public_id=project_public_id))

        # Send the file with its original filename
        return send_file(file_real, as_attachment=True, download_name=knowledge_file.original_filename)
    except Exception:
        abort(500)

@bp.route('/<project_public_id>/knowledge/upload', methods=['POST'])
@limiter.limit("20/minute; 200/hour")
@login_required
def upload_knowledge_file(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id

    def _is_ajax():
        return request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json

    def _respond(payload, status=200):
        # If AJAX, return JSON; otherwise flash+redirect to knowledge base
        if _is_ajax():
            return jsonify(payload), status
        if 'error' in payload:
            flash(payload.get('error'), 'error')
        else:
            flash(payload.get('message', ''), 'success')
        return redirect(url_for('project.knowledge_base', project_public_id=project_public_id))

    if not any(pu.project_id == project_id for pu in current_user.projects):
        return _respond({'error': "Brak dostępu do tego projektu."}, 403)

    if 'file' not in request.files:
        return _respond({'error': "Nie wybrano pliku."}, 400)

    replace_flag = request.form.get('replace', 'false').lower() == 'true'
    force_full_ocr_flag = request.form.get('force_full_ocr', 'false').lower() == 'true'

    file = request.files['file']
    if file.filename == '':
        return _respond({'error': "Nie wybrano pliku."}, 400)

    # Hardened upload policy
    if file and allowed_file(file.filename):
        # Enforce server-side per-file limit (e.g., 200 MB)
        max_file_mb = int(current_app.config.get('MAX_UPLOAD_FILE_MB', 200))
        filename = secure_filename(file.filename)
        # Security: Ensure project_id and user_id are safe integers (prevent path injection)
        safe_project_id = int(project_id)
        safe_user_id = int(current_user.id)
        unique_filename = f"{safe_project_id}_{safe_user_id}_{filename}"

        # Create project upload directory (use safe integer values)
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(safe_project_id))
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.join(upload_dir, unique_filename)

        # Save to a temp path first and verify size & mime
        tmp_path = file_path + ".part"
        file.save(tmp_path)

        try:
            # Validate size
            file_size = os.path.getsize(tmp_path)
            if file_size > max_file_mb * 1024 * 1024:
                os.remove(tmp_path)
                return _respond({'error': f'Plik przekracza limit {max_file_mb} MB.'}, 400)

            # ClamAV scan (fail-close if scan service unreachable when enabled)
            av_enabled = current_app.config.get('CLAMAV_ENABLED', True)
            if av_enabled:
                try:
                    if file_size <= (current_app.config.get('CLAMAV_MAX_MB', 100) * 1024 * 1024):
                        # Use internal lightweight client (no deprecated pkg_resources usage)
                        from app.utils.clamav_client import ClamAVClient, ClamAVConnectionError
                        retries = int(current_app.config.get('CLAMAV_CONNECT_RETRIES', current_app.config.get('CLAMAV_CONNECT_RETRIES', 3)))
                        retry_delay = float(current_app.config.get('CLAMAV_RETRY_DELAY_SECS', current_app.config.get('CLAMAV_RETRY_DELAY_SECS', 2.0)))
                        cd = ClamAVClient(
                            host=current_app.config.get('CLAMAV_HOST', 'clamav'),
                            port=int(current_app.config.get('CLAMAV_PORT', 3310)),
                            timeout=int(current_app.config.get('CLAMAV_TIMEOUT_SECS', 25)),
                            retries=retries,
                            retry_delay=retry_delay
                        )
                        metrics = current_app.config.get('_METRICS')
                        if metrics is not None:
                            metrics['av_scan_total'] = metrics.get('av_scan_total',0) + 1
                        with open(tmp_path, 'rb') as fh:
                            scan_res = cd.instream(fh)
                        status = None
                        signature = None
                        if isinstance(scan_res, dict):
                            status_tuple = scan_res.get('stream')
                            if isinstance(status_tuple, (list, tuple)) and status_tuple:
                                status = status_tuple[0]
                                if len(status_tuple) > 1:
                                    signature = status_tuple[1]
                        current_app.logger.info(f"AV scan result filename={filename} status={status} signature={signature}")
                        if status == 'FOUND':
                            if metrics is not None:
                                metrics['av_scan_found_total'] = metrics.get('av_scan_found_total',0) + 1
                            os.remove(tmp_path)
                            pass  # audit removed
                            return _respond({'error': "Plik został odrzucony (skan antywirusowy)."}, 400)
                        if status == 'ERROR' or status is None:
                            # Treat as scanner failure (fail-close) – preserve behavior from previous implementation
                            raise Exception(f"clamav-scan-error:{signature or 'unknown'}")
                    else:
                        # Over AV size threshold
                        if current_app.config.get('_METRICS') is not None:
                            current_app.config['_METRICS']['av_scan_skipped_too_large_total'] = current_app.config['_METRICS'].get('av_scan_skipped_too_large_total',0) + 1
                        pass  # audit removed
                except Exception as av_e:
                    # Fail-close with optional startup grace
                    grace_secs = int(current_app.config.get('CLAMAV_STARTUP_GRACE_SECS', 120))
                    # Use a cached startup timestamp in app config
                    start_ts = current_app.config.setdefault('_APP_START_TS', int(__import__("time").time()))
                    now_ts = int(__import__("time").time())
                    within_grace = (now_ts - start_ts) < grace_secs
                    meta = {'error': str(av_e)[:200], 'within_grace': within_grace}
                    if within_grace:
                        current_app.logger.warning(f"AV unavailable but within grace ({now_ts-start_ts}s<{grace_secs}s): {av_e}")
                        pass  # audit removed
                        if current_app.config.get('_METRICS') is not None:
                            current_app.config['_METRICS']['av_scan_grace_skip_total'] = current_app.config['_METRICS'].get('av_scan_grace_skip_total',0) + 1
                        # Continue WITHOUT deleting temp (allow file) – will proceed to hashing + store
                    else:
                        os.remove(tmp_path)
                        pass  # audit removed
                        if current_app.config.get('_METRICS') is not None:
                            current_app.config['_METRICS']['av_scan_error_total'] = current_app.config['_METRICS'].get('av_scan_error_total',0) + 1
                        return _respond({'error': "Skan antywirusowy nie powiódł się – spróbuj ponownie później."}, 503)

            # Compute hash from disk
            import hashlib
            h = hashlib.sha256()
            with open(tmp_path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                    h.update(chunk)
            file_hash = h.hexdigest()

            # Optional content sniffing
            try:
                import magic
                mime = magic.from_file(tmp_path, mime=True)
                ext = filename.rsplit('.', 1)[1].lower()
                mime_map = {
                    'pdf': 'application/pdf',
                    'txt': 'text',
                    'doc': 'application',
                    'docx': 'application',
                    'odt': 'application',
                    'eml': 'message',
                    'msg': 'application',
                    'jpg': 'image',
                    'jpeg': 'image',
                    'png': 'image',
                    'xlsx': 'application'
                }
                expected = mime_map.get(ext)
                if expected and not mime.startswith(expected):
                    os.remove(tmp_path)
                    return _respond({'error': "Typ pliku nie zgadza się z treścią (mime mismatch)."}, 400)
            except Exception:
                pass

            # Check for duplicates
            existing = KnowledgeFile.query.filter_by(project_id=project_id, file_hash=file_hash).first()
            if not existing:
                existing = KnowledgeFile.query.filter_by(project_id=project_id, original_filename=filename).first()

            if existing and not replace_flag:
                os.remove(tmp_path)
                pass  # audit removed
                return _respond({'error': 'conflict', 'message': "Plik już istnieje", 'existing_id': existing.id, 'existing_filename': existing.original_filename, 'file_hash': file_hash}, 409)

            # Move file atomically into place
            os.replace(tmp_path, file_path)

            if existing and replace_flag:
                # Full replacement: delete old chunk vectors and decrement fragment usage BEFORE reprocessing
                old_chunks = existing.chunks_count or 0
                if old_chunks > 0:
                    try:
                        from app.services.vector_service import VectorService
                        vs = VectorService()
                        deleted = vs.delete_file_chunks(project_id, existing.id, old_chunks)
                        # Log the purge
                        current_app.logger.info(f"Project {project.id}: Deleted {deleted}/{old_chunks} fragments before reprocessing file {existing.id}")
                        try:
                            db.session.add(FileProcessingLog(knowledge_file_id=existing.id, project_id=project_id, event='replace_purge', message=f'Usunięto {deleted}/{old_chunks} starych fragmentów przed ponownym przetwarzaniem'))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                    except Exception as ve:
                        current_app.logger.warning(f"Vector purge during replace failed: {ve}")
                # Reset existing file metadata for reprocessing
                existing.status = 'uploaded'
                existing.error_message = None
                existing.chunks_count = 0
                existing.processed_at = None
                existing.filename = unique_filename
                existing.original_filename = filename
                existing.file_size = file_size
                existing.file_type = filename.rsplit('.', 1)[1].lower()
                existing.file_path = file_path
                existing.file_hash = file_hash
                # No longer persist full_ocr on the record (ephemeral flag handled during processing)
                knowledge_file = existing
                db.session.add(knowledge_file)
            else:
                # New record
                knowledge_file = KnowledgeFile(
                    project_id=project_id,
                    filename=unique_filename,
                    original_filename=filename,
                    file_size=file_size,
                    file_type=filename.rsplit('.', 1)[1].lower(),
                    file_path=file_path,
                    file_hash=file_hash,
                    uploaded_by=current_user.id
                )
                db.session.add(knowledge_file)

            db.session.commit()

            # Background processing (will be shifted to Celery later if available)
            from flask import current_app as _ca
            file_processor = FileProcessor(app=_ca._get_current_object())
            file_processor.process_file_async(knowledge_file.id, force_full_ocr=force_full_ocr_flag)

            pass  # audit removed
            return _respond({
                'message': "Plik został przesłany pomyślnie.",
                'file_id': knowledge_file.id,
                'filename': filename,
                'size': knowledge_file.get_size_formatted()
            })
        finally:
            # Cleanup temp if still exists
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    return _respond({'error': "Nieprawidłowy typ pliku."}, 400)


@bp.route('/<project_public_id>/knowledge/template.xlsx', methods=['GET'])
@login_required
def download_xlsx_template(project_public_id):
    """Zwraca statyczny plik szablonu XLSX z katalogu aplikacji zamiast generować go dynamicznie.

    Aby podmienić szablon:
      1. Umieść przygotowany plik w: app/static/knowledge_import_template.xlsx (domyślnie)
         LUB ustaw zmienną środowiskową KNOWLEDGE_XLSX_TEMPLATE_PATH wskazującą pełną ścieżkę.
      2. Użytkownicy pobiorą go z tej trasy bez regeneracji.
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    if not any(pu.project_id == project.id for pu in current_user.projects):
        flash("Brak dostępu do tego projektu.", 'error')
        return redirect(url_for('main.dashboard'))

    # Resolve template path: env override or default under static
    env_path = os.environ.get('KNOWLEDGE_XLSX_TEMPLATE_PATH')
    if env_path:
        template_path = env_path
    else:
        template_path = os.path.join(current_app.root_path, 'static', 'NexuSphere_import_template.xlsx')

    if not os.path.exists(template_path):
        current_app.logger.warning('Knowledge XLSX template not found at %s', template_path)
        flash("Szablon XLSX nie jest dostępny (brak pliku). Skontaktuj się z administratorem.", 'error')
        return redirect(url_for('project.knowledge_base', project_public_id=project_public_id))

    try:
        return send_file(
            template_path,
            as_attachment=True,
            download_name='knowledge_import_template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception:
        current_app.logger.exception('Failed to send knowledge XLSX template: %s', template_path)
        abort(500)


@bp.route('/<project_public_id>/knowledge/check_duplicates', methods=['POST'])
@login_required
def check_knowledge_duplicates(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403

    # Expect JSON array of objects: { filename, file_hash }
    payload = request.get_json() or {}
    files = payload.get('files', [])
    conflicts = []
    for f in files:
        fname = f.get('filename')
        fhash = f.get('file_hash')
        existing = None
        if fhash:
            existing = KnowledgeFile.query.filter_by(project_id=project_id, file_hash=fhash).first()
        if not existing and fname:
            existing = KnowledgeFile.query.filter_by(project_id=project_id, original_filename=fname).first()
        if existing:
            conflicts.append({'filename': fname, 'existing_id': existing.id, 'existing_filename': existing.original_filename})

    return jsonify({'conflicts': conflicts})

@bp.route('/<project_public_id>/knowledge/status', methods=['GET'])
@login_required
def knowledge_files_status(project_public_id):
    """Long-polling endpoint returning current statuses of knowledge files.
    Optional query param: since (ISO8601) - if provided, can be used in future for delta filtering (currently ignored for simplicity).
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu."}), 403
    files = KnowledgeFile.query.filter_by(project_id=project_id).order_by(KnowledgeFile.uploaded_at.desc()).all()
    data = []
    for f in files:
        data.append({
            'id': f.id,
            'filename': f.original_filename,
            'status': f.status,
            'error_message': f.error_message,
            'chunks_count': f.chunks_count,
            'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
            'processed_at': f.processed_at.isoformat() if f.processed_at else None
        })
    return jsonify({'files': data, 'ts': datetime.utcnow().isoformat() + 'Z'})

@bp.route('/<project_public_id>/knowledge/ws_token', methods=['GET'])
@login_required
def knowledge_ws_token(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu."}), 403
    signer = TimestampSigner(current_app.config['SECRET_KEY'])
    token = signer.sign(f"{current_user.id}:{project_id}").decode()
    return jsonify({'token': token})

@bp.route('/<project_public_id>/knowledge/<int:file_id>/retry', methods=['POST'])
@limiter.limit("30/minute; 300/hour")
@login_required
def retry_knowledge_file(project_public_id, file_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403
    knowledge_file = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first()
    if not knowledge_file:
        pass  # audit removed
        return jsonify({'error': "Plik nie istnieje."}), 404
    # Reset status
    knowledge_file.status = 'uploaded'
    knowledge_file.error_message = None
    db.session.add(knowledge_file)
    # Log retry
    try:
        log = FileProcessingLog(knowledge_file_id=knowledge_file.id, project_id=project_id, event='retry', message='User requested retry')
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Re-dispatch async processing
    from flask import current_app as _ca
    fp = FileProcessor(app=_ca._get_current_object())
    fp.process_file_async(knowledge_file.id)
    pass  # audit removed
    return jsonify({'message': "Ponowne przetwarzanie uruchomione."})

@bp.route('/<project_public_id>/knowledge/<int:file_id>', methods=['DELETE', 'POST'])
@limiter.limit("60/hour")
@login_required
def delete_knowledge_file(project_public_id, file_id):
    """Delete a knowledge file record and the underlying file from disk.

    Accepts both DELETE and POST (for CSRF protection).
    Returns JSON so it can be called via AJAX from the UI.
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id

    # Permission check
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403

    knowledge_file = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first()
    if not knowledge_file:
        pass  # audit removed
        return jsonify({'error': "Plik nie istnieje."}), 404

    # Attempt to remove file from disk if present and inside upload folder
    try:
        if knowledge_file.file_path and os.path.exists(knowledge_file.file_path):
            upload_root = os.path.realpath(current_app.config.get('UPLOAD_FOLDER', ''))
            # Resolve symlinks to prevent path traversal attacks
            file_real = os.path.realpath(knowledge_file.file_path)
            # Security check: ensure resolved path is still within upload folder
            try:
                # Use os.path.commonpath to verify file is under upload_root
                common = os.path.commonpath([upload_root, file_real])
                if common == upload_root:
                    try:
                        os.remove(file_real)
                    except Exception:
                        # If file cannot be removed, continue to remove DB record but log
                        current_app.logger.exception("Nie udało się usunąć pliku z dysku: %s", file_real)
                else:
                    current_app.logger.warning(f"Security: Attempt to delete file outside upload folder: {file_real}")
            except ValueError:
                # commonpath raises ValueError if paths are on different drives (Windows)
                current_app.logger.warning(f"Security: File path on different drive: {file_real}")

        # Remove vectors for this file from Qdrant (best-effort)
        try:
            if knowledge_file.chunks_count and (knowledge_file.chunks_count > 0):
                from app.services.vector_service import VectorService
                vs = VectorService()
                try:
                    vs.delete_file_chunks(project.id, knowledge_file.id, knowledge_file.chunks_count)
                except Exception as ve:
                    current_app.logger.warning(f"Vector delete failed for file {knowledge_file.id}: {ve}")
        except Exception:
            pass

        # Log the deletion
        current_app.logger.info(f"Project {project.id}: Deleting file {knowledge_file.id} with {knowledge_file.chunks_count} fragments")

        # Remove DB record
        db.session.delete(knowledge_file)
        db.session.commit()
        pass  # audit removed
        return jsonify({'status': 'ok'})
    except Exception as e:
        current_app.logger.exception("Błąd podczas usuwania pliku: %s", e)
        pass  # audit removed[:200]})
        db.session.rollback()
        return jsonify({'error': "Wystąpił błąd podczas usuwania pliku."}), 500

@bp.route('/<project_public_id>/knowledge/<int:file_id>/logs', methods=['GET'])
@login_required
def get_knowledge_file_logs(project_public_id, file_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403
    knowledge_file = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first()
    if not knowledge_file:
        return jsonify({'error': "Plik nie istnieje."}), 404
    # Retention purge (lazy): delete logs older than retention days
    try:
        retention_days = int(current_app.config.get('FILE_PROCESSING_LOG_RETENTION_DAYS', 30))
        if retention_days > 0:
            from sqlalchemy import text as _sql_text
            db.session.execute(
                _sql_text("DELETE FROM file_processing_log WHERE project_id = :pid AND created_at < (now() - (:rd || ' days')::interval)"),
                {"pid": project_id, "rd": retention_days}
            )
            db.session.commit()
    except Exception:
        db.session.rollback()
    # Pagination params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    if per_page > 100:
        per_page = 100
    base_q = FileProcessingLog.query.filter_by(project_id=project_id, knowledge_file_id=file_id)
    total = base_q.count()
    logs = base_q.order_by(FileProcessingLog.created_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    return jsonify({
        'logs': [l.to_dict() for l in logs],
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': (total // per_page) + (1 if total % per_page else 0)
    })



def allowed_file(filename):
    return (
        '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']
    )
@bp.route('/<project_public_id>/knowledge/<int:file_id>/force_full_ocr', methods=['POST'])
@limiter.limit("20/minute; 100/hour")
@login_required
def force_full_ocr(project_public_id, file_id):
    """Trigger a one-time full OCR processing run for a PDF file (ephemeral, non-persistent)."""
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403
    kf = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first()
    if not kf:
        pass  # audit removed
        return jsonify({'error': "Plik nie istnieje."}), 404
    if (kf.file_type or '').lower() != 'pdf':
        pass  # audit removed
        return jsonify({'error': "Pełny OCR jest dostępny tylko dla plików PDF."}), 400
    # Reset status for reprocessing (do not set any persistent flag)
    kf.status = 'uploaded'
    kf.error_message = None
    kf.chunks_count = 0
    kf.processed_at = None
    db.session.add(kf)
    try:
        db.session.add(FileProcessingLog(knowledge_file_id=kf.id, project_id=project_id, event='retry', message='Force Full OCR (ephemeral)'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    from flask import current_app as _ca
    fp = FileProcessor(app=_ca._get_current_object())
    fp.process_file_async(kf.id, force_full_ocr=True)
    pass  # audit removed
    return jsonify({'message': "Pełny OCR (jednorazowy) uruchomiony.", 'file_id': kf.id})
@bp.route('/<project_public_id>/knowledge/<int:file_id>/preview', methods=['GET'])
@limiter.limit("60/minute; 600/hour")
@login_required
def preview_knowledge_file(project_public_id, file_id):
    """Return the full textual representation of a processed knowledge file (no truncation).

    Previous implementation limited preview to 20k characters. Requirement changed to show the
    entire converted content ("bez limitu"). This endpoint now returns the full text. Be aware
    that extremely large documents may impact response size and browser rendering performance.
    Future enhancements could introduce streaming, paging, or an optional size cap toggle.
    OCR-heavy paths remain disabled for performance (no image OCR during preview).
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu."}), 403
    kf = KnowledgeFile.query.filter_by(id=file_id, project_id=project_id).first()
    if not kf:
        return jsonify({'error': "Plik nie istnieje."}), 404
    if kf.status != 'processed':
        return jsonify({'error': "Plik nie jest jeszcze przetworzony."}), 409
    # For simple text-like types, just read a slice directly
    ext = (kf.file_type or '').lower()
    preview_text = ''
    # 1. Prefer persisted sidecar markdown if present (written at processing time)
    try:
        if kf.file_path and os.path.exists(kf.file_path + '.md'):
            with open(kf.file_path + '.md', 'r', encoding='utf-8', errors='ignore') as fsc:
                preview_text = fsc.read()
    except Exception:
        preview_text = ''
    try:
        # 2. Direct file read fallback (for inherently textual originals) if sidecar absent
        if (not preview_text) and ext in ('txt','md','html','htm','eml') and os.path.exists(kf.file_path):
            try:
                with open(kf.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    preview_text = f.read()  # full file
            except Exception:
                pass
        # 3. As last resort re-run conversion (may be slower, attempts to disable OCR).
        if not preview_text:
            from app.services.file_processor import FileProcessor
            fp = FileProcessor(app=current_app._get_current_object())
            orig_max_images = fp.MAX_OCR_IMAGES
            fp.MAX_OCR_IMAGES = 0  # disable OCR for speed
            try:
                converted = fp._convert_file_to_markdown(kf) or ''
            finally:
                fp.MAX_OCR_IMAGES = orig_max_images
            preview_text = converted
    except Exception as e:
        return jsonify({'error': f'Błąd generowania podglądu: {str(e)}'}), 500
    return jsonify({
        'file_id': kf.id,
        'filename': kf.original_filename,
        'content': preview_text,
        'truncated': False
    })

@bp.route('/<project_public_id>/config', methods=['GET', 'POST'])
@login_required
def project_config(project_public_id):
    """Admin panel for adjusting project configuration: response_style, description, packages.
    - Admins may increase packages.
    - Only superadmin may decrease packages.
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    _ensure_project_on_host(project)
    project_id = project.id
    # Ensure membership and admin rights
    membership = next((pu for pu in current_user.projects if pu.project_id == project_id), None)
    if not membership or membership.role != 'admin':
        flash("Brak uprawnień administracyjnych do projektu.", 'error')
        return redirect(url_for('project.dashboard', project_public_id=project_public_id))

    contexts = []

    if request.method == 'POST':
        new_style = request.form.get('response_style') or project.response_style
        if new_style not in ('formal','standard','casual'):
            new_style = 'standard'
        project.response_style = new_style
        project.description = request.form.get('description') or project.description
        # Context prompts removed
        project.context = None

        db.session.add(project)
        try:
            db.session.commit()
            flash("Konfiguracja projektu zaktualizowana.", 'success')
        except Exception:
            db.session.rollback()
            flash("Błąd zapisu konfiguracji.", 'error')
        return redirect(url_for('project.project_config', project_public_id=project_public_id))


    # Package change logs removed
    logs = []
    user_map = {}
    return render_template('project/config.html', project=project, logs=logs, user_map=user_map, contexts=contexts)



