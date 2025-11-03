
from app import db
from datetime import datetime

class KnowledgeFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False, index=True)

    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    file_type = db.Column(db.String(20), nullable=False, index=True)
    file_path = db.Column(db.String(500), nullable=False)
    file_hash = db.Column(db.String(128), nullable=True, index=True)

    # Processing status
    status = db.Column(db.String(20), default='uploaded')  # uploaded, processing, processed, error
    processed_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)

    # Vector DB info
    vector_collection = db.Column(db.String(100))
    chunks_count = db.Column(db.Integer, default=0)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def get_size_formatted(self):
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
