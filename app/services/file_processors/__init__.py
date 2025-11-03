"""Per-file type processors for knowledge ingestion.

Each module implements a convert_to_markdown(fp: FileProcessor, knowledge_file|file_path, ...)->str function
or a class with a to_markdown method. The main FileProcessor delegates based on extension.
"""

from .pdf_processor import convert_pdf_to_markdown  # noqa: F401
from .docx_processor import convert_docx_to_markdown  # noqa: F401
from .odt_processor import convert_odt_to_markdown  # noqa: F401
from .rtf_processor import convert_rtf_to_markdown  # noqa: F401
from .email_processor import convert_eml_to_markdown, convert_msg_to_markdown  # noqa: F401
from .html_processor import convert_html_to_markdown  # noqa: F401
from .image_processor import convert_image_to_markdown  # noqa: F401
from .text_processor import extract_text_from_txt  # noqa: F401
from .xlsx_processor import convert_xlsx_to_markdown_and_embed  # noqa: F401
