
def get_file_icon(file_type):
    """Return appropriate Material Design icon name for file type"""
    icons = {
        'pdf': 'picture_as_pdf',
        'doc': 'description',
        'docx': 'description',
        'odt': 'article',
        'txt': 'subject',
        'eml': 'email',
        'msg': 'email',
        'jpg': 'image',
        'jpeg': 'image',
        'png': 'image'
    }
    return icons.get(file_type.lower(), 'attach_file')

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    return f"{s} {size_names[i]}"

def get_status_class(status):
    """Return Materialize CSS color class for status"""
    classes = {
        'connected': 'green',
        'error': 'red',
        'processing': 'blue',
        'processed': 'green',
        'uploaded': 'light-blue',
        'disconnected': 'grey'
    }
    return classes.get(status, 'grey')

def format_tokens_k(value):
    """Format token count divided by 1000 WITHOUT thousands separators.

    Examples:
    - 2_000_000 -> "2000"
    - 12_345 -> "12"
    - 987_654 -> "988"
    If value is None or not numeric, returns the original value.
    """
    try:
        num = float(value)
    except Exception:
        return value
    try:
        scaled = int(round(num / 1000.0))
        return str(scaled)
    except Exception:
        return value
