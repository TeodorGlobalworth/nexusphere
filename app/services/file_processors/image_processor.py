from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.file_processor import FileProcessor
    from app.models.knowledge_file import KnowledgeFile

def convert_image_to_markdown(fp: 'FileProcessor', knowledge_file: 'KnowledgeFile') -> str:
    try:
        with open(knowledge_file.file_path, 'rb') as f:
            img_bytes = f.read()
        # Use unified PNG-based OCR helper; treat single image as page 1
        # If caller FileProcessor has debug output configured, forward it so artifacts are saved
        debug = getattr(fp, 'debug_mode', False)
        out_dir = None
        # Some callers (like pdf processor) pass a debug_out path via temporary attribute; try to reuse it
        debug_out = getattr(fp, '_debug_out_path', None)
        if debug_out:
            out_dir = str(debug_out)
        text, _ = fp._ocr_png_bytes_to_text(img_bytes, knowledge_file.project_id, 1, debug=debug, out_dir=out_dir)
        return text or ''
    except Exception as e:
        print(f"Error reading image: {e}")
        return ''
