"""Admin routes - simplified without organizations."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_babel import gettext as _
from flask_login import current_user
from app.models.user import User
from app.models.project import Project, ProjectUser
from app.models.knowledge_file import KnowledgeFile
from app import db
from sqlalchemy import func
from werkzeug.security import generate_password_hash

bp = Blueprint("admin", __name__)

@bp.before_request
def require_admin_guard():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not (current_user.is_admin or current_user.is_superadmin):
        flash(_("Brak uprawnień do panelu administracyjnego."), "danger")
        return redirect(url_for("main.dashboard"))

@bp.route("/dashboard")
def dashboard():
    projects = Project.query.all()
    users = User.query.all()
    total_files = db.session.query(func.count(KnowledgeFile.id)).scalar() or 0
    
    stats = {
        "total_projects": len(projects),
        "total_users": len(users),
        "total_files": total_files
    }
    
    return render_template("admin/dashboard.html", projects=projects[:10], users=users[:10], stats=stats, orgs=[])

@bp.route("/experimental", methods=["GET"])
def experimental():
    projects = Project.query.all()
    return render_template("admin/experimental.html", organizations=[], projects=projects, active_org_id=None)

@bp.route("/projects")
def projects():
    page = request.args.get("page", 1, type=int)
    pagination = Project.query.order_by(Project.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/projects.html", projects=pagination.items, pagination=pagination)

@bp.route("/users")
def users():
    page = request.args.get("page", 1, type=int)
    pagination = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    total_users = User.query.count()
    return render_template("admin/users.html", users=pagination.items, pagination=pagination, total_users=total_users)

@bp.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        response_style = request.form.get("response_style", "standard")
        
        if not name:
            flash(_("Nazwa projektu jest wymagana."), "danger")
            return redirect(url_for("admin.new_project"))
        
        # Create project
        project = Project(
            name=name,
            description=description,
            response_style=response_style,
            created_by=current_user.id
        )
        db.session.add(project)
        
        # Add creator as project user
        project_user = ProjectUser(
            project=project,
            user=current_user,
            role='admin'
        )
        db.session.add(project_user)
        
        db.session.commit()
        flash(_("Projekt został utworzony pomyślnie!"), "success")
        return redirect(url_for("admin.projects"))
    
    return render_template("admin/new_project.html")

@bp.route("/users/new", methods=["GET", "POST"])
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "on"
        is_superadmin = request.form.get("is_superadmin") == "on" if current_user.is_superadmin else False
        
        if not username or not email or not password:
            flash(_("Wszystkie pola są wymagane."), "danger")
            return redirect(url_for("admin.new_user"))
        
        if len(password) < 8:
            flash(_("Hasło musi mieć minimum 8 znaków."), "danger")
            return redirect(url_for("admin.new_user"))
        
        # Check if user already exists
        if User.query.filter_by(username=username).first():
            flash(_("Nazwa użytkownika jest już zajęta."), "danger")
            return redirect(url_for("admin.new_user"))
        
        if User.query.filter_by(email=email).first():
            flash(_("Adres email jest już zajęty."), "danger")
            return redirect(url_for("admin.new_user"))
        
        # Create user
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            is_superadmin=is_superadmin
        )
        db.session.add(user)
        db.session.commit()
        
        flash(_("Użytkownik został utworzony pomyślnie!"), "success")
        return redirect(url_for("admin.users"))
    
    return render_template("admin/new_user.html")
