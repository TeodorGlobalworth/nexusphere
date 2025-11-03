import os
from typing import Iterable, Optional
from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable as bool."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'true', '1', 'on', 'yes'}


def _env_int(name: str, default: int) -> int:
    """Return an environment variable as int with safe fallback."""
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, '') else default


def _env_float(name: str, default: float) -> float:
    """Return an environment variable as float with safe fallback."""
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, '') else default


def _env_list(name: str, default: Iterable[str], sep: str = ',') -> list[str]:
    """Return a normalized list split by separator."""
    raw = os.environ.get(name)
    if raw is None:
        return [s.strip().lower() for s in default if s.strip()]
    return [item.strip().lower() for item in raw.split(sep) if item.strip()]


def _env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return a trimmed string or default when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


class Config:
    """Configuration for document search application."""
    
    # --- Application basics ---
    SECRET_KEY = _env_str('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # --- Database ---
    SQLALCHEMY_DATABASE_URI = _env_str('DATABASE_URL') or f"postgresql://doc_user:{_env_str('DB_PASSWORD', 'doc_pass')}@db:5432/doc_search_db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # --- AI API Keys ---
    OPENAI_API_KEY = _env_str('OPENAI_API_KEY')
    ZEROENTROPY_API_KEY = _env_str('ZEROENTROPY_API_KEY')
    ZEROENTROPY_MODEL = _env_str('ZEROENTROPY_MODEL', 'zerank-1')
    NOVITA_API_KEY = _env_str('NOVITA_API_KEY')
    NOVITA_RERANK_MODEL = _env_str('NOVITA_RERANK_MODEL', 'qwen/qwen3-reranker-8b')
    
    # --- Retrieval Configuration ---
    VECTOR_TOP_K_DEFAULT = _env_int('VECTOR_TOP_K_DEFAULT', 5)
    RERANK_TOP_K_DEFAULT = _env_int('RERANK_TOP_K_DEFAULT', 5)
    RERANK_THRESHOLD_DEFAULT = _env_float('RERANK_THRESHOLD_DEFAULT', 0.6)
    RERANK_PROVIDER = _env_str('RERANK_PROVIDER', 'zeroentropy')
    MULTI_QUERY_ENABLED = _env_bool('MULTI_QUERY_ENABLED', True)
    MULTIQUERY_MODEL_DEFAULT = _env_str('MULTIQUERY_MODEL_DEFAULT', 'gpt-4o-mini')
    HYBRID_RRF_K = _env_int('HYBRID_RRF_K', 60)
    PREFETCH_LIMIT = _env_int('PREFETCH_LIMIT', 20)
    COLBERT_CANDIDATES = _env_int('COLBERT_CANDIDATES', 15)
    RRF_DENSE_WEIGHT = _env_float('RRF_DENSE_WEIGHT', 0.6)
    RRF_SPARSE_WEIGHT = _env_float('RRF_SPARSE_WEIGHT', 0.3)
    RRF_COLBERT_WEIGHT = _env_float('RRF_COLBERT_WEIGHT', 0.1)
    INCLUDE_NEIGHBOR_CHUNKS_DEFAULT = _env_bool('INCLUDE_NEIGHBOR_CHUNKS_DEFAULT', True)

    # --- Qdrant Vector Database ---
    QDRANT_HOST = _env_str('QDRANT_HOST', 'qdrant')
    QDRANT_PORT = _env_int('QDRANT_PORT', 6333)
    QDRANT_ALLOW_INSECURE_FALLBACK = _env_bool('QDRANT_ALLOW_INSECURE_FALLBACK', False)
    QDRANT_DEBUG = _env_bool('QDRANT_DEBUG', False)
    
    # --- BGE-M3 Embeddings Service ---
    BGE_M3_BASE_URL = _env_str('BGE_M3_BASE_URL', 'http://bge_m3:8000')
    BGE_M3_TIMEOUT = _env_float('BGE_M3_TIMEOUT', 30.0)
    BGE_RETRY_ATTEMPTS = _env_int('BGE_RETRY_ATTEMPTS', 3)
    BGE_RETRY_BACKOFF_SECS = _env_float('BGE_RETRY_BACKOFF_SECS', 1.0)
    BGE_RETRY_JITTER_SECS = _env_float('BGE_RETRY_JITTER_SECS', 0.5)
    COLBERT_DIM = _env_int('COLBERT_DIM', 1024)
    
    # --- AI Models ---
    EMBEDDING_MODEL = _env_str('EMBEDDING_MODEL', 'text-embedding-3-large')
    EMBEDDING_DIM = _env_int('EMBEDDING_DIM', 1024)
    OPENAI_RESPONSES_MODEL = _env_str('OPENAI_RESPONSES_MODEL', 'gpt-4o-mini')
    VISION_MODEL = _env_str('VISION_MODEL', 'gpt-4o-mini')
    DEFAULT_RESPONSE_LANGUAGE = _env_str('DEFAULT_RESPONSE_LANGUAGE', 'en')

    # --- File Upload Configuration ---
    MAX_CONTENT_LENGTH = _env_int('MAX_CONTENT_LENGTH', 512 * 1024 * 1024)
    UPLOAD_FOLDER = _env_str('UPLOAD_FOLDER', '/app/data/uploads')
    ALLOWED_EXTENSIONS = set(_env_list('ALLOWED_EXTENSIONS', 
        {'xlsx', 'doc', 'docx', 'odt', 'pdf', 'txt', 'md', 'rtf', 'html', 'eml', 'msg'}))
    MAX_UPLOAD_FILE_MB = _env_int('MAX_UPLOAD_FILE_MB', 200)
    MAX_PDF_PAGES = _env_int('MAX_PDF_PAGES', 200)
    MAX_IMAGES_PER_FILE = _env_int('MAX_IMAGES_PER_FILE', 200)
    FILE_PROCESSING_LOG_RETENTION_DAYS = _env_int('FILE_PROCESSING_LOG_RETENTION_DAYS', 30)
    
    # --- Usage Limits ---
    MAX_TOKENS_PER_PROJECT = _env_int('MAX_TOKENS_PER_PROJECT', 2_000_000)
    MAX_OUTPUT_TOKENS = _env_int('MAX_OUTPUT_TOKENS', 20000)
    
    # --- Session / Cookie Security ---
    SESSION_COOKIE_SAMESITE = _env_str('SESSION_COOKIE_SAMESITE', 'Lax')
    REMEMBER_COOKIE_SAMESITE = _env_str('REMEMBER_COOKIE_SAMESITE', 'Lax')
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', False)
    REMEMBER_COOKIE_SECURE = _env_bool('REMEMBER_COOKIE_SECURE', False)
    PERMANENT_SESSION_LIFETIME = _env_int('PERMANENT_SESSION_LIFETIME', 60 * 60 * 8)
    
    # --- Security Headers ---
    SECURITY_CSP = _env_str('SECURITY_CSP')
    REFERRER_POLICY = _env_str('REFERRER_POLICY', 'strict-origin-when-cross-origin')
    PERMISSIONS_POLICY = _env_str('PERMISSIONS_POLICY', "geolocation=(), microphone=(), camera=()")
    ENABLE_HSTS = _env_bool('ENABLE_HSTS', False)
    HSTS_MAX_AGE = _env_int('HSTS_MAX_AGE', 15_552_000)
    HSTS_INCLUDE_SUBDOMAINS = _env_bool('HSTS_INCLUDE_SUBDOMAINS', True)
    HSTS_PRELOAD = _env_bool('HSTS_PRELOAD', False)
    SECURITY_CSP_STRICT = _env_bool('SECURITY_CSP_STRICT', False)
    CSP_UPGRADE_INSECURE_REQUESTS = _env_bool('CSP_UPGRADE_INSECURE_REQUESTS', True)
    
    # --- Rate Limiting ---
    RATELIMIT_STORAGE_URI = _env_str('RATELIMIT_STORAGE_URI', 'memory://')
    
    # --- WebSocket ---
    WS_MAX_CONNECTIONS_PER_USER = _env_int('WS_MAX_CONNECTIONS_PER_USER', 1)
    WS_IDLE_TIMEOUT = _env_int('WS_IDLE_TIMEOUT', 90)
    
    # --- ClamAV Antivirus ---
    CLAMAV_STARTUP_GRACE_SECS = _env_int('CLAMAV_STARTUP_GRACE_SECS', 180)
    CLAMAV_CONNECT_RETRIES = _env_int('CLAMAV_CONNECT_RETRIES', 5)
    CLAMAV_RETRY_DELAY_SECS = _env_float('CLAMAV_RETRY_DELAY_SECS', 2.0)


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _env_str('TEST_DATABASE_URL', 'sqlite:///:memory:')
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'check_same_thread': False},
        'poolclass': StaticPool,
    }
    WTF_CSRF_ENABLED = False
    SERVER_NAME = 'localhost'
    RATELIMIT_STORAGE_URI = 'memory://'
    
    # Speed up tests
    VECTOR_TOP_K_DEFAULT = 3
    RERANK_TOP_K_DEFAULT = 3
    RERANK_THRESHOLD_DEFAULT = 0.6
    RERANK_PROVIDER = 'none'
    MULTI_QUERY_ENABLED = _env_bool('TEST_MULTI_QUERY_ENABLED', False)
    MULTIQUERY_MODEL_DEFAULT = 'gpt-4o-mini'
    HYBRID_RRF_K = 30
