# Brownie Points 🍫

A tiny economy of goodwill. Friends award each other **Brownie Points** — single, spendable tokens of gratitude — and later cash them in for favors. It started as a joke. Now it has an append-only ledger.

## Run it (one command)

```bash
docker compose up
```

Open **http://localhost:8000**. That's it — the database creates itself on first boot, and the **first person to register becomes the admin**. Data survives restarts (it lives in a Docker volume).

Signing up requires an **invite code** (default: `brownie-batch`) — share it with the friends you want in your economy. Before exposing the app beyond localhost, copy `.env.example` to `.env` and set a real `SECRET_KEY` and your own `INVITE_CODE`.

## How it works

- **Award** points to a friend with a reason ("drove me to the airport at 5am") and a category.
- **Spend** points by requesting a favor from someone. Points go on hold immediately (no double-spending), and the request moves through `PENDING → APPROVED → FULFILLED` (or gets denied/cancelled, returning the points).
- **Ledger**: every event is an append-only entry; balances are always derived from the ledger, never stored. Fully auditable, mistakes always recoverable.
- **Reports**: balance over time, points by category, and who owes whom.
- **Admin** can make transparent ledger adjustments to fix mistakes.

## Run without Docker (development)

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Uses a local `brownie.db` SQLite file by default. Run the tests with:

```bash
pytest
```

## Stack

FastAPI · SQLAlchemy 2 · SQLite · Jinja2 + htmx · Tailwind (CDN) · Chart.js — one container, no build step, no external database.
