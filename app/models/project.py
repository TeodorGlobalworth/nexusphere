
from app import db
from datetime import datetime
from sqlalchemy import func

try:
    from app.models.ai_usage_log import AIUsageLog
except Exception:
    AIUsageLog = None
import uuid

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, index=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    context = db.Column(db.String(200))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    response_style = db.Column(db.String(20), default='standard')

    # Relationships
    users = db.relationship('ProjectUser', back_populates='project')
    knowledge_files = db.relationship('KnowledgeFile', backref='project')

    def get_users(self):
        return [pu.user for pu in self.users]

    def get_monthly_tokens_used(self):
        if AIUsageLog is None:
            return 0
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        q = db.session.query(func.coalesce(func.sum(AIUsageLog.total_tokens), 0)).filter(
            AIUsageLog.project_id == self.id,
            AIUsageLog.created_at >= month_start,
            AIUsageLog.created_at < next_month
        )
        return int(q.scalar() or 0)

class ProjectUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    role = db.Column(db.String(20), default='user')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    project = db.relationship('Project', back_populates='users')
    user = db.relationship('User', back_populates='projects')
