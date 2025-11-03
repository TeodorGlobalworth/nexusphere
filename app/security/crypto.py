import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

class EncryptionError(Exception):
    pass

def _load_key() -> bytes:
    key = os.environ.get('ENCRYPTION_KEY')
    if not key:
        # Allow weak mode only when explicitly enabled (dev convenience)
        if os.environ.get('DEV_ALLOW_WEAK', 'false').lower() in ('1','true','on'):
            # Generate ephemeral key for this process (won't decrypt previous data)
            return Fernet.generate_key()
        raise RuntimeError('ENCRYPTION_KEY environment variable is required')
    # Accept raw 32-byte base64 or generate if 'GENERATE'
    if key == 'GENERATE':
        generated = Fernet.generate_key()
        raise RuntimeError(f"Generate and set ENCRYPTION_KEY (example): {generated.decode()}")
    try:
        return key.encode('utf-8')
    except Exception as e:
        raise RuntimeError('Invalid ENCRYPTION_KEY') from e

@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    return Fernet(_load_key())

def encrypt(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None:
        return None
    if plaintext == '':
        return ''
    token = _cipher().encrypt(plaintext.encode('utf-8'))
    return token.decode('utf-8')

def decrypt(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    if token == '':
        return ''
    try:
        pt = _cipher().decrypt(token.encode('utf-8'), ttl=None)
        return pt.decode('utf-8')
    except InvalidToken:
        raise EncryptionError('Decryption failed (InvalidToken)')
    except Exception as e:
        raise EncryptionError(f'Decryption failed: {e}')
