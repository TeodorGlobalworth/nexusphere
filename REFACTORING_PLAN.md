# Plan Refaktoringu - Transformacja do Document Search Engine

## Cel projektu
Przekształcenie aplikacji z multi-tenant email processing do lokalnej aplikacji do wyszukiwania dokumentów z prostym UI.

## Co zostaje:
- ✅ Użytkownicy (User model) - przypisywanie do projektów
- ✅ Projekty (Project model) - organizacja dokumentów
- ✅ System wyszukiwania: BGE-M3 + OpenAI + Reranker + Qdrant
- ✅ Procesowanie plików (file_processor + knowledge_file)
- ✅ AI Service (pełna funkcjonalność - do późniejszej przebudowy)
- ✅ Generation History - historia generowania
- ✅ AI Usage Log - logowanie zapytań
- ✅ File Processing Log - logowanie przetwarzania
- ✅ WebSockets (ws.py) - real-time updates
- ✅ Admin panel (uproszczony)
- ✅ Upload interface
- ✅ Docker (PostgreSQL, Qdrant, BGE-M3)

## Co wywalamy:
- ❌ Email (service, monitor, simulator, models)
- ❌ ClamAV antywirus
- ❌ nginx
- ❌ Celery + Redis (async tasks)
- ❌ OAuth integrations
- ❌ Organizations & Packages
- ❌ Analytics
- ❌ Audit logs (ogólne)
- ❌ Context Prompts
- ❌ Translations (i18n) - zostaje tylko English
- ❌ Scripts folder
- ❌ MFA/2FA
- ❌ User routes (profile management z MFA)

---

## ETAP 1: Usunięcie Backend Models & Services (BEZPIECZNE)
**Czas: ~10-15 min**

### 1.1 Usunięcie modeli bazodanowych
Pliki do usunięcia z `app/models/`:
- [ ] `email_account.py`
- [ ] `processed_email.py`
- [ ] `organization.py`
- [ ] `organization_package_change_log.py`
- [ ] `package_change_log.py`
- [ ] `project_oauth_config.py`
- [ ] `context_prompt.py`
- [ ] `audit_log.py`

### 1.2 Usunięcie services
Pliki do usunięcia z `app/services/`:
- [ ] `email_service.py`
- [ ] `mail_monitor.py`
- [ ] `mail_simulator.py`
- [ ] `audit.py`

Pliki do usunięcia z `app/utils/`:
- [ ] `clamav_client.py`
- [ ] `language.py`

### 1.3 Analiza i cleanup app/services/ subdirectories
- [ ] Przeanalizować `app/services/ai_components/` - usunąć zbędne
- [ ] Przeanalizować `app/services/ai_providers/` - zostawić tylko używane
- [ ] Przeanalizować `app/services/file_processors/` - zostawić tylko używane

**Checkpoint**: Commit zmian

---

## ETAP 2: Usunięcie Routes & Controllers
**Czas: ~10 min**

### 2.1 Usunięcie niepotrzebnych routes
Pliki do usunięcia z `app/routes/`:
- [ ] `analytics.py`
- [ ] `oauth.py`
- [ ] `ui_preview.py`
- [ ] `user.py` (zawiera MFA/org dependencies)

### 2.2 Uproszczenie admin.py
- [ ] Usunąć z `app/routes/admin.py`:
  - Organization management
  - Package management
  - Email accounts config
  - MFA settings
  - Analytics dashboard
- [ ] Zostawić tylko:
  - User management
  - Project management
  - Basic settings

**Checkpoint**: Commit zmian

---

## ETAP 3: Czyszczenie Templates & Static Files
**Czas: ~10 min**

### 3.1 Usunięcie folderów templates
Z `app/templates/` usunąć:
- [ ] `analytics/`
- [ ] `ui_preview/`
- [ ] `user/` (MFA templates)

### 3.2 Uproszczenie admin templates
- [ ] `admin/` - usunąć templates dla org, packages, email
- [ ] Zostawić tylko: users, projects, basic settings

### 3.3 Analiza i cleanup pozostałych templates
- [ ] `project/` - przeanalizować, zostawić search + file management
- [ ] `auth/` - zostawić login/logout, usunąć MFA
- [ ] `errors/` - zostawić
- [ ] `_macros/` - przeanalizować, usunąć zbędne
- [ ] `base.html` - uprościć navigation

**Checkpoint**: Commit zmian

---

## ETAP 4: Uproszczenie API & Main Routes
**Czas: ~15 min**

### 4.1 Uproszczenie api.py
Z `app/routes/api.py` usunąć:
- [ ] Email endpoints
- [ ] Analytics endpoints
- [ ] OAuth endpoints
- [ ] Organization endpoints
- [ ] Package endpoints

Zostawić:
- [ ] Search endpoints
- [ ] File upload/management
- [ ] Project endpoints
- [ ] User endpoints (basic)

### 4.2 Uproszczenie main.py
- [ ] Usunąć niepotrzebne routes
- [ ] Zostawić: home, search, project selection

**Checkpoint**: Commit zmian

---

## ETAP 5: Docker & Infrastructure Cleanup
**Czas: ~10 min**

### 5.1 Aktualizacja docker-compose.yml
Usunąć services:
- [ ] nginx
- [ ] celery-worker
- [ ] celery-beat
- [ ] redis
- [ ] clamav

Zostawić:
- [ ] db (PostgreSQL)
- [ ] qdrant
- [ ] bge-m3
- [ ] web (Flask app)

### 5.2 Usunięcie plików Docker
- [ ] `docker/nginx.conf`
- [ ] `docker/clamav/` (cały folder)

### 5.3 Usunięcie Celery
- [ ] `app/celery_app.py`

**Checkpoint**: Commit zmian

---

## ETAP 6: Cleanup Dependencies & Config
**Czas: ~10 min**

### 6.1 Czyszczenie requirements.txt
Usunąć dependencies dla:
- [ ] celery, redis
- [ ] imapclient (email)
- [ ] flask-mail
- [ ] pyotp, qrcode (MFA)
- [ ] extract-msg (email parsing)
- [ ] flask-babel, babel (i18n)
- [ ] clamd (ClamAV)

### 6.2 Cleanup config/config.py
Usunąć konfiguracje:
- [ ] Celery
- [ ] Email (IMAP, SMTP)
- [ ] OAuth
- [ ] ClamAV
- [ ] MFA settings
- [ ] Organization/Package settings
- [ ] Babel/i18n

### 6.3 Usunięcie translations
- [ ] `babel.cfg`
- [ ] `messages.pot`
- [ ] `app/translations/` (cały folder)

**Checkpoint**: Commit zmian

---

## ETAP 7: Cleanup Scripts & Documentation
**Czas: ~5 min**

### 7.1 Usunięcie scripts
- [ ] Cały folder `scripts/` (będzie oddzielny automat)

### 7.2 Usunięcie sample data
- [ ] `Sample_Databse_import/` folder

### 7.3 Cleanup docs
- [ ] Usunąć nieaktualne docs z `docs/`
- [ ] Usunąć `rendered_snapshots/` (stare screenshoty)

**Checkpoint**: Commit zmian

---

## ETAP 8: Aktualizacja app/__init__.py & Głównych plików
**Czas: ~15 min**

### 8.1 Aktualizacja app/__init__.py
- [ ] Usunąć inicjalizację Celery
- [ ] Usunąć Flask-Babel
- [ ] Usunąć Flask-Mail
- [ ] Usunąć rejestracje blueprints: analytics, oauth, ui_preview, user
- [ ] Uprościć konfigurację

### 8.2 Aktualizacja run.py i wsgi.py
- [ ] Sprawdzić i uprościć jeśli potrzeba

**Checkpoint**: Commit zmian

---

## ETAP 9: UI Redesign - Nowa Kolorystyka
**Czas: ~20-30 min**

### 9.1 Nowa paleta kolorów (inspiracja GlobalWorth)
```css
/* Primary */
--primary-burgundy: #8B3A3A;
--primary-dark: #6B2929;
--primary-light: #A64D4D;

/* Accent */
--accent-gold: #D4AF37;
--accent-gold-light: #E5C158;

/* Neutral */
--bg-white: #FFFFFF;
--bg-light: #F5F5F5;
--text-dark: #2C2C2C;
--text-gray: #666666;

/* Status */
--warning: #FFC107;
--success: #4CAF50;
--error: #D32F2F;
```

### 9.2 Aktualizacja CSS
- [ ] Nowy `app/static/css/main.css` z kolorystyką
- [ ] Usunąć stare, nieużywane pliki CSS
- [ ] Minimalistyczny design

### 9.3 Aktualizacja base.html
- [ ] Nowa navigation (tylko: Projects, Search, Admin, Logout)
- [ ] Nowy header z kolorystyką burgundu
- [ ] Uproszczony layout

### 9.4 Redesign kluczowych stron
- [ ] Login page (burgundy theme)
- [ ] Project selection page
- [ ] Search interface (minimal, clean)
- [ ] File upload page

**Checkpoint**: Commit zmian

---

## ETAP 10: Database Migration & Cleanup
**Czas: ~10 min**

### 10.1 Aktualizacja migracji bazy danych
- [ ] Utworzyć migration usuwający stare tabele:
  - email_accounts
  - processed_emails
  - organizations
  - package_change_logs
  - organization_package_change_logs
  - project_oauth_configs
  - context_prompts
  - audit_logs

### 10.2 Aktualizacja init_db.sql
- [ ] Usunąć niepotrzebne tabele z init skryptu

**Checkpoint**: Commit zmian

---

## ETAP 11: Testing & Bug Fixing
**Czas: ~30-60 min**

### 11.1 Sprawdzenie importów
- [ ] Przejrzeć wszystkie pliki pod kątem broken imports
- [ ] Poprawić importy po usuniętych modułach

### 11.2 Aktualizacja testów
Z `tests/`:
- [ ] Usunąć testy dla usuniętych funkcjonalności
- [ ] Zaktualizować testy dla zachowanych modułów
- [ ] Uruchomić testy

### 11.3 Weryfikacja Docker
- [ ] `docker-compose build`
- [ ] `docker-compose up`
- [ ] Sprawdzić czy wszystkie serwisy startują

### 11.4 Smoke tests
- [ ] Login
- [ ] Projekt creation/selection
- [ ] File upload
- [ ] Search functionality
- [ ] Admin panel

**Checkpoint**: Final commit

---

## ETAP 12: Dokumentacja & Finalizacja
**Czas: ~15 min**

### 12.1 Aktualizacja README.md
- [ ] Nowy opis projektu
- [ ] Zaktualizowane instrukcje instalacji
- [ ] Usunięcie odniesień do starych funkcjonalności

### 12.2 Cleanup
- [ ] Usunąć `REFACTORING_PLAN.md` (ten plik)
- [ ] Usunąć stare pliki konfiguracyjne jeśli pozostały
- [ ] Final review kodu

---

## Podsumowanie Etapów

| Etap | Czas | Status |
|------|------|--------|
| 1. Backend Models & Services | 15 min | ✅ Zakończony |
| 2. Routes & Controllers | 10 min | ⚠️ Częściowo (admin.py wymaga sesji) |
| 3. Templates & Static | 10 min | ✅ Zakończony |
| 4. API & Main Routes | 15 min | ⚠️ Częściowo (api.py wymaga sesji) |
| 5. Docker & Infrastructure | 10 min | ✅ Zakończony |
| 6. Dependencies & Config | 10 min | ✅ Zakończony |
| 7. Scripts & Docs | 5 min | ✅ Zakończony |
| 8. app/__init__.py | 15 min | ✅ Zakończony |
| 9. Naprawa importów | 10 min | ✅ Zakończony |
| 10. Database Migration | 10 min | ⏸️ Odroczone |
| 11. Testing & Bugs | 60 min | ⏸️ Odroczone |
| 12. Documentation | 15 min | ⏸️ Odroczone |
| **COMPLETED** | **~2h** | **8/12 etapów** |

---

## Kolejność wykonania

**Rekomendowany flow:**
1. Start od Etapu 1-4 (backend cleanup) - najmniej ryzykowne
2. Potem Etap 5-8 (infrastructure & config) 
3. Na koniec Etap 9 (UI) - najbardziej czasochłonny
4. Finał: Etap 10-12 (testing & docs)

**Po każdym etapie: commit + verify że nic nie pękło**

---

## Dodatkowe notatki

### Rzeczy do rozważenia później (POZA SCOPE):
- Oddzielny automat do zasilania Qdrant
- Wyszukiwarka międzyprojektowa
- Dodatkowe filtry wyszukiwania
- Batch processing dokumentów
- Export wyników wyszukiwania

### Technologie finalne (po refactoringu):
- **Frontend**: Flask + Jinja2 + Vanilla JS (burgundy theme)
- **Backend**: Flask + SQLAlchemy
- **AI Stack**: BGE-M3 (embeddings), OpenAI (generation), Reranker API
- **Vector DB**: Qdrant
- **Storage**: PostgreSQL
- **Container**: Docker Compose
- **Auth**: Flask-Login (simple)

---

*Wygenerowano: 2025-10-29*
*Projekt: NexuSphere → Document Search Engine*
