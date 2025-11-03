from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db


class GenerationHistory(db.Model):
    __tablename__ = 'generation_history'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), index=True, nullable=False)
    project_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    source_kind = db.Column(db.String(20), nullable=False)  # ui | mailbox | api
    source_address = db.Column(db.String(255), nullable=True)
    source_user = db.Column(db.String(255), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    response_body = db.Column(db.Text, nullable=False)
    chunk_refs = db.Column(db.JSON, nullable=True)
    total_tokens = db.Column(db.Integer, default=0)
    spam_flag = db.Column(db.Boolean, nullable=True)
    importance_level = db.Column(db.String(32), nullable=True)

    project = db.relationship('Project', backref=db.backref('generation_history', lazy='dynamic'))

    def chunk_reference_list(self) -> List[Dict[str, Any]]:
        value = self.chunk_refs
        if isinstance(value, list):
            return [ref for ref in value if isinstance(ref, dict)]
        return []

    def to_summary(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'project_id': self.project_id,
            'project_name': self.project_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'source_kind': self.source_kind,
            'source_address': self.source_address,
            'source_user': self.source_user,
            'title': self.title,
            'response_body': self.response_body,
            'chunk_refs': self.chunk_reference_list(),
            'total_tokens': self.total_tokens or 0,
            'spam_flag': self.spam_flag,
            'importance_level': self.importance_level,
        }
