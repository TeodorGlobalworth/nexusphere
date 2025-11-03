from bs4 import BeautifulSoup

def convert_html_to_markdown(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        soup = BeautifulSoup(html, 'html.parser')
        return soup.get_text('\n')
    except Exception as e:
        print(f"Error reading HTML: {e}")
        return ''
