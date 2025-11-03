
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from app.models.project import Project
from app.models.knowledge_file import KnowledgeFile
from app.services.ai_service import AIService
from app import db
from app.models.generation_history import GenerationHistory
from app.models.user import User
from app.services.generation_history import build_chunk_refs, persist_history_entry

bp = Blueprint('api', __name__)

@bp.before_request
def _api_auth_guard():
    """Ensure API returns JSON 401 instead of HTML redirect when not authenticated."""
    try:
        if not getattr(current_user, 'is_authenticated', False):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    except Exception:
        return jsonify({'success': False, 'error': 'Authentication error'}), 401

@bp.route('/projects/<project_public_id>/status')
@login_required
def project_status(project_public_id):
    """Get project status - simplified without email accounts."""
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    project_id = project.id

    if not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': 'No access to this project.'}), 403

    knowledge_files = KnowledgeFile.query.filter_by(project_id=project_id).all()
    processed_files = sum(1 for file in knowledge_files if file.status == 'processed')

    monthly_used = 0
    try:
        monthly_used = project.get_monthly_tokens_used()
    except Exception:
        pass

    return jsonify({
        'project': {
            'id': project.id,
            'public_id': project.public_id,
            'name': project.name,
            'tokens_used_monthly': monthly_used
        },
        'knowledge_files': {
            'total': len(knowledge_files),
            'processed': processed_files,
            'processing': sum(1 for file in knowledge_files if file.status == 'processing')
        }
    })


@bp.route('/projects/<project_public_id>/generate-response', methods=['POST'])
@login_required
def generate_response(project_public_id):
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    project_id = project.id

    if not getattr(current_user, 'is_superadmin', False) and not any(pu.project_id == project_id for pu in current_user.projects):
        return jsonify({'error': "Brak dostępu do tego projektu."}), 403

    payload = request.get_json(silent=True) or {}
    email_content = payload.get('email_content')
    temperature = payload.get('temperature', 'default')
    mode = (payload.get('mode') or 'full').strip().lower()

    if mode not in {'full', 'search_only'}:
        return jsonify({'error': "Nieprawidłowy tryb pipeline."}), 400

    if not email_content:
        return jsonify({'error': "Treść emaila jest wymagana."}), 400

    # Block generation if knowledge base is empty (no processed files)
    try:
        from app.models.knowledge_file import KnowledgeFile as _KF
        kb_count = _KF.query.filter_by(project_id=project_id, status='processed').count()
        if kb_count == 0:
            return jsonify({'success': False, 'error': "Brak przetworzonych dokumentów w bazie wiedzy dla tego projektu."}), 400
    except Exception:
        # If check fails, fail-safe and block to avoid blind generations
        return jsonify({'success': False, 'error': "Baza wiedzy niedostępna. Spróbuj ponownie później."}), 400

    options = payload.get('options') or {}

    def _to_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {'1', 'true', 'yes', 'on'}:
                return True
            if lowered in {'0', 'false', 'no', 'off'}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return None

    include_neighbor_chunks_raw = options.get('include_neighbor_chunks')
    include_neighbor_chunks = _to_bool(include_neighbor_chunks_raw)

    def _clean_str(value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
        else:
            value = str(value).strip()
        return value or None

    def _to_int(value, *, minimum=None, maximum=None):
        if value is None or value == '':
            return None
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return None
        if minimum is not None and ivalue < minimum:
            return None
        if maximum is not None and ivalue > maximum:
            return None
        return ivalue

    def _to_float(value):
        if value is None or value == '':
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    email_subject = _clean_str(payload.get('email_subject'))
    response_model = _clean_str(options.get('response_model'))
    multiquery_mode = _clean_str(options.get('multiquery_mode'))
    if multiquery_mode in {'inherit', 'default'}:
        multiquery_mode = None
    multiquery_model = _clean_str(options.get('multiquery_model'))
    multiquery_variants = _to_int(options.get('multiquery_variants'), minimum=1, maximum=12)
    multiquery_aggregate_top_k = _to_int(options.get('multiquery_aggregate_top_k'), minimum=1, maximum=100)
    vector_top_k = _to_int(options.get('vector_top_k'), minimum=1, maximum=50)
    context_limit = _to_int(options.get('context_limit'), minimum=1, maximum=50)
    retrieval_threshold = _to_float(options.get('retrieval_threshold'))
    rerank_provider = _clean_str(options.get('rerank_provider'))
    rerank_model = _clean_str(options.get('rerank_model'))
    rerank_top_k = _to_int(options.get('rerank_top_k'), minimum=1, maximum=50)
    rerank_threshold = _to_float(options.get('rerank_threshold'))
    prefetch_limit = _to_int(options.get('prefetch_limit'), minimum=1, maximum=400)
    colbert_candidates = _to_int(options.get('colbert_candidates'), minimum=1, maximum=400)
    rrf_k = _to_int(options.get('rrf_k'), minimum=1, maximum=1000)

    rrf_weights_raw = options.get('rrf_weights')
    rrf_weights = None
    if isinstance(rrf_weights_raw, dict):
        rrf_weights = rrf_weights_raw
    elif isinstance(rrf_weights_raw, str) and rrf_weights_raw.strip():
        try:
            import json as _json

            parsed_weights = _json.loads(rrf_weights_raw)
            if isinstance(parsed_weights, dict):
                rrf_weights = parsed_weights
        except Exception:
            rrf_weights = None

    # Legacy fallbacks
    hybrid_per_vector_limit = _to_int(options.get('hybrid_per_vector_limit'), minimum=1, maximum=400)
    hybrid_rrf_k = _to_int(options.get('hybrid_rrf_k'), minimum=1, maximum=1000)

    if prefetch_limit is None:
        prefetch_limit = hybrid_per_vector_limit
    if rrf_k is None:
        rrf_k = hybrid_rrf_k

    if prefetch_limit and vector_top_k and prefetch_limit < vector_top_k:
        prefetch_limit = vector_top_k

    # Keep legacy values in sync for downstream compatibility layers
    hybrid_per_vector_limit = prefetch_limit
    hybrid_rrf_k = rrf_k

    def _sum_usage_tokens(breakdown):
        total = 0
        if isinstance(breakdown, dict):
            for entry in breakdown.values():
                if isinstance(entry, dict):
                    try:
                        total += int(entry.get('total_tokens') or entry.get('total') or 0)
                    except Exception:
                        continue
        return total

    def _resolve_filenames(ids):
        if not ids:
            return []
        try:
            return [row.original_filename for row in _KF.query.filter(_KF.id.in_(ids)).all()]
        except Exception:
            return []

    # Build prompt bundle (shared with AIService)
    ai_service = AIService()
    default_context_limit = current_app.config.get('EMAIL_CONTEXT_DOCS_LIMIT') or current_app.config.get('VECTOR_CONTEXT_LIMIT') or 6
    try:
        default_context_limit = int(default_context_limit)
    except Exception:
        default_context_limit = 6
    max_context_docs = context_limit or default_context_limit
    if max_context_docs < 1:
        max_context_docs = 1

    prompt_bundle = ai_service.build_email_prompt(
        project_id=project_id,
        email_content=email_content,
        style_hint=temperature,
        max_context_docs=max_context_docs,
        vector_top_k=vector_top_k,
        multi_query_mode=multiquery_mode,
        multi_query_model=multiquery_model,
        multi_query_variants=multiquery_variants,
        multi_query_aggregate_top_k=multiquery_aggregate_top_k,
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
        include_neighbor_chunks=include_neighbor_chunks,
    )


    if mode == 'search_only':
        result = ai_service.run_search_only(project_id=project_id, prompt_bundle=prompt_bundle)
        file_ids = prompt_bundle.get('context_file_ids') or []
        filenames = _resolve_filenames(file_ids)
        try:
            current_month_usage = project.get_monthly_tokens_used()
        except Exception:
            current_month_usage = 0
        result.update({
            'tokens_used': _sum_usage_tokens(result.get('token_usage_breakdown')),
            'monthly_tokens_used': current_month_usage,
            'context_filenames': filenames,
        })
        return jsonify(result)

    # For 'full' mode, add all diagnostic fields for parity with 'search_only'

    system_prompt = prompt_bundle['system_prompt']
    user_prompt = prompt_bundle['user_prompt']

    try:
        est_prompt_tokens = ai_service.count_tokens(system_prompt) + ai_service.count_tokens(user_prompt)
    except Exception:
        est_prompt_tokens = ai_service.count_tokens(email_content)
    # Determine an upper bound for completion tokens (default 2000, or lower if configured)
    default_max_completion = 2000
    try:
        cfg_max = int(current_app.config.get('MAX_COMPLETION_TOKENS', default_max_completion))
        max_completion_tokens = max(1, min(cfg_max, default_max_completion))
    except Exception:
        max_completion_tokens = default_max_completion
    # Current month usage
    try:
        current_month_usage = project.get_monthly_tokens_used()
    except Exception:
        current_month_usage = 0

    # Call OpenAI
    response = ai_service.generate_response(
        project_id=project_id,
        email_content=email_content,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        prompt_bundle=prompt_bundle,
        response_model=response_model,
    )

    if response['success']:
        tokens_in = response.get('tokens_input', 0)
        tokens_out = response.get('tokens_output', 0)

        file_ids = prompt_bundle.get('context_file_ids') or []
        filenames = _resolve_filenames(file_ids)
        context_docs_data = prompt_bundle.get('context_docs') or []
        chunk_refs = build_chunk_refs(context_docs_data)
        # Normalize response_json to an object for clients and compute a definitive plain body
        rj = response.get('response_json')
        if isinstance(rj, str):
            try:
                import json as _json
                rj = _json.loads(rj)
            except Exception:
                rj = None
        plain = ''
        # Prefer structured body
        if isinstance(rj, dict):
            try:
                plain = ((rj.get('email') or {}).get('body') or '')
            except Exception:
                plain = ''
        # If still empty and response looks like JSON, parse and extract
        if (not plain) and isinstance(response.get('response'), str):
            raw = response.get('response').strip()
            if raw.startswith('{'):
                try:
                    import json as _json
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        plain = ((parsed.get('email') or {}).get('body') or '')
                except Exception:
                    pass
            # Fallback to raw text if not JSON
            if not plain:
                plain = raw

        response_body = plain or (response.get('response') or '')
        history_title = email_subject or 'generated from UI'
        source_user_label = getattr(current_user, 'username', None) or getattr(current_user, 'full_name', None) or current_user.email
        total_tokens = tokens_in + tokens_out
        try:
            persist_history_entry(
                project=project,
                source_kind='ui',
                source_address=current_user.email,
                source_user=source_user_label,
                title=history_title,
                response_body=response_body,
                total_tokens=total_tokens,
                chunk_refs=chunk_refs,
            )
        except Exception:
            db.session.rollback()

        token_breakdown = response.get('token_usage_breakdown') or prompt_bundle.get('token_usage_breakdown') or {}
        # Add all diagnostic fields for parity with search_only
        payload = {
            'success': True,
            'response': plain,
            'response_json': rj,
            'tokens_used': total_tokens,
            'mode': 'full',
            'token_usage_breakdown': token_breakdown,
            'token_usage_breakdown_total': _sum_usage_tokens(token_breakdown),
            'context_docs': prompt_bundle.get('context_docs') or [],
            'context_file_ids': file_ids,
            'context_filenames': filenames,
            'multi_query_used': prompt_bundle.get('multi_query_used'),
            'multi_query_variants': prompt_bundle.get('multi_query_variants'),
            'multi_query_usage': prompt_bundle.get('multi_query_usage'),
            'multi_query_aggregate_limit': prompt_bundle.get('multi_query_aggregate_limit'),
            'multi_query_mode': prompt_bundle.get('multi_query_mode'),
            'multi_query_model': prompt_bundle.get('multi_query_model'),
            'multi_query_variant_count': prompt_bundle.get('multi_query_variant_count'),
            'rerank_usage': prompt_bundle.get('rerank_usage'),
            'rerank_provider': prompt_bundle.get('rerank_provider'),
            'rerank_model': prompt_bundle.get('rerank_model'),
            'rerank_top_k': prompt_bundle.get('rerank_top_k'),
            'rerank_threshold': prompt_bundle.get('rerank_threshold'),
            'rerank_settings': prompt_bundle.get('rerank_settings'),
            'context_limit': prompt_bundle.get('context_limit'),
            'prefetch_limit': prompt_bundle.get('prefetch_limit'),
            'colbert_candidates': prompt_bundle.get('colbert_candidates'),
            'rrf_k': prompt_bundle.get('rrf_k'),
            'rrf_weights': prompt_bundle.get('rrf_weights'),
            'hybrid_per_vector_limit': prompt_bundle.get('hybrid_per_vector_limit'),
            'hybrid_rrf_k': prompt_bundle.get('hybrid_rrf_k'),
            'response_model': prompt_bundle.get('response_model'),
            'vector_debug_logs': prompt_bundle.get('vector_debug_logs'),
            'search_steps': prompt_bundle.get('search_steps'),
            'knowledge_entries': prompt_bundle.get('knowledge_entries'),
            'example_texts': prompt_bundle.get('example_texts'),
            'detection_confidence': prompt_bundle.get('detection_confidence'),
        }
        return jsonify(payload)
    else:
        return jsonify({
            'success': False,
            'error': response.get('error', 'Error generating response.')
        })


@bp.route('/admin/users')
@login_required
def admin_users_all():
    """Return all users with basic fields and project list - simplified without organizations."""
    # Only admins/superadmins can enumerate users
    if not (getattr(current_user, 'is_admin', False) or getattr(current_user, 'is_superadmin', False)):
        return jsonify({'users': []})

    from app.models.user import User
    from app.models.project import ProjectUser, Project
    from datetime import datetime, timedelta, timezone
    
    users = User.query.all()

    # Preload project assignments in one pass
    user_ids = [u.id for u in users]
    proj_map = {}
    if user_ids:
        links = ProjectUser.query.filter(ProjectUser.user_id.in_(user_ids)).all()
        proj_ids = list({ln.project_id for ln in links})
        projects = {p.id: p for p in Project.query.filter(Project.id.in_(proj_ids)).all()}
        for ln in links:
            proj = projects.get(ln.project_id)
            if not proj:
                continue
            arr = proj_map.setdefault(ln.user_id, [])
            arr.append({'id': proj.id, 'public_id': proj.public_id, 'name': proj.name})

    # Basic online status heuristic: consider online if last_login within past 10 minutes
    now = datetime.now(timezone.utc)
    
    def _to_utc(dt):
        if dt is None:
            return None
        try:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    
    out = []
    for u in users:
        last = _to_utc(u.last_login)
        out.append({
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'is_admin': bool(u.is_admin),
            'is_superadmin': bool(u.is_superadmin),
            'is_self': u.id == getattr(current_user, 'id', 0),
            'last_login_iso': last.isoformat() if last else None,
            'last_login_human': last.strftime('%d.%m.%Y %H:%M') if last else None,
            'is_online': bool(last and (now - last) <= timedelta(minutes=10)),
            'projects': proj_map.get(u.id, [])
        })
    
    return jsonify({'users': out})


@bp.route('/projects/<project_public_id>/config', methods=['GET', 'PUT'])
@login_required
def project_config_api(project_public_id):
    """Lightweight API to read/update selected project config fields (sample emails and description/style/packages read-only here).

    PUT accepts JSON with optional keys: sample_email_1/2/3. Each up to 5000 chars.
    Only project admins can update; members can GET.
    """
    project = Project.query.filter_by(public_id=project_public_id).first_or_404()
    project_id = project.id
    # Determine membership and role
    membership = next((pu for pu in current_user.projects if pu.project_id == project_id), None)
    is_admin = bool(membership and getattr(membership, 'role', 'user') == 'admin') or bool(getattr(current_user, 'is_superadmin', False))
    if request.method == 'GET':
        if not membership and not getattr(current_user, 'is_superadmin', False):
            return jsonify({'error': "Brak dostępu do tego projektu."}), 403
        return jsonify({
            'public_id': project.public_id,
            'name': project.name,
            'context': project.context,
            'response_style': project.response_style
        })
    # PUT - reserved for future config updates
    if not is_admin:
        return jsonify({'error': "Brak uprawnień administracyjnych do projektu."}), 403
    return jsonify({'status': 'ok'})
