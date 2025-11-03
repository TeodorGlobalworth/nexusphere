# NexuSphere Operations Manual

This manual explains what NexuSphere does, how the service is organised, and how to operate it day to day. The language is intentionally plain so that non-technical operators can follow each procedure. Wherever a concept is technical, short call-outs explain the gist in simple terms.

> **Tip for bilingual teams.** When a Polish term differs from the English UI label, the Polish wording appears in parentheses, e.g. “Organisation (Organizacja)”.

---

## Table of contents

1. [Platform overview](#platform-overview)
2. [System architecture](#system-architecture)
3. [Core concepts](#core-concepts)
4. [Screen-by-screen tour](#screen-by-screen-tour)
5. [Development (practice) setup](#development-practice-setup)
6. [Production launch](#production-launch)
7. [Configuration reference](#configuration-reference)
8. [Routine operations](#routine-operations)
9. [Maintenance & security](#maintenance--security)
10. [Operational cost & capacity planning](#operational-cost--capacity-planning)
11. [Troubleshooting](#troubleshooting)
12. [FAQ](#faq)
13. [Support channels](#support-channels)
14. [Appendix A – Glossary](#appendix-a--glossary)

Use the table of contents to jump directly to the task you are performing; every section stands on its own.

---

## Platform overview

NexuSphere is an AI co-pilot for teams that answer large volumes of email. The platform ingests company knowledge (documents, notes, archived threads), watches shared inboxes, and produces draft replies that match the organisation's policies and tone. Operators double-check the context, edit the suggested answer, and respond much faster than by hand.

Key benefits:

- **Central knowledge base** – PDFs, DOCX, TXT, EML, MSG, and similar files are normalised, sliced into fragments, and stored in the vector engine for quick semantic search.
- **Inbox monitoring** – Gmail, Microsoft 365/Outlook, or generic IMAP mailboxes can be connected. Projects choose which folders OmniResponse monitors.
- **AI draft generation** – incoming messages or pasted content trigger multi-query retrieval, optional reranking, and an OpenAI Responses call that returns a ready-to-review draft.
- **Audit trails** – each AI operation logs token consumption, referenced documents, and the operator who triggered the action.
- **Role-based access** – organisations contain projects; projects contain users, documents, mailboxes, and limits. Users only see the data for their assigned projects.

---

## System architecture

| Component | Purpose | Key facts |
|-----------|---------|-----------|
| Flask web app (`web` container) | Serves the dashboard, API, and background jobs. | Code lives in `app/`; Celery handles long-running tasks. |
| PostgreSQL (`db`) | Stores organisations, projects, users, and logs. | Data lives in a Docker volume so restarts keep history. |
| Qdrant (`qdrant`) | Vector database for knowledge fragments (embeddings). | One collection per project; auto-created on upload. |
| ClamAV (`clamav`) | Scans uploads for malware. | Optional in practice mode, mandatory for production. |
| OpenAI Responses API | Generates drafts and embeddings. | Needs an API key stored in `.env`. |
| Optional rerank APIs | ZeroEntropy or Novita reorder search results. | Disabled by default; enable when quality demands. |

### High-level data flow

1. An email arrives (or an operator pastes text into the composer).
2. The retrieval pipeline generates multiple search queries, fetches relevant fragments from Qdrant, and optionally reranks them.
3. The prompt builder combines system instructions, style samples, and the retrieved fragments.
4. The OpenAI Responses API produces a draft reply and usage metrics.
5. The draft, context, and token counts are stored and shown in the UI.

---

## Core concepts

| Term | What it means | Why it matters |
|------|----------------|----------------|
| Organisation (Organizacja) | Top-level customer entity. | Holds projects, billing limits, and OAuth credentials. |
| Project (Projekt) | Operational unit inside an organisation. | Owns knowledge files, mailboxes, and users. |
| Package (Pakiet) | Bundle of monthly AI tokens and fragment capacity. | Controls how much a project can upload and generate. |
| Knowledge file | Uploaded document converted into indexed fragments. | Supplies factual context for responses. |
| Fragment | Chunk of text stored in Qdrant. | Retrieved during semantic search. |
| Usage log | Database record of tokens consumed. | Supports billing and audits. |
| Multi-query retrieval | Strategy issuing several related searches. | Improves recall of relevant documents. |
| Rerank | Secondary model reordering search results. | Improves precision when many fragments match. |

---

## Screen-by-screen tour

This section describes each major screen: the information it shows, actions available, and who can use it.

### Global navigation

- **Top bar** – organisation/project selector, language switch, user menu (profile, MFA, logout).
- **Sidebar** – groups screens into “Operations”, “Knowledge”, “Email”, “Administration”, and “System”. Items appear based on role.
- **Alert banner** – highlights quota warnings, antivirus downtime, or missing configuration.

### Project dashboard (Kokpit projektu)

**Purpose:** give operators a live picture of their project.

- **Usage cards** – monthly tokens, fragment usage, active packages, remaining budget. Colours shift at 70% and 90% to warn about limits.
- **Knowledge status** – five latest uploads with processing state. Click to open details.
- **Email activity feed** – last 20 drafts with status (sent, pending, failed) and token cost.
- **Quick actions** – buttons for “Upload knowledge”, “Generate response”, “Invite teammate”.
- **Real-time updates** – websockets refresh metrics; a yellow banner appears if the socket disconnects.

Permissions: project admins/operators have full access; viewers see metrics only.

### Response composer (Generator odpowiedzi)

**Purpose:** produce AI drafts on demand.

1. Paste or type the incoming message. Optional fields accept subject, sender, and desired tone (formal/standard/casual).
2. Toggle **Search only** to preview retrieved fragments without generating a response.
3. Optionally attach up to three supporting files; they influence retrieval and are deleted afterwards.
4. Click **Generate draft**. The right panel displays the AI answer, context fragments with highlights, and token usage including rerank spend if enabled.
5. Operators can copy the draft, flag it as helpful/not accurate, or mark it sent. Feedback writes to `generation_history`.

### Knowledge base (Baza wiedzy)

**Purpose:** manage documents feeding the AI.

- **Upload drawer** – drag-and-drop files, view antivirus queue and estimated fragment count.
- **Files table** – columns for status, type, uploader, size, fragment count, updated date. Filters by status and type.
- **Detail view** – processing timeline, OCR statistics, fragment preview (Markdown), retry controls, and “Force full OCR” when scans dominate.
- **Indicators** – blue *Processing*, amber *Needs attention* (high OCR cost), red *Malware blocked* (ClamAV quarantine; superadmin required).

### Email accounts (Konta e-mail)

**Purpose:** configure mailbox connections per project.

- **Account list** – provider, connection status, last sync timestamp, monitored folders.
- **Add account wizard** – choose provider → authenticate (OAuth or IMAP) → pick folders → decide whether to download attachments.
- **Health check** – run a live test; shows latency, new UID count, and warnings (expiring token, invalid credentials).

### Usage logs (Zużycie AI)

**Purpose:** audit AI spend.

- Filters by date range, organisation, project, and source (manual, inbox, search only).
- Table displays total tokens, prompt vs completion split, rerank tokens, triggering user.
- CSV export for finance reconciliations.
- Forecast chart extrapolates usage versus package limit for the month.

### Project settings (Ustawienia projektu)

Tabs:

1. **General** – name, locale, timezone, default tone.
2. **Packages & limits** (superadmin) – assign packages, set hard caps, enable overflow alerts.
3. **Prompt tuning** – manage sample emails (`sample_email_1..3`), voice guidelines, fallback instructions.
4. **Retrieval** – set multi-query count, rerank provider, context token cap, attachment defaults.

All changes apply immediately and log who modified what.

### Organisation administration (Superadmin)

- **Organisations list** – filters, package summaries, suspension toggles, MFA enforcement switches.
- **Organisation detail** – billing info, address, central OAuth credentials (Gmail/Outlook). Sensitive fields update only when non-empty values are supplied.
- **Suspension page** – blocked organisations see a dedicated notice with support instructions until reactivated.

### User management & MFA

- **User list** – search by name/email, filter by role/project, reset passwords, force MFA, deactivate accounts.
- **Invite modal** – send onboarding email with selected role; links expire after 72 hours.
- **Profile screen** – change password, enable/disable TOTP MFA, download recovery codes.

### Optional FAQ (if enabled)

Lightweight FAQ for operators, fully translated (PL/EN) via `app/translations`. Ideal for quick self-service answers.

---

## Development (practice) setup

Use this mode on a workstation or sandbox server. Commands assume Windows PowerShell; macOS/Linux can run the same commands (switch backslashes to slashes).

### 1. Install prerequisites

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and enable WSL2 on Windows.
2. (Optional) Install [Git](https://git-scm.com/downloads).
3. Restart Docker Desktop to ensure `docker compose` works.

### 2. Obtain the source code

```powershell
cd C:\Path\To\Projects
git clone https://github.com/YOUR_USERNAME/NexuSphere.git
cd NexuSphere
```

Or download the ZIP from GitHub and extract it.

### 3. Create `.env`

```powershell
Copy-Item .env.template .env
```

Open `.env`, set at least:

- `OPENAI_API_KEY` – personal test key.
- `ENCRYPTION_KEY` – generate via the PowerShell snippet in the template or ask a developer.
- Optional mail settings if you plan to test real inboxes.

Save and keep `.env` private.

### 4. Start the stack

```powershell
docker compose up --build
```

First boot downloads dependencies, OCR tools, and virus definitions—allow several minutes.

### 5. Log in

1. Visit <http://localhost:8080>.
2. Sign in with `admin@company.com` / `admin123`.
3. Change the password immediately via **Users → Edit**.

### 6. Smoke-test workflows

- Create a demo organisation and project.
- Upload a PDF/DOCX and track the status (Uploaded → Processing → Ready).
- Generate a draft in the composer; inspect retrieved context and token usage.
- Review the Usage Logs screen to confirm the action recorded.

### 7. Shut down

```powershell
docker compose down
```

Add `-v` only when you need to wipe volumes.

---

## Production launch

Deploying for real users involves extra safeguards. Coordinate with IT/security.

### 1. Infrastructure checklist

- Linux server (Ubuntu 22.04 LTS recommended), ≥4 vCPUs, 16 GB RAM, SSD storage.
- Hardened SSH (key-based access, restricted IP range, fail2ban).
- Public domain with DNS A/AAAA records pointing to the server.
- TLS certificate plan (Let’s Encrypt, managed proxy, or corporate CA).

### 2. Secure secrets

Store securely (vault/password manager):

- Production OpenAI API key with spending controls.
- Strong Postgres password.
- Unique Fernet encryption key per environment.
- OAuth client secrets for Gmail/Microsoft or IMAP credentials.
- Optional rerank API tokens.

### 3. Prepare `.env`

Key differences vs development:

- `FLASK_ENV=production`, `FLASK_DEBUG=0`.
- `TENANT_URL_SCHEME=https`, `TENANT_ENFORCE_HOST=true`.
- `PRIMARY_DOMAIN` = your public domain.
- Enable strict security once HTTPS works: `SECURITY_CSP_STRICT=true`, `ENABLE_HSTS=true`, `SESSION_COOKIE_SECURE=true`, `REMEMBER_COOKIE_SECURE=true`.
- Set `METRICS_DISABLED=false` if you want Prometheus scraping.
- Tune `CLAMAV_MAX_MB` and `CLAMAV_STARTUP_GRACE_SECS` for your file sizes and network speed.

Peer-review the file; most incidents stem from typos here.

### 4. Deploy

```bash
git clone https://github.com/YOUR_USERNAME/NexuSphere.git
cd NexuSphere
cp .env.template .env  # edit with production values
sudo docker compose up --build -d
```

Place a reverse proxy (Nginx, Caddy, Traefik) in front of the `web` container, forwarding HTTPS to port 8080. Sample config: `docker/nginx.conf`.

### 5. Hardening

- Change the superadmin password; create individual admin accounts.
- Enforce MFA for privileged roles.
- Verify antivirus by uploading the EICAR test string (should be blocked).
- Run smoke tests: upload a file, generate a draft, check logs.
- Set up backups for database and uploads.

### 6. Go-live readiness

- TLS certificate valid and auto-renewing.
- Email sending/OAuth flows succeed.
- Token budgets signed off by finance.
- On-call documentation includes log locations and escalation paths.

Roll out to a pilot team first, observe usage, then expand.

---

## Configuration reference

| Item | Location | Description |
|------|----------|-------------|
| `.env` | Repository root | Primary configuration: secrets, domains, toggles. Copy from `.env.template`, keep private. |
| `.env.template` | Repository root | Safe starter copy for new environments, no secrets. |
| `config/config.py` | `config/` | Python defaults with inline plain-language comments. Overrides via `.env`. |
| `docker-compose.yml` | Repository root | Defines containers, volumes, ports. Adjust for scaling or custom networks. |
| `docker/nginx.conf` | `docker/` | Example reverse proxy for TLS offloading. |

Frequently tuned variables:

- `OPENAI_RESPONSES_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`.
- `PACKAGE_TOKENS_PER_UNIT`, `PACKAGE_FRAGMENTS_PER_UNIT`.
- `CLAMAV_MAX_MB`, `CLAMAV_STARTUP_GRACE_SECS`.
- `METRICS_DISABLED` (set to `false` for `/metrics`).

When unsure, read the comment in `.env` and cross-check `config/config.py`.

---

## Routine operations

| Task | Frequency | Role | Notes |
|------|-----------|------|-------|
| Upload new knowledge documents | As policies change | Project operators | Add version notes in file detail view. |
| Review AI drafts | Daily | Project operators | Check context tab before sending. |
| Monitor usage vs limits | Weekly | Project admins | Export CSV from Usage Logs for finance. |
| Audit users & MFA | Monthly | Organisation admins | Remove dormant accounts, enforce MFA. |
| Check antivirus health | Weekly | Sysadmin | `/metrics` counters should show recent scans; signature age <7 days. |
| Test backups | Monthly | Sysadmin | Restore a dump to staging, confirm uploads sync. |
| Apply updates | Per release | DevOps | `git pull`, `docker compose build`, `docker compose up -d`. |

---

## Maintenance & security

### Backups

1. **Database** – nightly `pg_dump`, encrypted storage, retain ≥30 daily snapshots.
2. **Uploads** – sync `data/uploads/` to object storage (S3, Azure Blob) with lifecycle policies.
3. **Configuration** – store `.env` in a secure vault with change history.

### Monitoring

- Ship Docker logs to your logging stack (Loki, ELK, Splunk).
- Use `/metrics` (if enabled) for ClamAV counters (`av_scan_total`, `av_scan_error_total`, etc.).
- Watch host CPU, RAM, and disk utilisation; vector indexes grow with document volume.

### Security practices

- Restrict SSH to trusted IPs and use key-based auth.
- Enforce MFA for admin accounts.
- Keep host OS patched; track advisories for Docker, OpenAI, and dependencies.
- Document incident response for AI outages, database failures, and malware detections.

---

## Operational cost & capacity planning

### Example monthly operating cost (benchmark)

These figures come from the reference deployment used during earlier pilots. Adjust them to match your hosting region and currency.

| Item | Estimate | Notes |
|------|----------|-------|
| Application server (8 vCPU, 32 GB RAM, 240 GB SSD) | €50–€80 / month | Managed Hetzner instance; add ~€10 / month for automated backups. |
| CDN / WAF (Cloudflare Pro) | $20 / month | Provides TLS, caching, and DDoS shielding. |
| Domain registration | 200 PLN / year | Approx. $45 / year; renew annually. |
| Shared email provider | 900 PLN / year | For transactional inbox; replace with your enterprise plan if different. |
| Qualified signature certificate (Autenti) | 219 PLN / year | Required when legally binding signatures are needed. |
| Autenti platform subscription | 90 PLN / month | Skip if your organisation uses another signature provider. |

**Baseline total:** ≈ 533 PLN per month (excluding OpenAI usage). Add OpenAI spend separately—current contracts allow up to €4 per 2 million tokens per package. Larger compliance projects (for example ISO 27001 certification) typically require a one-time budget of ~30 000 PLN; subsidies may be available depending on jurisdiction.

### Database storage planning

Keep at least 1 GB of free space in the PostgreSQL volume for every 100 active projects. The table below summarises the yearly growth observed in production-like tests.

**Assumptions:**

- 100 projects × 200 processed emails per month × 12 months = 240 000 emails per year.
- Each email generates one record in `generation_history` and one record in `ai_usage_log` (embedding ingestion logs are excluded).
- Average text fields (titles, filenames) stay reasonably short.

**Storage estimate (PostgreSQL):**

- Single log entry (row + overhead + indexes): 0.8–1.2 KB.
- 240 000 records ≈ 190–290 MB per table.
- Two main tables ⇒ ~380–580 MB per year.

Plan for a 20–30 % safety buffer to accommodate VACUUM overhead and unusually long titles. In practice, keep 0.5–0.8 GB of free space per year of log retention, per 100 projects. Increase the allocation if you retain logs beyond 12 months or ingest large attachments into metadata.

---

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---------|--------------|------------|
| `ModuleNotFoundError` during startup | Dependency download interrupted. | `docker compose build --no-cache`, then restart. |
| Draft fails with 401/403 | OpenAI key invalid or quota exceeded. | Update `OPENAI_API_KEY`, check OpenAI dashboard, set budgets. |
| File stuck in `Processing` | OCR job failed or rate limit hit. | `docker compose logs -f web`, retry from file detail page. |
| Antivirus blocks clean file | `CLAMAV_MAX_MB` too low or signatures outdated. | Raise limit slightly, ensure signatures refreshed. |
| Users loop back to login | Domain/HTTPS mismatch. | Verify `PRIMARY_DOMAIN`, `TENANT_URL_SCHEME`, and proxy headers (`X-Forwarded-Proto`). |
| Gmail/Outlook integration errors | OAuth redirect or scope mismatch. | Reconfigure organisation OAuth settings, re-authorise. |
| Draft generation slow | Too many multi-query variants or large context window. | Tune retrieval settings, enable rerank to reduce fragments. |
| `docker compose up` exits instantly | Required env variable missing. | Check terminal output; Docker prints the variable name. |

Capture relevant logs (`docker compose logs web`, `docker compose logs clamav`) before opening support tickets.

---

## FAQ

**Do we need the OpenAI key for search-only mode?** Yes—multi-query generation still uses the Responses API.

**Can ClamAV be disabled locally?** Yes, set `CLAMAV_ENABLED=false` in `.env`. Re-enable before production.

**How do we switch models?** Change `OPENAI_RESPONSES_MODEL` (and related settings) in `.env`. Verify the model supports the Responses API and fits your budget.

**Where are logs stored?** Application logs stream from the `web` container; audit trails live in PostgreSQL tables such as `ai_usage_log`, `generation_history`, and `file_processing_log`.

**Can we use another vector database?** Not without development work. NexuSphere is tightly integrated with Qdrant.

**How many tokens are in a package?** Default: 2 000 tokens and 2 000 fragments per package. Adjust `PACKAGE_*` settings to match your commercial offer.

---

## Support channels

- **First line** – organisation's NexuSphere champion or IT helpdesk.
- **Engineering** – open a GitHub issue with timestamps, logs, and reproduction steps.
- **Emergency** – capture `docker compose logs`, note recent changes, and escalate through the agreed on-call process.

---

## Appendix A – Glossary

| Term | Definition |
|------|------------|
| **AI usage log** | Record of prompt and completion token counts for each AI call. |
| **Embedding** | Numeric representation of text used for semantic search. |
| **Fernet key** | 32-byte base64 string used to encrypt sensitive data. |
| **Package** | Commercial bundle of monthly AI usage and fragment capacity. |
| **Project** | Container for knowledge, mailboxes, and users inside an organisation. |
| **Qdrant** | Vector database storing embeddings and metadata. |
| **Rerank** | Secondary model that reorders search results by relevance. |
| **WSGI** | Interface Flask uses to talk to production web servers. |

Document local policy decisions (custom limits, proxy tweaks) in your internal wiki so future operators understand why values differ from this manual. With this guide and the inline comments in `.env` and `config/config.py`, even non-technical staff can operate NexuSphere confidently.
