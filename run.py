
import os
import json
import secrets
import string
from app import create_app, db
from app.models.user import User
from app.models.project import Project, ProjectUser
from app.models.knowledge_file import KnowledgeFile

app = create_app()


def _split_sql_statements(sql_text: str):
    """Split SQL script into statements, respecting dollar-quoted blocks, string
    literals and comments so DO $$...$$ blocks aren't broken by naive ';' splits.
    This is a lightweight parser sufficient for our init scripts.
    """
    stmts = []
    s = sql_text
    length = len(s)
    i = 0
    start = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    while i < length:
        ch = s[i]
        # handle line comment
        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
            i += 1
            continue

        # handle block comment
        if in_block_comment:
            if ch == '*' and i + 1 < length and s[i+1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # handle single-quoted string
        if in_single:
            if ch == "'":
                # handle escaped '' sequence
                if i + 1 < length and s[i+1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        # handle double-quoted identifier
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue

        # Not inside string/comment: detect comment starts
        if ch == '-' and i + 1 < length and s[i+1] == '-':
            in_line_comment = True
            i += 2
            continue
        if ch == '/' and i + 1 < length and s[i+1] == '*':
            in_block_comment = True
            i += 2
            continue

        # detect single or double quotes
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue

        # detect dollar-quote start like $$ or $tag$
        if ch == '$':
            # find end of tag
            j = i + 1
            while j < length and s[j] != '$' and s[j] not in ('\n', '\t', ' '):
                j += 1
            if j < length and s[j] == '$':
                tag = s[i:j+1]
                # find closing tag
                k = s.find(tag, j+1)
                if k == -1:
                    # unterminated; consume rest and break
                    i = length
                    break
                else:
                    i = k + len(tag)
                    continue

        # statement terminator
        if ch == ';':
            stmt = s[start:i].strip()
            if stmt:
                stmts.append(stmt)
            start = i + 1
        i += 1

    # remaining tail
    tail = s[start:].strip()
    if tail:
        stmts.append(tail)
    return stmts

@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Project': Project,
        'ProjectUser': ProjectUser,
        'KnowledgeFile': KnowledgeFile
    }

if __name__ == '__main__':
    from sqlalchemy import text, inspect
    from flask import current_app
    from app.utils.validators import normalize_subdomain, is_valid_subdomain
    from app.utils.tenant import is_reserved_subdomain

    def _gen_password(length=20):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_-+="
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    with app.app_context():
        print('[init] Starting one-time initialization...')
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        # Best-effort: create tables if init SQL didn't run
        if not {'user', 'project'}.issubset(set(existing_tables)):
            try:
                db.create_all()
                print('[init] Created tables from SQLAlchemy models')
            except Exception:
                current_app.logger.exception('[init] Error creating tables from models')

        # Best-effort: apply init SQL (idempotent) if present
        sql_path = os.path.join(os.path.dirname(__file__), 'scripts', 'init_db.sql')
        if os.path.exists(sql_path):
            try:
                with open(sql_path, 'r', encoding='utf-8') as fh:
                    sql = fh.read()
                with db.engine.connect() as conn:
                    try:
                        stmts = _split_sql_statements(sql)
                        for stmt in stmts:
                            if not stmt or stmt.strip().startswith('--'):
                                continue
                            try:
                                with conn.begin():
                                    conn.execute(text(stmt))
                            except Exception as e:
                                msg = str(e).lower()
                                if 'already exists' in msg or 'duplicate' in msg or 'duplicateobject' in msg or 'in failed sql transaction' in msg:
                                    continue
                                current_app.logger.info('[init] SQL skipped due to error: %s', stmt[:120])
                        print('[init] Applied scripts/init_db.sql (idempotent)')
                    except Exception:
                        current_app.logger.exception('[init] Failed while applying init SQL script')
            except Exception:
                current_app.logger.exception('[init] Unable to read or apply scripts/init_db.sql')

        # Ensure RLS session allows bootstrap user creation
        try:
            db.session.execute(text("SELECT set_config('app.is_superadmin','1', true)"))
            db.session.execute(text("SELECT set_config('app.org_id','', true)"))
        except Exception:
            pass

        # --- Lightweight schema upgrades (idempotent) ---
        # Email monitoring tables removed (processed_email, email_account - feature deleted)

        # Ensure generation_history table exists (for activity UI)
        try:
            db.session.execute(text(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                    project_name VARCHAR(120) NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
                    source_kind VARCHAR(20) NOT NULL,
                    source_address VARCHAR(255),
                    source_user VARCHAR(255),
                    total_tokens INTEGER DEFAULT 0,
                    title VARCHAR(255) NOT NULL,
                    response_body TEXT NOT NULL,
                    chunk_refs JSON,
                    spam_flag BOOLEAN,
                    importance_level VARCHAR(32)
                );
                """
            ))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_generation_history_project ON generation_history(project_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_generation_history_created ON generation_history(created_at)"))
            db.session.commit()
            print('[init] Ensured generation_history table exists')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('[init] generation_history ensure failed (non-fatal)')

        # Upgrade password_hash column length if still at 128 (older schema)
        try:
            current_len = db.session.execute(text(
                """
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='user' AND column_name='password_hash'
                """
            )).scalar()
            if current_len is not None and current_len < 255:
                db.session.execute(text('ALTER TABLE public."user" ALTER COLUMN password_hash TYPE VARCHAR(255);'))
                db.session.commit()
                print('[init] Upgraded password_hash column length to 255')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('[init] password_hash column upgrade failed (non-fatal)')

        # Create superadmin user if not exists (from ENV if provided)
        try:
            admin = User.query.filter_by(is_superadmin=True).first()
            if not admin:
                email = os.environ.get('SUPERADMIN_EMAIL', 'admin@company.com')
                username = os.environ.get('SUPERADMIN_USERNAME', 'admin')
                password = os.environ.get('SUPERADMIN_PASSWORD') or _gen_password()
                admin = User(
                    username=username,
                    email=email,
                    is_admin=True,
                    is_superadmin=True
                )
                admin.set_password(password)
                db.session.add(admin)
                db.session.commit()
                print('[init] Superadmin created:')
                print(f'       username: {username}')
                print(f'       email   : {email}')
                print(f'       password: {password}')
                print('       IMPORTANT: Change this password immediately in user profile.')
            else:
                print('[init] Superadmin already exists — skipping creation')
        except Exception:
            current_app.logger.exception('[init] Failed to create or verify superadmin user')

        # Seed global context prompts from JSON (idempotent)
        # Context prompts initialization removed (feature deleted)
        # prompts_path = os.path.join(os.path.dirname(__file__), 'app', 'data', 'context_prompts.json')
        # if os.path.isfile(prompts_path):
        #     ...

        # Organization slug assignment removed (feature deleted)
        # try:
        #     def _allocate_org_slug(name_hint: str | None, *, used: set[str]) -> str:
        #         ...
        #     orgs = Organization.query.order_by(Organization.id.asc()).all()
        #     ...

        print('[init] Done. This script does not start the web server.')
        print('[init] The application should be served by Gunicorn (wsgi:app).')
