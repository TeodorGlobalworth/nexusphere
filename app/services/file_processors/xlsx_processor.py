import os
from typing import Optional, Tuple

import pandas as pd

from app.services.bge_client import BGEClientError


def _compact_markdown_from_df(df: pd.DataFrame) -> str:
    df = df.copy()
    # replace NaN with empty strings for nicer output
    df = df.fillna("")
    header = "|".join(map(str, df.columns))
    separator = "|".join(["---"] * len(df.columns))
    rows = ["|".join(map(lambda x: str(x), row)) for row in df.to_numpy()]
    return "\n".join([header, separator] + rows)


REQUIRED_SHEETS_ERROR = 'Brak wymaganych arkuszy: Description i/lub Table.'


def convert_xlsx_to_markdown_and_embed(fp, knowledge_file, do_embed: bool = True) -> Tuple[Optional[str], int, int]:
    """Use simple pandas-based helpers (aligned with ExcelImportSample.py).
    - Read Description!A2 as text for embedding.
    - Build compact Markdown table from 'Table' sheet.
    - Enforce 2000-token limit on table markdown.
    Returns (markdown, processed_vectors_count, tokens_used_for_embedding_input)
    """
    logger = fp._logger()
    path = knowledge_file.file_path
    if not path or not os.path.exists(path):
        return None, 0, 0

    try:
        xls = pd.ExcelFile(path)
    except Exception:
        return None, 0, 0

    name_map = {name.lower(): name for name in xls.sheet_names}
    if 'description' not in name_map or 'table' not in name_map:
        knowledge_file.status = 'error'
        knowledge_file.error_message = REQUIRED_SHEETS_ERROR
        return None, 0, 0

    # Description!A2 (row index 1, col index 0)
    try:
        df_desc = pd.read_excel(path, sheet_name=name_map['description'], header=None)
        description_text = str(df_desc.iloc[1, 0]) if df_desc.shape[0] > 1 and df_desc.shape[1] > 0 else ''
        description_text = '' if description_text.lower() == 'nan' else description_text.strip()
    except Exception:
        description_text = ''

    # Table sheet to compact Markdown
    try:
        df_table = pd.read_excel(path, sheet_name=name_map['table'])
        table_md = _compact_markdown_from_df(df_table)
    except Exception:
        table_md = ''

    # Token cap check for table
    try:
        table_tokens = fp.ai_service().count_tokens(table_md)
    except Exception:
        table_tokens = 0
    if table_tokens and table_tokens > 2000:
        knowledge_file.status = 'error'
        knowledge_file.error_message = 'Zawartość arkusza Table przekracza limit 2000 tokenów.'
        return None, 0, 0

    processed = 0
    used_tokens = 0
    if do_embed and description_text:
        fp.vector_service().create_collection(knowledge_file.project_id)
        emb = None
        emb_usage = None
        try:
            emb, emb_usage = fp.ai_service().generate_embeddings(description_text)
        except Exception:
            emb, emb_usage = None, None
        if emb:
            lexical_sparse = None
            colbert_vector = None
            try:
                bge_result = fp.bge_client().encode([description_text], return_dense=False, return_colbert_vecs=True)
                lexical_sparse = bge_result.first_sparse()
                colbert_vector = bge_result.first_colbert_agg()
            except BGEClientError as exc:
                logger.warning(
                    "BGE ingestion encode failed (xlsx)",
                    extra={'event': 'xlsx_processor_bge_failed', 'file_id': knowledge_file.id, 'error': str(exc)}
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error calling BGE for xlsx description",
                    extra={'event': 'xlsx_processor_bge_error', 'file_id': knowledge_file.id, 'error': str(exc)}
                )
            try:
                base_file_id = int(knowledge_file.id)
            except Exception:
                base_file_id = abs(hash(str(knowledge_file.id))) % 100000
            point_id = base_file_id * 100000
            ok = fp.vector_service().add_document(
                project_id=knowledge_file.project_id,
                document_id=point_id,
                text=description_text,
                embeddings=emb,
                lexical=lexical_sparse,
                colbert=colbert_vector,
                metadata={
                    'file_id': knowledge_file.id,
                    'filename': knowledge_file.original_filename,
                    'chunk_index': 0,
                    'file_type': knowledge_file.file_type,
                    'chunk_key': f"{knowledge_file.id}_0",
                    'xlsx_table_markdown': table_md
                }
            )
            if ok:
                processed += 1
                try:
                    if emb_usage:
                        tokens_used = emb_usage.total_tokens or emb_usage.prompt_tokens
                    else:
                        tokens_used = None
                    if not tokens_used:
                        tokens_used = fp.ai_service().count_tokens(description_text)
                    used_tokens += tokens_used or 0
                except Exception:
                    pass

    # Combined markdown for preview
    md_parts = [f"# {knowledge_file.original_filename}"]
    if description_text:
        md_parts += [f"## Opis", description_text]
    if table_md:
        md_parts += [f"## Tabela", table_md]
    return "\n\n".join(md_parts), processed, used_tokens
