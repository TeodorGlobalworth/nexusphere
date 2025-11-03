from app import db
from datetime import datetime

class AIUsageLog(db.Model):
    __tablename__ = 'ai_usage_log'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True)
    source = db.Column(db.String(50), nullable=False)  # embedding | ocr | image_description | email_analysis
    model = db.Column(db.String(100), nullable=False)
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    total_tokens = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    metadata_json = db.Column(db.Text)  # optional JSON string with extra context

    project = db.relationship('Project', backref=db.backref('ai_usage_logs', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'source': self.source,
            'model': self.model,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'total_tokens': self.total_tokens,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'metadata_json': self.metadata_json,
        }
