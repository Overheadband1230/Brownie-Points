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

## Run it on Unraid

The stack is compose-based, so the **Compose Manager** plugin is the smoothest path.

**No-clone option (public repo):** Docker can build straight from the GitHub URL, so the server never needs the source. Install Compose Manager, add a new stack, and paste in the contents of [`docker-compose.unraid.yml`](docker-compose.unraid.yml) — its `build:` points at `https://github.com/Overheadband1230/Brownie-Points.git#main`. Set `SECRET_KEY`/`INVITE_CODE` in the stack's `.env`, Compose Up, done. To update after pushing new code, just **Compose Down → Compose Up (with build)** — it re-fetches `main`. This requires the repo to be public; for a private repo use the clone route below.

**Clone route (works for private repos):**

1. **Apps** tab → install **Compose Manager** (Community Applications).
2. Get this repo onto the server at `/mnt/user/appdata/brownie-points` — either `git clone` over SSH, or copy the folder across an SMB share.
3. Create `/mnt/user/appdata/brownie-points/.env`:
   ```
   SECRET_KEY=some-long-random-string
   INVITE_CODE=your-invite-code
   ```
4. **Docker** tab → Compose section → **Add New Stack**, point it at that folder, then **Compose Up**. The first start builds the image (a minute or two).
5. Open `http://<unraid-ip>:8000` and register — the first account becomes admin.

The database is a single SQLite file at `/mnt/user/appdata/brownie-points/data/brownie.db`. Backing up means copying that one file; appdata backup plugins catch it automatically. To migrate existing data from another machine, drop its `brownie.db` there before first start.

<details>
<summary>Alternative: plain Docker tab, no plugin</summary>

Unraid's built-in Docker tab can't build from a Dockerfile, so build once over SSH:

```bash
cd /mnt/user/appdata/brownie-points
docker build -t brownie-points .
```

Then **Docker** tab → **Add Container**: Repository `brownie-points`, port `8000 → 8000`, path `/data` → `/mnt/user/appdata/brownie-points/data`, and env variables `DATABASE_URL=sqlite:////data/brownie.db`, `SECRET_KEY`, `INVITE_CODE`. Re-run the build command whenever you pull new code.
</details>

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
