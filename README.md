# Akamai inference + transcript embeddings + PostgreSQL — drop-in package

This package is **new integration code** for your team's repo. It is **not a clone/fork of Akamai's workshop**, and it does not include live Muse, phone, Jev, authentication, or authorization integrations.

Reference: https://github.com/akamai-developers/akamai-workshop-hackmidwest

## What works

- One Akamai `hackathon-chat` call to extract business-request details from **untrusted text** (JSON output parsed/validated; never authorizes actions).
- One Akamai `hackathon-embedding` call that returns a 1,024-dimensional vector.
- PostgreSQL/pgvector tables for businesses, policies, interactions, transcript segments, and embeddings (plus governance/audit tables).
- Business-scoped insert, transcript retrieval, and vector similarity search. Writes are transactionally saved.
- Mocked automated tests that run **without API keys or a database**.

## Copy into your own repository

Copy `backend/` and the `.env.example` template into your existing Python project. Keep your existing main app, Jev integration, and frontend. **Do not overwrite your own files.**

Expected relative structure:

```
your-repo/
  backend/
    services/akamai.py
    database/repository.py
    database/migrations/002_governance.sql
    scripts/{init_db.py,demo.py}
  tests/
  requirements.txt
  .env.example
```

If your repo already has a `backend` package, merge the directories. If you have already applied the previous `002_governance.sql`, you can skip applying it again (this version is safe to re-run for schema creation, but does not handle schema upgrades).

## Setup

From your repo root (Python 3.11+):

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows PowerShell: Copy-Item .env.example .env
```

Replace `OPENAI_API_KEY` with the team key from the hackathon organizers and `DATABASE_URL` with your PostgreSQL connection string. Never commit `.env` to git. The Akamai inference key and database credentials are separate.

Provision PostgreSQL with the `vector` extension available, then initialize the schema:

```bash
python -m backend.scripts.init_db
```

This `002_governance.sql` is based on your previously proposed governance schema, independent from Akamai's demo sessions/documents tables. If you use **both** this package and the Akamai starter's `001_init.sql`, apply `001_init.sql` first.

Run the actual end-to-end smoke test:

```bash
python -m backend.scripts.demo
```

Expected ending (only when live Akamai and PostgreSQL credentials work):

```
Embedding length: 1024
SUCCESS: model call, embedding, PostgreSQL save/get/vector search
```

Automated unit tests (fake Akamai responses, fake PostgreSQL cursor):

```bash
python -m pytest -q
```

## Use the modules inside your existing gateway

```python
from backend.services.akamai import AkamaiService
from backend.database.repository import TranscriptRepository

akamai = AkamaiService()
repo = TranscriptRepository()

# Your trusted service obtains `business_id`, `interaction_id` from authenticated context,
# never from an unauthenticated external agent.
text = "Two lunch combos for pickup, total 40 dollars."
proposal = akamai.analyze_transcript(text)
embedding = akamai.embed_transcript(text)
segment_id = repo.save_segment(business_id, interaction_id, text, embedding)

# NEXT: your trusted Jev risk evaluation + deterministic policy engine.
# Do not execute orders based on `proposal` alone.
```

## Known limitations / before accepting real callers

- **Requires live team credentials** for actual inference/database smoke tests. Unit tests only verify integration behavior with mocks.
- Transcripts can include private information: obtain legally appropriate notice/consent, redact PII, establish retention and access controls, and avoid storing payment card data.
- `create_demo_business` exists for CLI demonstrations only; do not expose to external callers.
- Use authenticated service-to-service routes and authenticated business owners. This package does not implement tenant authentication or phone webhook verification. Passing a `business_id` argument is not itself authentication; enforce isolation at the API/service boundary and consider PostgreSQL row-level security.
- `embedding_model` must match the model used to generate the vectors; existing data from different models may not be comparable.
- Identifying prompt injection / malicious callers requires Jev/behavioral screening and rule checks; vector similarity is context, not proof of risk.
- Gateway must enforce authorization before business tool execution. This package deliberately does not execute any POS, scheduling, or refund actions.
- The package is a template because **your team's GitHub repo URL/files were not provided**; it has not been merged into their branch.
