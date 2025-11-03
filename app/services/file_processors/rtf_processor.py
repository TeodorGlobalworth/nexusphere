def convert_rtf_to_markdown(file_path: str) -> str:
    """Convert RTF to plain text/markdown-ish by stripping RTF control words."""
    try:
        try:
            from striprtf.striprtf import rtf_to_text  # type: ignore
        except Exception as ie:
            print(f"RTF support not installed (striprtf). {ie}")
            return ''
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            rtf = f.read()
        text = rtf_to_text(rtf) or ''
        return text
    except Exception as e:
        print(f"Error reading RTF: {e}")
        return ''
