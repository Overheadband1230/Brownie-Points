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

### Networking: Custom: br0 instead of bridge

Both compose files attach the container to **`br0`** (macvlan) rather than Docker's default bridge network. This gives the container its own IP directly on your LAN — useful if a reverse proxy (e.g. Nginx Proxy Manager) in a separate stack was getting 502s because it couldn't route into Docker's isolated bridge network.

A few things that are specific to this mode:

- **`ports:` doesn't apply.** Macvlan containers bypass the host's network stack, so there's no `8000:8000` mapping — the app is reachable directly at `http://<container-ip>:8000`.
- **`br0` must already exist** as a Docker network on the host. Unraid creates it automatically the first time *any* container is set to use "Custom: br0" — if you've never done that before, create one throwaway container that way first (Docker tab → Add Container → Network Type: `Custom: br0`), or set it under **Settings → Docker → Docker Custom Network Type**. If the network genuinely doesn't exist yet, `docker compose up` will fail with `network br0 declared as external, but could not be found`.
- **No static IP is set**, so the container gets a DHCP-leased address from your router. After the first `Compose Up`, find it with:
  ```bash
  docker inspect brownie-points-web-1 --format '{{.NetworkSettings.Networks.br0.IPAddress}}'
  ```
  (or check the Docker tab — the container's IP is shown under its icon). Point Nginx Proxy Manager's "Forward Hostname / IP" at that address, port `8000`. Since it's DHCP, the address *can* change if the container is recreated — if that becomes annoying, reserve the container's MAC address a fixed lease in your router, or switch to a static IP by adding `ipv4_address: 192.168.1.50` (pick an address outside your DHCP range) under `networks: br0:` in the compose file.
- **The Unraid host itself may not be able to reach the container** by that IP — this is a known macvlan quirk (the host and a macvlan container on the same physical interface can't talk to each other by default). It doesn't affect other *containers* or devices on the LAN reaching it (including NPM, if NPM is also a container). If you need host-to-container access too (e.g. for local troubleshooting from Unraid's own terminal), enable **Settings → Docker → Host access to custom networks**.

<details>
<summary>Alternative: plain Docker tab, no plugin</summary>

Unraid's built-in Docker tab can't build from a Dockerfile, so build once over SSH:

```bash
cd /mnt/user/appdata/brownie-points
docker build -t brownie-points .
```

Then **Docker** tab → **Add Container**: Repository `brownie-points`, Network Type `Custom: br0` (or `Bridge` with port `8000 → 8000`, if you don't need the macvlan setup described above), path `/data` → `/mnt/user/appdata/brownie-points/data`, and env variables `DATABASE_URL=sqlite:////data/brownie.db`, `SECRET_KEY`, `INVITE_CODE`. Re-run the build command whenever you pull new code.
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
