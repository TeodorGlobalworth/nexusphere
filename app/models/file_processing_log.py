from app import db
from datetime import datetime

class FileProcessingLog(db.Model):
    __tablename__ = 'file_processing_log'

    id = db.Column(db.Integer, primary_key=True)
    knowledge_file_id = db.Column(db.Integer, db.ForeignKey('knowledge_file.id', ondelete='CASCADE'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True)
    event = db.Column(db.String(30), nullable=False)  # start | success | error | retry
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'knowledge_file_id': self.knowledge_file_id,
            'project_id': self.project_id,
            'event': self.event,
            'message': self.message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
