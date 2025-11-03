from typing import TYPE_CHECKING
import pdfplumber
import fitz
import tempfile
import os
from pathlib import Path
import json

if TYPE_CHECKING:
    from app.services.file_processor import FileProcessor
    from app.models.knowledge_file import KnowledgeFile


def classify_pdf_pages(doc, fp: 'FileProcessor'):
    """Return list of dicts per page: {index, image_cover, text_len, kind}
    kind in {'native_text','ocr_scan_with_text_overlay','scanned_image_only','uncertain'}
    Uses thresholds from the FileProcessor instance.
    """
    results = []
    for idx, page in enumerate(doc):
        try:
            text = page.get_text("text") or ""
        except Exception:
            text = ""
        text_len = len(text.strip())
        # Sum image coverage
        cover = 0.0
        try:
            page_rect = page.rect
            page_area = float(page_rect.width * page_rect.height) if page_rect else 1.0
            for img in page.get_images(full=True) or []:
                try:
                    xref = img[0]
                    bbox = page.get_image_bbox(xref)
                    if bbox:
                        area = float(bbox.width * bbox.height)
                        cover += max(0.0, min(area / page_area, 1.0))
                except Exception:
                    continue
            cover = min(cover, 1.0)
        except Exception:
            cover = 0.0
        kind = 'uncertain'
        try:
            if cover <= fp.IMAGE_COVER_THRESHOLD and text_len >= fp.MIN_TEXT_CHARS_NATIVE:
                kind = 'native_text'
            elif cover > fp.IMAGE_COVER_THRESHOLD and text_len >= fp.MIN_TEXT_CHARS_NATIVE:
                kind = 'ocr_scan_with_text_overlay'
            elif cover > fp.IMAGE_COVER_THRESHOLD and text_len < fp.MIN_TEXT_CHARS_NATIVE:
                kind = 'scanned_image_only'
            else:
                kind = 'uncertain'
        except Exception:
            kind = 'uncertain'
        results.append({
            'index': idx,
            'image_cover': cover,
            'text_len': text_len,
            'kind': kind
        })
    return results


def markdown_table(rows, max_rows=200) -> str:
    """Render a table (list of rows) into GitHub-flavored Markdown. Best-effort.
    - Uses first non-empty row as header; falls back to generic headers.
    - Limits rows to max_rows to avoid bloat.
    """
    try:
        if not rows:
            return ''
        # Normalize rows to strings and ensure consistent column count
        norm = []
        max_cols = 0
        for r in rows[: max_rows]:
            rr = [(str(c).strip() if c is not None else '') for c in r]
            max_cols = max(max_cols, len(rr))
            norm.append(rr)
        if max_cols == 0:
            return ''
        # Pad rows to max_cols
        for r in norm:
            if len(r) < max_cols:
                r.extend([''] * (max_cols - len(r)))
        # Find header: first non-empty row
        header = None
        for r in norm:
            if any(cell for cell in r):
                header = r
                break
        if header is None:
            header = [f"Col {i+1}" for i in range(max_cols)]
        # Build Markdown
        lines = []
        lines.append('| ' + ' | '.join(header) + ' |')
        lines.append('|' + '|'.join([' --- ']*max_cols) + '|')
        # Add the rest, skipping the header row once
        header_skipped = False
        for r in norm:
            if not header_skipped and r == header:
                header_skipped = True
                continue
            lines.append('| ' + ' | '.join(r) + ' |')
        return '\n'.join(lines)
    except Exception:
        return ''


def convert_pdf_to_markdown(fp: 'FileProcessor', knowledge_file: 'KnowledgeFile', debug: bool = False, out_dir: str | None = None, force_full_ocr: bool = False) -> str:
    """Convert PDF to markdown.

    Parameters:
    - fp: FileProcessor instance (provides thresholds, OCR method, flags)
    - knowledge_file: KnowledgeFile instance (must have file_path and project_id)
    - debug: when True produce verbose logs, save PNGs and analysis to disk
    - out_dir: optional path to directory where debug artifacts will be saved. If None and debug=True a temp dir is created.
    """
    file_path = knowledge_file.file_path
    segments = []
    tables_done = 0
    debug_out = None
    if debug:
        if out_dir:
            debug_out = Path(out_dir)
            debug_out.mkdir(parents=True, exist_ok=True)
        else:
            debug_out = Path(tempfile.mkdtemp(prefix='pdf_debug_'))
        print(f"[FileProcessor][PDF][DEBUG] Debug output dir: {str(debug_out)}")
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        print(f"Error opening PDF with PyMuPDF: {e}")
        return ''
    total_pages = len(doc)
    limit = min(total_pages, fp.MAX_PDF_PAGES)
    if debug:
        print(f"[FileProcessor][PDF][DEBUG] Total pages={total_pages} processing up to {limit}")
    # Classify pages first (still needed for debug / metrics) unless we are forcing full OCR.
    page_meta = classify_pdf_pages(doc, fp)[:limit]
    # If debugging, dump page analysis table to debug_out and to stdout
    if debug:
        try:
            rows = [["Index", "ImageCover", "TextLen", "Kind"]]
            for m in page_meta:
                rows.append([str(m['index'] + 1), f"{m['image_cover']:.3f}", str(m['text_len']), m['kind']])
            analysis_md = markdown_table(rows)
            print('\n[FileProcessor][PDF][DEBUG] Page analysis:\n')
            print(analysis_md)
            if debug_out is not None:
                (debug_out / 'page_analysis.md').write_text(analysis_md, encoding='utf-8')
                (debug_out / 'page_analysis.json').write_text(json.dumps(page_meta, indent=2), encoding='utf-8')
        except Exception as e:
            print(f"[FileProcessor][PDF][DEBUG] Failed to write page analysis: {e}")
    if force_full_ocr:
        # When forcing full OCR, every page becomes an OCR candidate regardless of classification
        ocr_candidates = page_meta  # for logging consistency
        ocr_pages_count = len(ocr_candidates)
        if debug:
            print(f"[FileProcessor][PDF][DEBUG] Force Full OCR enabled: all {ocr_pages_count} pages will be OCR'd")
    else:
        ocr_candidates = [m for m in page_meta if m['kind'] != 'native_text']
        ocr_pages_count = len(ocr_candidates)
        # Guard unless recently retried
        from app.services.file_processor import ProcessingBlocked
        if not fp._has_recent_retry(knowledge_file.id):
            if ocr_pages_count > fp.OCR_GUARD_MIN_PAGES and (ocr_pages_count / max(1, limit)) > fp.OCR_GUARD_PERCENT:
                raise ProcessingBlocked(
                    f"Zbyt wiele stron wymaga OCR ({ocr_pages_count}/{limit}). To może być bardzo kosztowne. "
                    f"Użyj opcji 'Ponów przetwarzanie', aby kontynuować mimo ostrzeżenia."
                )
    # Open pdfplumber for native text extraction and tables
    try:
        pdfp = pdfplumber.open(file_path)
    except Exception as e:
        pdfp = None
        if debug:
            print(f"[FileProcessor][PDF][DEBUG] pdfplumber open failed: {e}")
    try:
        for idx in range(limit):
            kind = page_meta[idx]['kind'] if idx < len(page_meta) else 'uncertain'
            header = f"\n\n## Page {idx+1}\n\n"
            page_md = ''

            if (not force_full_ocr) and kind == 'native_text' and pdfp is not None:
                try:
                    page = pdfp.pages[idx]
                    page_text = page.extract_text() or ''
                    if page_text.strip():
                        page_md += page_text.strip()
                    # Tables only for native pages
                    if tables_done < fp.MAX_PDF_TABLES:
                        try:
                            table_settings = { 'vertical_strategy': 'lines', 'horizontal_strategy': 'lines' }
                            tbls = []
                            try:
                                tbls = page.extract_tables(table_settings)
                            except Exception:
                                tbls = page.extract_tables()
                            for t_i, table in enumerate(tbls or []):
                                if tables_done >= fp.MAX_PDF_TABLES:
                                    break
                                md = markdown_table(table, max_rows=fp.MAX_PDF_TABLE_ROWS)
                                if md:
                                    page_md += f"\n\n### Table p{idx+1}-{t_i+1}\n\n{md}"
                                    tables_done += 1
                        except Exception as te:
                            print(f"[FileProcessor][PDF] table extraction error on page {idx+1}: {te}")
                except Exception as e:
                    print(f"[FileProcessor][PDF] text extraction error on page {idx+1}: {e}")
            else:
                # Render page to PNG (med-low quality ~1.5x) and OCR
                try:
                    page = doc[idx]
                    if fp.MAX_OCR_IMAGES == 0:
                        ocr_text = ''
                    else:
                        mat = fitz.Matrix(1.5, 1.5)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        png_bytes = pix.tobytes("png")
                        # If debug: save generated PNG to debug dir named by page number
                        if debug and debug_out is not None:
                            try:
                                png_name = debug_out / f"page_{idx+1:04}.png"
                                png_name.write_bytes(png_bytes)
                            except Exception as e:
                                print(f"[FileProcessor][PDF][DEBUG] Failed to write PNG for page {idx+1}: {e}")
                        ocr_text, _usage = fp._ocr_png_bytes_to_text(png_bytes, knowledge_file.project_id, idx+1, debug=debug, out_dir=str(debug_out) if debug_out is not None else None)
                    if ocr_text:
                        page_md += ocr_text
                    else:
                        page_md += f"(Uwaga: błąd OCR na stronie {idx+1})"
                except Exception as e:
                    if debug:
                        print(f"[FileProcessor][PDF][DEBUG] OCR render/error on page {idx+1}: {e}")
                    page_md += f"(Uwaga: błąd OCR na stronie {idx+1})"

            if page_md.strip():
                segments.append(header + page_md.strip())
            else:
                segments.append(header + "")

            if (idx + 1) % 5 == 0 and debug:
                print(f"[FileProcessor][PDF][DEBUG] Processed {idx+1}/{limit} pages (kind={kind}) tables_total={tables_done}")

        # After processing pages, if debug: write full markdown to file
        if debug and debug_out is not None:
            try:
                out_md = "\n\n".join(segments)
                (debug_out / 'converted.md').write_text(out_md, encoding='utf-8')
                print(f"[FileProcessor][PDF][DEBUG] Saved converted markdown to {str(debug_out / 'converted.md')}")
            except Exception as e:
                print(f"[FileProcessor][PDF][DEBUG] Failed to write converted markdown: {e}")
    finally:
        try:
            if pdfp is not None:
                pdfp.close()
        except Exception:
            pass
        try:
            doc.close()
        except Exception:
            pass
    return "\n\n".join(segments)
