# Brownie Points — Design & Build Spec

> A build specification for **Fable** (or any developer/AI agent) to implement.
> Everything needed to go from empty repo → running app is in this document.
> Target experience for the operator (Caleb): clone the repo, run `docker compose up`, open a browser. That's it.

---

## 1. The Story (why this exists)

A friend once told Caleb she was awarding him a **Brownie Point** — a single, spendable token of goodwill, redeemable for a favor at a time of his choosing. It started as a joke. This app makes the joke real and a little bit ridiculous: a tiny economy where people award each other Brownie Points and later cash them in.

Keep the tone light throughout the UI. Copy should be playful ("You've been awarded a Brownie Point 🍫", "Spend wisely"), not corporate.

---

## 2. Scope & Decisions (locked)

These were decided up front — build to them, don't re-litigate:

| Decision | Choice |
|---|---|
| Users | **Multi-user with accounts.** Anyone can sign up and transact with anyone else. |
| Auth | Email + password, session-cookie based. |
| Accounting features | **All four:** running balance + ledger, redemption requests with approval, reasons/categories, and reports/stats. |
| Tech stack | **Recommended below** (chosen for single-container simplicity). |
| Deployment | **One Docker container**, `docker compose up`, SQLite file persisted to a volume. Near-zero setup. |

---

## 3. Recommended Tech Stack

Chosen so the entire app runs as **one container** with **no external database, no build servers, and no config files to hand-edit**.

- **Backend:** Python 3.12 + **FastAPI** (async, tiny, great docs).
- **Data layer:** **SQLAlchemy 2.x** ORM + **SQLite** (single file, zero setup). Migrations via **Alembic** (optional; SQLite auto-create-on-boot is fine for v1).
- **Auth:** session cookies via `itsdangerous`-signed cookies or `fastapi-login`; passwords hashed with **passlib[bcrypt]**.
- **Frontend:** Server-rendered **Jinja2** templates + **htmx** (for live updates without a JS build step) + **Tailwind CSS via CDN** (no build pipeline). This keeps everything in the one Python container — no Node, no webpack.
- **Charts (reports page):** **Chart.js** via CDN.
- **Server:** **uvicorn** (single process; fine for a two-to-few-person app).
- **Container:** one `Dockerfile`, one `docker-compose.yml`, one named volume for the SQLite file.

> Why not React/Next? It would add a Node build step and usually a second container. For a small friends-and-family app, server-rendered + htmx is dramatically less to set up and maintain while still feeling live. If Caleb later wants a fancy SPA, the API is clean enough to bolt one on.

---

## 4. Core Concepts & Rules

**Brownie Point (BP):** the unit of currency. Whole numbers only (no fractions).

**Award:** User A gives User B some number of BP with a reason and optional category. This immediately increases B's balance. Awards are final (no take-backs) — but see *Adjustments* for corrections.

**Balance:** For each user, `balance = total awarded to them − total spent by them`. Balances are **per-user global**, not per-relationship, for v1. (A "who owes whom" breakdown is shown in reports but the spendable balance is a single number.)

**Redemption (Spend):** User B "spends" BP by creating a **Redemption Request** aimed at a specific user (usually whoever owes/awarded them) describing what they want in exchange. It has a lifecycle:

```
PENDING ──approve──▶ APPROVED ──mark fulfilled──▶ FULFILLED
   │
   └──deny──▶ DENIED        (any party may CANCEL while PENDING)
```

- Points are **held (escrowed)** when a request is created — they leave the spendable balance immediately so a user can't double-spend the same points across two pending requests.
- On **APPROVED → FULFILLED**, the hold becomes a permanent debit.
- On **DENIED** or **CANCELLED**, the hold is released back to the spendable balance.

**Reason & Category:** Every award and every redemption carries a free-text `reason` and an optional `category` (from a small preset list, editable): `favor`, `chore`, `apology`, `gift`, `bet`, `other`.

**Adjustment (admin correction):** A manual ledger entry to fix mistakes. Recorded transparently in the ledger; never silently edits history.

**Ledger:** append-only log of every event (award, hold, release, debit, adjustment). Balances are always **derived from the ledger**, never stored as a mutable field — this is the "accounting system" done right (single source of truth, fully auditable).

---

## 5. Data Model

Use these tables. All timestamps UTC. All money-like values are integers.

### `users`
| column | type | notes |
|---|---|---|
| id | int PK | |
| email | text unique | login |
| display_name | text | shown in UI |
| password_hash | text | bcrypt |
| is_admin | bool | first registered user = admin; can create adjustments |
| created_at | datetime | |

### `redemptions`
| column | type | notes |
|---|---|---|
| id | int PK | |
| requester_id | int FK→users | the person spending |
| grantor_id | int FK→users | the person being asked to honor it |
| amount | int | BP being spent (>0) |
| reason | text | what they want in return |
| category | text | nullable |
| status | text | PENDING / APPROVED / DENIED / CANCELLED / FULFILLED |
| created_at | datetime | |
| resolved_at | datetime | nullable |

### `ledger_entries` (append-only — the source of truth)
| column | type | notes |
|---|---|---|
| id | int PK | |
| user_id | int FK→users | whose balance this line affects |
| counterparty_id | int FK→users | nullable; the other side of the transaction |
| entry_type | text | `AWARD`, `HOLD`, `RELEASE`, `DEBIT`, `ADJUSTMENT` |
| amount | int | signed: +credits, −debits/holds |
| reason | text | |
| category | text | nullable |
| redemption_id | int FK→redemptions | nullable; links holds/debits to a request |
| created_by | int FK→users | who caused this entry |
| created_at | datetime | |

**Derived balances (compute, don't store):**
```
awarded_in   = Σ AWARD amounts where user_id = U            (+)
adjustments  = Σ ADJUSTMENT amounts where user_id = U       (±)
held         = Σ |HOLD| currently active for U              (−, PENDING requests)
spent        = Σ DEBIT amounts where user_id = U            (−, FULFILLED)
spendable_balance = awarded_in + adjustments − spent − held
lifetime_earned   = awarded_in + max(adjustments,0)
```

### `categories` (optional lookup, seed with defaults)
`id, name` — seed: favor, chore, apology, gift, bet, other.

---

## 6. Transaction Logic (the important part)

Implement these as service functions inside a DB transaction so the ledger stays consistent.

**Award(from, to, amount, reason, category):**
1. Validate `amount > 0`, `from ≠ to`.
2. Insert one `AWARD` ledger entry: `user_id=to`, `counterparty_id=from`, `amount=+amount`.
3. (No balance field to update — it's derived.)

**CreateRedemption(requester, grantor, amount, reason, category):**
1. Validate `amount > 0`, requester ≠ grantor, and `requester.spendable_balance ≥ amount`.
2. Insert `redemptions` row status=PENDING.
3. Insert `HOLD` ledger entry: `user_id=requester`, `amount=−amount`, `redemption_id=...`. (This is the escrow.)

**ApproveRedemption(id, by=grantor):** set status APPROVED, `resolved_at=now`. No ledger change yet (still held).

**FulfillRedemption(id):** requires APPROVED. Insert `DEBIT` `user_id=requester amount=0`? — No. Cleaner: on fulfill, **convert the hold to a debit**: insert a `RELEASE` (+amount) to cancel the hold and a `DEBIT` (−amount) as the permanent spend, OR simply mark the existing HOLD as settled and add a `DEBIT`. Recommended: keep HOLD as-is and add a `DEBIT` only if you *don't* count HOLDs in `spent`. To avoid double-counting, define the derived formula as: HOLD affects balance only while its redemption is PENDING or APPROVED; once FULFILLED, ignore the HOLD and count the DEBIT. Simplest robust rule below.

> **Recommended simplest model:** treat the **HOLD** as the thing that reduces spendable balance for PENDING and APPROVED requests. On **FULFILLED**, insert a `DEBIT` and mark the redemption FULFILLED; compute `held` as Σ HOLD where the linked redemption is in (PENDING, APPROVED), and `spent` as Σ DEBIT. This way holds and debits never overlap. On **DENIED/CANCELLED**, insert a `RELEASE` (+amount) and the hold no longer counts. Pick one convention and document it in code comments.

**DenyRedemption / CancelRedemption(id):** set status, insert `RELEASE` `+amount` for requester, `resolved_at=now`.

**Adjustment(admin, target_user, amount, reason):** insert `ADJUSTMENT` entry with signed amount. Admin only.

Always recompute and never trust a cached number. If performance ever matters (it won't at this scale), add a materialized balance later.

---

## 7. API (REST, JSON)

All under `/api`. Auth via session cookie. Return proper 4xx on validation failures.

```
POST   /api/auth/register        {email, display_name, password}
POST   /api/auth/login           {email, password}
POST   /api/auth/logout
GET    /api/me                    → current user + spendable_balance + lifetime_earned

GET    /api/users                 → list users (id, display_name, balance summary)

POST   /api/awards                {to_user_id, amount, reason, category}
GET    /api/awards                → awards involving me (given + received)

POST   /api/redemptions           {grantor_id, amount, reason, category}
GET    /api/redemptions           → mine (as requester and as grantor), filterable by status
POST   /api/redemptions/{id}/approve
POST   /api/redemptions/{id}/deny
POST   /api/redemptions/{id}/cancel
POST   /api/redemptions/{id}/fulfill

GET    /api/ledger                → my ledger entries (paginated)
GET    /api/reports/summary       → totals, balances, points over time, per-counterparty net

POST   /api/adjustments           {user_id, amount, reason}   (admin only)
GET    /api/categories
```

For htmx, also expose server-rendered fragment routes (e.g. `GET /partials/ledger`) that return HTML snippets. Keep the JSON API too so a future SPA/mobile app is possible.

---

## 8. Pages / UI

Server-rendered, Tailwind-styled, playful copy. Mobile-friendly (friends will check it on phones).

1. **Login / Register** — one combined page, tabbed.
2. **Dashboard (home)** — big **spendable balance** number, lifetime earned, quick actions: *Award a point*, *Spend a point*. Recent activity feed (from ledger). Pending items needing my action (redemptions awaiting my approval) shown as a badge/list.
3. **Award** — pick a user, amount, reason, category → submit. Confirmation with confetti-ish 🍫 flair.
4. **Spend / Redemptions** — create a request; list of my requests with statuses; list of requests awaiting my approval with Approve/Deny buttons; Fulfill button when approved. Status chips color-coded.
5. **Ledger** — full paginated table of my entries: date, type, counterparty, amount (color-coded ±), reason, category. Filter by type/category/date.
6. **Reports** — Chart.js: balance over time (line), points by category (doughnut), and a small "who owes whom" table (net BP per counterparty). Totals: awarded, spent, held, net.
7. **Admin** (admin users only) — user list, create adjustment, view any user's ledger.

Nav: Dashboard · Award · Spend · Ledger · Reports · (Admin) · Logout.

---

## 9. Docker Setup (must be truly one-command)

The whole point: Caleb runs `docker compose up` and it works.

**`Dockerfile`**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# DB auto-creates on boot if missing; no manual migration step for v1.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**`docker-compose.yml`**
```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - brownie_data:/data          # SQLite file lives here, survives restarts
    environment:
      - DATABASE_URL=sqlite:////data/brownie.db
      - SECRET_KEY=change-me-to-a-random-string   # used to sign session cookies
    restart: unless-stopped

volumes:
  brownie_data:
```

**`requirements.txt`** (starting point)
```
fastapi
uvicorn[standard]
sqlalchemy
passlib[bcrypt]
itsdangerous
jinja2
python-multipart
```

**On first boot:** app creates tables if absent, seeds default categories. First user to register becomes admin. No shell steps, no seeding commands.

**`.env` (optional):** compose reads a `.env` for `SECRET_KEY` if present; otherwise the default works for local use. Tell Caleb to change `SECRET_KEY` before exposing it beyond localhost.

---

## 10. Security Notes (small but don't skip)

- Hash passwords with bcrypt; never store plaintext.
- Sign session cookies with `SECRET_KEY`; set `HttpOnly`, `SameSite=Lax`, and `Secure` when served over HTTPS.
- Server-side authorization on every mutating endpoint (e.g., only the grantor can approve; only the requester can cancel; only admins can adjust).
- Validate amounts are positive integers; reject self-awards/self-redemptions.
- Rate-limit auth endpoints lightly (optional for v1).
- This is a friends app, not a bank — but the ledger being append-only means mistakes are always recoverable and auditable.

---

## 11. Suggested Build Order (for Fable)

1. Project skeleton: FastAPI app, config, SQLAlchemy engine pointing at `DATABASE_URL`, table auto-create on startup, seed categories.
2. Auth: register/login/logout, session cookie, `GET /api/me`, first-user-is-admin.
3. Ledger + balance service functions (Section 6) with unit tests for the derived-balance math.
4. Awards (API + Award page).
5. Redemptions full lifecycle (API + Spend page), including hold/release/debit ledger effects.
6. Dashboard pulling balance + activity + pending approvals.
7. Ledger page with filters.
8. Reports page + Chart.js.
9. Admin page + adjustments.
10. Polish: playful copy, 🍫 flair, mobile layout, empty states.
11. Dockerfile + compose; confirm `docker compose up` boots to a working login page on `http://localhost:8000` with an empty DB.

---

## 12. Acceptance Criteria

- `docker compose up` from a fresh clone yields a working app at `localhost:8000` with no extra steps.
- Two users can register; user A awards user B points; B's balance updates.
- B creates a redemption; the held amount leaves B's spendable balance immediately.
- A approves and marks fulfilled; B's ledger shows a permanent debit; balances reconcile.
- Denying/cancelling a pending redemption returns the held points.
- Every balance shown in the UI equals the sum of that user's ledger entries (no drift).
- Reports page renders balance-over-time and category charts.
- Restarting the container preserves all data (volume works).

---

## 13. Nice-to-haves (explicitly out of scope for v1)

Email notifications, per-relationship balances/currencies, "interest" jokes, leaderboards, avatars, a real SPA/mobile app, OAuth login, multi-currency (e.g., "Cookie Points"), gifting points you don't have (credit/IOU). Note these so Fable leaves clean seams but doesn't build them yet.
