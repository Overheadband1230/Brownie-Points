"""API-level lifecycle tests via TestClient (spec §12 acceptance criteria)."""


def register(client, email, name, password="password1"):
    r = client.post("/api/auth/register",
                    json={"email": email, "display_name": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def login(client, email, password="password1"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def me(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    return r.json()


def test_register_login_me(client):
    data = register(client, "alice@example.com", "Alice")
    assert data["is_admin"] is True  # first user is admin

    data = register(client, "bob@example.com", "Bob")
    assert data["is_admin"] is False

    # bob is now logged in via the register cookie
    info = me(client)
    assert info["display_name"] == "Bob"
    assert info["spendable_balance"] == 0

    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401

    login(client, "alice@example.com")
    assert me(client)["display_name"] == "Alice"


def test_full_redemption_lifecycle(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    # Alice awards Bob 5 BP.
    login(client, "alice@example.com")
    r = client.post("/api/awards", json={
        "to_user_id": 2, "amount": 5, "reason": "airport run", "category": "favor"})
    assert r.status_code == 201, r.text

    login(client, "bob@example.com")
    assert me(client)["spendable_balance"] == 5

    # Bob creates a redemption — points held immediately.
    r = client.post("/api/redemptions", json={
        "grantor_id": 1, "amount": 3, "reason": "make me dinner", "category": "favor"})
    assert r.status_code == 201, r.text
    redemption_id = r.json()["id"]
    info = me(client)
    assert info["spendable_balance"] == 2
    assert info["held"] == 3

    # Bob can't approve his own request.
    assert client.post(f"/api/redemptions/{redemption_id}/approve").status_code == 403

    # Alice approves and fulfills.
    login(client, "alice@example.com")
    assert client.post(f"/api/redemptions/{redemption_id}/approve").status_code == 200
    assert client.post(f"/api/redemptions/{redemption_id}/fulfill").status_code == 200

    # Bob's ledger shows a permanent debit; balances reconcile.
    login(client, "bob@example.com")
    info = me(client)
    assert info["spendable_balance"] == 2
    assert info["held"] == 0

    entries = client.get("/api/ledger").json()["entries"]
    types = [e["entry_type"] for e in entries]
    assert types == ["DEBIT", "HOLD", "AWARD"]  # newest first
    assert sum(e["amount"] for e in entries if e["entry_type"] != "HOLD") == 2

    statuses = {r["id"]: r["status"] for r in client.get("/api/redemptions").json()}
    assert statuses[redemption_id] == "FULFILLED"


def test_deny_and_cancel_release_points(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    client.post("/api/awards", json={"to_user_id": 2, "amount": 10, "reason": "gift"})

    login(client, "bob@example.com")
    r1 = client.post("/api/redemptions", json={
        "grantor_id": 1, "amount": 4, "reason": "one"}).json()
    r2 = client.post("/api/redemptions", json={
        "grantor_id": 1, "amount": 4, "reason": "two"}).json()
    assert me(client)["spendable_balance"] == 2

    # Bob cancels his own request.
    assert client.post(f"/api/redemptions/{r1['id']}/cancel").status_code == 200
    assert me(client)["spendable_balance"] == 6

    # Alice denies the other.
    login(client, "alice@example.com")
    assert client.post(f"/api/redemptions/{r2['id']}/deny").status_code == 200

    login(client, "bob@example.com")
    assert me(client)["spendable_balance"] == 10


def test_validation_errors(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    # Self-award.
    assert client.post("/api/awards", json={
        "to_user_id": 1, "amount": 1, "reason": "me"}).status_code == 400
    # Non-positive amount.
    assert client.post("/api/awards", json={
        "to_user_id": 2, "amount": 0, "reason": "zero"}).status_code == 400
    # Overspend.
    assert client.post("/api/redemptions", json={
        "grantor_id": 2, "amount": 99, "reason": "greed"}).status_code == 400
    # Unauthenticated mutation.
    client.cookies.clear()
    assert client.post("/api/awards", json={
        "to_user_id": 2, "amount": 1, "reason": "x"}).status_code == 401


def test_admin_adjustments_api(client):
    register(client, "alice@example.com", "Alice")  # admin
    register(client, "bob@example.com", "Bob")

    # Bob (not admin) is rejected.
    assert client.post("/api/adjustments", json={
        "user_id": 1, "amount": 5, "reason": "sneaky"}).status_code == 403

    login(client, "alice@example.com")
    assert client.post("/api/adjustments", json={
        "user_id": 2, "amount": 5, "reason": "grant"}).status_code == 201

    login(client, "bob@example.com")
    assert me(client)["spendable_balance"] == 5


def test_reports_summary(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    client.post("/api/awards", json={
        "to_user_id": 2, "amount": 5, "reason": "x", "category": "chore"})

    login(client, "bob@example.com")
    r = client.post("/api/redemptions", json={"grantor_id": 1, "amount": 2, "reason": "y"}).json()
    login(client, "alice@example.com")
    client.post(f"/api/redemptions/{r['id']}/approve")
    client.post(f"/api/redemptions/{r['id']}/fulfill")

    login(client, "bob@example.com")
    data = client.get("/api/reports/summary").json()
    assert data["totals"]["awarded"] == 5
    assert data["totals"]["spent"] == 2
    assert data["totals"]["spendable"] == 3
    assert data["by_category"] == {"chore": 5}
    assert data["points_over_time"][-1]["balance"] == 3
    assert data["net_by_counterparty"] == [
        {"user_id": 1, "display_name": "Alice", "net": 3}]


def test_items_api_lifecycle(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    # Alice lists an item.
    login(client, "alice@example.com")
    r = client.post("/api/items", json={"name": "back massage", "price": 3, "category": "favor"})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    client.post("/api/awards", json={"to_user_id": 2, "amount": 5, "reason": "gift"})

    # Bob sees it on the menu and redeems it — lands as APPROVED with points held.
    login(client, "bob@example.com")
    menu = client.get("/api/items").json()
    assert [(i["name"], i["price"], i["owner_name"]) for i in menu] == [("back massage", 3, "Alice")]

    r = client.post(f"/api/items/{item_id}/redeem")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "APPROVED"
    info = me(client)
    assert info["spendable_balance"] == 2
    assert info["held"] == 3

    # Only the owner can delist.
    assert client.post(f"/api/items/{item_id}/delist").status_code == 403
    login(client, "alice@example.com")
    assert client.post(f"/api/items/{item_id}/delist").status_code == 200
    assert client.get("/api/items").json() == []

    # Fulfill completes the exchange.
    redemption_id = client.get("/api/redemptions").json()[0]["id"]
    assert client.post(f"/api/redemptions/{redemption_id}/fulfill").status_code == 200
    login(client, "bob@example.com")
    assert me(client)["spendable_balance"] == 2
    assert me(client)["held"] == 0


def test_categories_seeded(client):
    assert set(client.get("/api/categories").json()) == {
        "favor", "chore", "apology", "gift", "bet", "other"}
