import zipfile, re

def convert_odt_to_markdown(file_path: str) -> str:
    try:
        with zipfile.ZipFile(file_path) as odt_file:
            content_xml = odt_file.read('content.xml').decode('utf-8', errors='ignore')
            text = re.sub(r'<[^>]+>', ' ', content_xml)
            return text
    except Exception as e:
        print(f"Error reading ODT: {str(e)}")
        return ''
