import pytest

from app import create_app, db
from app.models.project import Project, ProjectUser
from app.models.user import User


@pytest.fixture
def app():
    app = create_app('config.TestingConfig')
    app.config.setdefault('TESTING', True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def admin_user(app_ctx):
    user = User(username="admin", email="admin@example.com", is_admin=True)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def project(app_ctx, admin_user):
    project = Project(name="Test Project", created_by=admin_user.id, response_style="standard")
    db.session.add(project)
    db.session.commit()
    membership = ProjectUser(project_id=project.id, user_id=admin_user.id, role='admin')
    db.session.add(membership)
    db.session.commit()
    return project


@pytest.fixture
def login_client(client, admin_user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_user.id)
        sess['_fresh'] = True
    return client
