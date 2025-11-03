import json
from datetime import datetime

import pytest

from flask import current_app

from app import db
from app.models.ai_usage_log import AIUsageLog
from app.models.knowledge_file import KnowledgeFile
from app.services.ai_service import AIService
from app.services.ai_providers.types import TokenUsage


@pytest.mark.usefixtures('app_ctx')
def test_ai_service_search_only_logs(project, admin_user):
    current_app.config['MULTI_QUERY_ENABLED'] = False

    knowledge = KnowledgeFile(
        project_id=project.id,
        filename='doc-1.txt',
        original_filename='Doc 1.txt',
        file_size=1024,
        file_type='txt',
        file_path='/tmp/doc-1.txt',
        file_hash='hash-doc-1',
        status='processed',
        processed_at=datetime.utcnow(),
        uploaded_by=admin_user.id,
        chunks_count=1,
    )
    db.session.add(knowledge)
    db.session.commit()

    service = AIService()

    def _search_single(*args, **kwargs):
        return [
            {
                'id': 'doc-1',
                'content': 'Example fragment body',
                'score': 0.87,
                'metadata': {'title': 'Doc 1', 'file_id': knowledge.id}
            }
        ]

    service.vector_service.search_similar = _search_single

    def _mock_rerank(**kwargs):
        docs = kwargs['documents']
        usage = TokenUsage(prompt_tokens=2, completion_tokens=0)
        return docs, usage

    service.rerank_service.rerank_documents = _mock_rerank

    bundle = service.build_email_prompt(project_id=project.id, email_content='Test email', style_hint='standard')
    result = service.run_search_only(project_id=project.id, prompt_bundle=bundle)

    assert result['success'] is True
    assert result['context_used'] == 1
    assert result['token_usage_breakdown']['rerank']['total_tokens'] == 2
    assert result['rerank_query'] == 'Test email'
    assert result['retrieval_top_k']
    assert result['context_limit'] == bundle.get('context_limit')
    assert result['response_model'] == bundle.get('response_model')
    assert bundle['context_file_names'] == ['Doc 1.txt']
    assert result['context_file_names'] == ['Doc 1.txt']

    logs = AIUsageLog.query.filter_by(project_id=project.id).all()
    assert len(logs) == 1
    metadata = json.loads(logs[0].metadata_json)
    assert metadata['mode'] == 'search_only'
    assert metadata['context_used'] == 1
    assert metadata['response_model'] == bundle.get('response_model')
    assert metadata['context_limit'] == bundle.get('context_limit')
    assert metadata['context_file_ids'] == [knowledge.id]


@pytest.mark.usefixtures('app_ctx')
def test_build_email_prompt_multi_query_flow(project, admin_user):
    current_app.config['MULTI_QUERY_ENABLED'] = True

    knowledge = KnowledgeFile(
        project_id=project.id,
        filename='doc-2.txt',
        original_filename='Doc 2.txt',
        file_size=512,
        file_type='txt',
        file_path='/tmp/doc-2.txt',
        file_hash='hash-doc-2',
        status='processed',
        processed_at=datetime.utcnow(),
        uploaded_by=admin_user.id,
        chunks_count=1,
    )
    db.session.add(knowledge)
    db.session.commit()

    service = AIService()

    variants = ['lease summary status', 'maintenance escalation timeline']
    primary_query = 'lease update status overview'
    multi_usage = TokenUsage(prompt_tokens=3, completion_tokens=1)

    service.openai.create_multiquery_variants = lambda **kwargs: (
        variants,
        primary_query,
        TokenUsage(prompt_tokens=multi_usage.prompt_tokens, completion_tokens=multi_usage.completion_tokens),
    )

    def _search_similar(project_id, query_text, limit=6, debug_label=None, ai_service=None, score_threshold=None, usage_tracker=None, **kwargs):
        assert project_id == project.id
        return [
            {
                'id': f"{query_text}-doc",
                'content': f"Excerpt for {query_text}",
                'score': 0.9 if 'lease' in query_text else 0.86,
                'metadata': {'title': 'Doc 2', 'file_id': knowledge.id},
            }
        ]

    service.vector_service.search_similar = _search_similar

    rerank_calls = {}

    def _rerank_documents(**kwargs):
        rerank_calls['last'] = kwargs
        usage = TokenUsage(prompt_tokens=0, completion_tokens=0)
        return kwargs['documents'], usage

    service.rerank_service.rerank_documents = _rerank_documents

    bundle = service.build_email_prompt(
        project_id=project.id,
        email_content='Need lease update and maintenance status',
        multi_query_mode='force_on',
        multi_query_variants=2,
    )

    assert 'last' in rerank_calls
    assert rerank_calls['last']['query'] == primary_query
    assert bundle['rerank_query'] == primary_query
    assert bundle['multi_query_used'] is True
    assert bundle['multi_query_variants'] == variants
    assert bundle['multi_query_usage']['total_tokens'] == multi_usage.total_tokens
    assert bundle['token_usage_breakdown']['multi_query']['total_tokens'] == multi_usage.total_tokens
    matched_queries = {doc['metadata'].get('matched_query') for doc in bundle['context_docs']}
    assert matched_queries == set(variants)
    assert rerank_calls['last']['provider'] == bundle['rerank_provider']
    assert bundle['retrieval_top_k'] >= 2


@pytest.mark.usefixtures('app_ctx')
def test_build_email_prompt_rerank_fallback(project, admin_user):
    current_app.config['MULTI_QUERY_ENABLED'] = False

    knowledge = KnowledgeFile(
        project_id=project.id,
        filename='doc-3.txt',
        original_filename='Doc 3.txt',
        file_size=2048,
        file_type='txt',
        file_path='/tmp/doc-3.txt',
        file_hash='hash-doc-3',
        status='processed',
        processed_at=datetime.utcnow(),
        uploaded_by=admin_user.id,
        chunks_count=1,
    )
    db.session.add(knowledge)
    db.session.commit()

    service = AIService()

    def _search_fallback(*args, **kwargs):
        return [
            {
                'id': 'doc-high',
                'content': 'High score excerpt',
                'score': 0.92,
                'metadata': {'title': 'Doc High', 'file_id': knowledge.id},
            },
            {
                'id': 'doc-low',
                'content': 'Low score excerpt',
                'score': 0.41,
                'metadata': {'title': 'Doc Low', 'file_id': knowledge.id},
            },
        ]

    service.vector_service.search_similar = _search_fallback

    def _rerank_documents(**kwargs):
        usage = TokenUsage(prompt_tokens=5, completion_tokens=1)
        return [], usage

    service.rerank_service.rerank_documents = _rerank_documents

    bundle = service.build_email_prompt(
        project_id=project.id,
        email_content='Please review latest compliance memo',
        vector_top_k=3,
        rerank_top_k=2,
        rerank_threshold=0.5,
        multi_query_mode='force_off',
    )

    assert bundle['multi_query_used'] is False
    assert bundle['rerank_usage']['total_tokens'] == 6
    assert bundle['rerank_query'] == 'Please review latest compliance memo'
    assert bundle['token_usage_breakdown']['rerank']['total_tokens'] == 6
    assert len(bundle['context_docs']) == 1
    assert bundle['context_docs'][0]['id'] == 'doc-high'
    assert bundle['context_docs'][0]['metadata']['vector_score'] == pytest.approx(0.92)
    assert bundle['rerank_settings']['provider'] == bundle['rerank_provider']
    assert bundle['retrieval_top_k'] >= 2


@pytest.mark.usefixtures('app_ctx')
def test_generate_response_search_only_mode(login_client, project, admin_user, monkeypatch):
    knowledge = KnowledgeFile(
        project_id=project.id,
        filename='doc.txt',
        original_filename='doc.txt',
        file_size=1024,
        file_type='txt',
        file_path='/tmp/doc.txt',
        file_hash='hash123',
        status='processed',
        processed_at=datetime.utcnow(),
        uploaded_by=admin_user.id,
        chunks_count=1
    )
    db.session.add(knowledge)
    db.session.commit()

    class StubAIService:
        last_instance = None

        def __init__(self):
            self.last_build_kwargs = None
            StubAIService.last_instance = self

        def build_email_prompt(self, project_id, email_content, style_hint, **kwargs):
            self.last_build_kwargs = {
                'project_id': project_id,
                'email_content': email_content,
                'style_hint': style_hint,
                'kwargs': kwargs,
            }
            return {
                'system_prompt': 'sys',
                'user_prompt': 'user',
                'context_docs': [
                    {
                        'id': 'doc',
                        'content': 'Example',
                        'score': 0.9,
                        'metadata': {'title': 'doc.txt', 'file_id': knowledge.id}
                    }
                ],
                'context_file_ids': [knowledge.id],
                'multi_query_used': False,
                'multi_query_variants': [],
                'multi_query_variant_count': 0,
                'multi_query_mode': 'org_off',
                'multi_query_model': None,
                'multi_query_usage': {'prompt_tokens': 1, 'completion_tokens': 0, 'total_tokens': 1},
                'rerank_usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                'token_usage_breakdown': {
                    'multi_query': {'prompt_tokens': 1, 'completion_tokens': 0, 'total_tokens': 1},
                    'rerank': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                },
                'retrieval_top_k': 1,
                'retrieval_threshold': None,
                'context_limit': 1,
                'rerank_provider': None,
                'rerank_model': None,
                'rerank_top_k': None,
                'rerank_threshold': None,
                'rerank_settings': {},
                'response_model': 'gpt-5-mini',
                'prefetch_limit': kwargs.get('prefetch_limit'),
                'colbert_candidates': kwargs.get('colbert_candidates'),
                'rrf_k': kwargs.get('rrf_k'),
                'rrf_weights': kwargs.get('rrf_weights'),
                'hybrid_per_vector_limit': kwargs.get('hybrid_per_vector_limit'),
                'hybrid_rrf_k': kwargs.get('hybrid_rrf_k'),
            }

        def run_search_only(self, project_id, prompt_bundle):
            return {
                'success': True,
                'mode': 'search_only',
                'context_used': len(prompt_bundle['context_docs']),
                'context_docs': prompt_bundle['context_docs'],
                'context_file_ids': prompt_bundle['context_file_ids'],
                'token_usage_breakdown': prompt_bundle['token_usage_breakdown'],
                'multi_query_used': prompt_bundle['multi_query_used'],
                'multi_query_variants': prompt_bundle['multi_query_variants'],
                'multi_query_usage': prompt_bundle['multi_query_usage'],
                'rerank_usage': prompt_bundle['rerank_usage'],
                'tokens_input': 0,
                'tokens_output': 0,
                'tokens_total': 0,
                'retrieval_top_k': prompt_bundle['retrieval_top_k'],
                'retrieval_threshold': prompt_bundle['retrieval_threshold'],
                'context_limit': prompt_bundle['context_limit'],
                'response_model': prompt_bundle['response_model'],
                'prefetch_limit': prompt_bundle.get('prefetch_limit'),
                'colbert_candidates': prompt_bundle.get('colbert_candidates'),
                'rrf_k': prompt_bundle.get('rrf_k'),
                'rrf_weights': prompt_bundle.get('rrf_weights'),
            }

        def count_tokens(self, text):
            return 0

        def generate_response(self, *args, **kwargs):
            return {}

    monkeypatch.setattr('app.routes.api.AIService', StubAIService)

    resp = login_client.post(
        f'/api/projects/{project.public_id}/generate-response',
        json={
            'email_content': 'Hello world',
            'mode': 'search_only',
            'options': {
                'context_limit': 5,
                'vector_top_k': 7,
                'retrieval_threshold': 0.4,
                'multiquery_mode': 'force_on',
                'multiquery_model': 'gpt-test',
                'multiquery_variants': 6,
                'rerank_provider': 'zeroentropy',
                'rerank_model': 'custom-rerank',
                'rerank_top_k': 5,
                'rerank_threshold': 0.55,
                'response_model': 'gpt-custom',
                'prefetch_limit': 9,
                'colbert_candidates': 11,
                'rrf_k': 75,
                'rrf_weights': {'dense': 1.3, 'sparse': 1.0}
            }
        }
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['mode'] == 'search_only'
    assert data['tokens_used'] == 1
    assert data['context_filenames'] == ['doc.txt']
    assert data['context_docs'][0]['metadata']['file_id'] == knowledge.id
    stub_instance = StubAIService.last_instance
    assert stub_instance is not None
    forwarded = stub_instance.last_build_kwargs
    assert forwarded is not None
    overrides = forwarded['kwargs']
    assert overrides['max_context_docs'] == 5
    assert overrides['vector_top_k'] == 7
    assert overrides['retrieval_threshold'] == 0.4
    assert overrides['multi_query_mode'] == 'force_on'
    assert overrides['multi_query_model'] == 'gpt-test'
    assert overrides['multi_query_variants'] == 6
    assert overrides['rerank_provider'] == 'zeroentropy'
    assert overrides['rerank_model'] == 'custom-rerank'
    assert overrides['rerank_top_k'] == 5
    assert overrides['rerank_threshold'] == 0.55
    assert overrides['response_model'] == 'gpt-custom'
    assert overrides['prefetch_limit'] == 9
    assert overrides['colbert_candidates'] == 11
    assert overrides['rrf_k'] == 75
    assert overrides['rrf_weights'] == {'dense': 1.3, 'sparse': 1.0}
    assert overrides['hybrid_per_vector_limit'] == 9
    assert overrides['hybrid_rrf_k'] == 75
