from datetime import datetime
import json
import threading
from urllib.parse import parse_qs
try:
    from flask_sock import Sock
except Exception:  # If dependency missing, noop register
    Sock = None

from flask import request, current_app
from app.models.knowledge_file import KnowledgeFile
from app.models.project import Project
from flask_login import current_user
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

_sock = None
_connections_lock = threading.Lock()
# project_id -> list of connection dicts: {ws, user_id, last_state: {file_id: status_hash}, last_pong: datetime}
_project_connections = {}

# user_id -> list of connection records (subset references from _project_connections) for global/user limiting
_user_connections = {}


def _signer():
    return TimestampSigner(current_app.config['SECRET_KEY'])


def _serialize_file(f):
    return {
        'id': f.id,
        'filename': f.original_filename,
        'status': f.status,
        'error_message': f.error_message,
        'chunks_count': f.chunks_count,
        'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
        'processed_at': f.processed_at.isoformat() if f.processed_at else None
    }


def register_ws(app):
    global _sock
    if Sock is None:
        return
    _sock = Sock(app)

    @_sock.route('/ws/knowledge/<project_public_id>')
    def knowledge_status(ws, project_public_id):  # type: ignore
        # --- Auth token verification (query param token) ---
        try:
            raw_qs = request.query_string.decode() if request.query_string else ''
            qs = parse_qs(raw_qs)
            token = qs.get('token', [None])[0]
            if not token:
                print('[WS] Missing token, closing')
                ws.close()  # type: ignore
                return
            try:
                data = _signer().unsign(token, max_age=3600).decode()
                user_id_str, proj_id_str = data.split(':', 1)
                user_id = int(user_id_str)
                proj_id = int(proj_id_str)
            except (BadSignature, SignatureExpired, ValueError):
                print('[WS] Bad/expired token, closing')
                ws.close()  # type: ignore
                return
            if not getattr(current_user, 'is_authenticated', False) or current_user.id != user_id:
                print('[WS] current_user mismatch or unauthenticated, closing')
                ws.close()  # type: ignore
                return
        except Exception as e:
            print(f'[WS] Exception during auth pre-check: {e}')
            try:
                ws.close()  # type: ignore
            finally:
                return

        project = Project.query.filter_by(public_id=project_public_id).first()
        if not project or project.id != proj_id or not any(pu.project_id == project.id for pu in getattr(current_user, 'projects', [])):
            print('[WS] Project auth failed, closing')
            try:
                ws.close()  # type: ignore
            finally:
                return

        # Prepare initial snapshot
        files = KnowledgeFile.query.filter_by(project_id=project.id).order_by(KnowledgeFile.uploaded_at.desc()).limit(200).all()
        snapshot = [_serialize_file(f) for f in files]
        try:
            print(f'[WS] Accepted connection for project {project.id}; sending snapshot ({len(snapshot)} files)')
            ws.send(json.dumps({'type': 'knowledge_snapshot', 'ts': datetime.utcnow().isoformat() + 'Z', 'files': snapshot}))  # type: ignore
        except Exception as e:
            print(f'[WS] Failed sending snapshot: {e}; closing')
            try:
                ws.close()  # type: ignore
            finally:
                return

        # Enforce per-user connection limits unless superadmin
        from flask import current_app as _ca
        is_super = getattr(current_user, 'is_superadmin', False)
        max_per_user = int(getattr(_ca.config, 'WS_MAX_CONNECTIONS_PER_USER', 1) or 0)
        if not is_super and max_per_user > 0:
            with _connections_lock:
                existing = [c for c in _user_connections.get(current_user.id, []) if getattr(c.get('ws'), 'connected', False)]
                if len(existing) >= max_per_user:
                    # Strategy: close the oldest existing and allow new one (so refresh works)
                    oldest = sorted(existing, key=lambda c: c.get('connected_at'))[0]
                    try:
                        oldest.get('ws').close()  # type: ignore
                    except Exception:
                        pass
                    # Clean after closing
                    for lst in _project_connections.values():
                        for rec in list(lst):
                            if rec is oldest:
                                lst.remove(rec)
                    _user_connections[current_user.id] = [c for c in existing if c is not oldest]
        conn_record = {
            'ws': ws,
            'project_id': project.id,
            'user_id': current_user.id,
            'last_state': {f['id']: _file_state_hash(f) for f in snapshot},
            'last_pong': datetime.utcnow(),
            'connected_at': datetime.utcnow()
        }
        with _connections_lock:
            _project_connections.setdefault(project.id, []).append(conn_record)
            _user_connections.setdefault(current_user.id, []).append(conn_record)

        # Keep the socket open; wait for client pings; terminate on error/close
        while True:
            try:
                if not ws.connected:  # type: ignore
                    break
                # Receive non-blocking small control messages (pings)
                import time
                # Try a short receive with timeout using underlying simple-websocket (ws.receive blocks). Use polling period.
                # We'll emulate heartbeat by expecting client JSON {"type": "ping"}
                try:
                    ws.sock.settimeout(0.1)  # type: ignore
                except Exception:
                    pass
                msg = None
                try:
                    msg = ws.receive(timeout=0.1)  # type: ignore
                except TypeError:
                    # Older flask-sock/simple-websocket versions don't support timeout kw; fallback to non-blocking pattern
                    try:
                        msg = ws.receive()  # type: ignore
                    except Exception:
                        msg = None
                except Exception:
                    msg = None
                if msg:
                    try:
                        data = json.loads(msg)
                        if isinstance(data, dict) and data.get('type') == 'ping':
                            conn_record['last_pong'] = datetime.utcnow()
                            try:
                                ws.send(json.dumps({'type': 'pong', 'ts': datetime.utcnow().isoformat() + 'Z'}))  # type: ignore
                            except Exception:
                                pass
                    except Exception:
                        pass
                # Idle timeout check
                idle_timeout = int(getattr(_ca.config, 'WS_IDLE_TIMEOUT', 90) or 0)
                if idle_timeout > 0:
                    if (datetime.utcnow() - conn_record['last_pong']).total_seconds() > idle_timeout:
                        try:
                            ws.close()  # type: ignore
                        finally:
                            break
                time.sleep(5)
            except Exception:
                break
        # Cleanup
        print(f'[WS] Connection closing for project {project.id}')
        with _connections_lock:
            lst = _project_connections.get(project.id, [])
            _project_connections[project.id] = [c for c in lst if c is not conn_record]
            # Remove from user connections
            ulist = _user_connections.get(conn_record.get('user_id'), [])
            _user_connections[conn_record.get('user_id')] = [c for c in ulist if c is not conn_record]


def _file_state_hash(fdict):
    # Minimal hash for comparing changes (status + error + chunks)
    return f"{fdict['status']}|{fdict.get('error_message') or ''}|{fdict.get('chunks_count') or 0}"


def broadcast_knowledge_update(project_id, file_ids=None):
    """Send delta updates for specified files (or all) to connected clients of a project.
    Safe to call best-effort from background threads.
    """
    if Sock is None:
        return
    with current_app.app_context():
        q = KnowledgeFile.query.filter_by(project_id=project_id)
        if file_ids:
            if not isinstance(file_ids, (list, tuple, set)):
                file_ids = [file_ids]
            q = q.filter(KnowledgeFile.id.in_(file_ids))
        changed_files = [_serialize_file(f) for f in q.all()]
    if not changed_files:
        return
    with _connections_lock:
        conns = list(_project_connections.get(project_id, []))
    if not conns:
        return
    for conn in conns:
        ws = conn.get('ws')
        if not getattr(ws, 'connected', False):  # type: ignore
            continue
        deltas = []
        last_state = conn.get('last_state', {})
        for f in changed_files:
            h = _file_state_hash(f)
            fid = f['id']
            if last_state.get(fid) != h:  # changed
                deltas.append(f)
                last_state[fid] = h
        if not deltas:
            continue
        payload = {'type': 'knowledge_delta', 'ts': datetime.utcnow().isoformat() + 'Z', 'files': deltas}
        try:
            ws.send(json.dumps(payload))  # type: ignore
        except Exception:
            # Mark connection as stale; cleanup will happen in its loop or future broadcast
            continue

def broadcast_project_usage(project_id, fragments_used=None, tokens_used=None):
    """Broadcast project usage metrics (fragments/tokens) to all project WS clients.
    Note: Limits are no longer enforced in NexuSphere, only usage tracking remains.
    """
    if Sock is None:
        return
    with _connections_lock:
        conns = list(_project_connections.get(project_id, []))
    if not conns:
        return
    payload = {
        'type': 'project_usage',
        'ts': datetime.utcnow().isoformat() + 'Z',
        'project_id': project_id,
        'fragments_used': fragments_used,
        'tokens_used': tokens_used
    }
    for conn in conns:
        ws = conn.get('ws')
        if not getattr(ws, 'connected', False):  # type: ignore
            continue
        try:
            ws.send(json.dumps(payload))  # type: ignore
        except Exception:
            continue
