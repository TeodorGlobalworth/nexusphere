import docx

def convert_docx_to_markdown(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paras)
    except Exception as e:
        print(f"Error reading DOCX: {str(e)}")
        return ''
