"""API-level lifecycle tests via TestClient (spec §12 acceptance criteria)."""


def register(client, email, name, password="password1", invite_code="test-invite"):
    r = client.post("/api/auth/register",
                    json={"email": email, "display_name": name, "password": password,
                          "invite_code": invite_code})
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


def test_register_requires_invite_code(client):
    r = client.post("/api/auth/register", json={
        "email": "x@example.com", "display_name": "X", "password": "password1"})
    assert r.status_code == 400
    assert "invite code" in r.json()["detail"].lower()

    r = client.post("/api/auth/register", json={
        "email": "x@example.com", "display_name": "X", "password": "password1",
        "invite_code": "wrong"})
    assert r.status_code == 400

    register(client, "x@example.com", "X")  # correct code works


def test_account_settings(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")  # logged in as Bob

    # Wrong current password rejected.
    r = client.post("/api/me/account", json={
        "email": "bob2@example.com", "display_name": "Bobby", "current_password": "nope"})
    assert r.status_code == 400

    # Can't take someone else's email.
    r = client.post("/api/me/account", json={
        "email": "alice@example.com", "display_name": "Bobby", "current_password": "password1"})
    assert r.status_code == 400

    # Valid update changes email + display name; session stays valid.
    r = client.post("/api/me/account", json={
        "email": "bob2@example.com", "display_name": "Bobby", "current_password": "password1"})
    assert r.status_code == 200
    info = me(client)
    assert info["email"] == "bob2@example.com"
    assert info["display_name"] == "Bobby"

    # Password change: wrong current rejected, then works; new login OK.
    assert client.post("/api/me/password", json={
        "current_password": "nope", "new_password": "newpassword1"}).status_code == 400
    assert client.post("/api/me/password", json={
        "current_password": "password1", "new_password": "newpassword1"}).status_code == 200

    client.cookies.clear()
    assert client.post("/api/auth/login", json={
        "email": "bob2@example.com", "password": "password1"}).status_code == 400
    assert client.post("/api/auth/login", json={
        "email": "bob2@example.com", "password": "newpassword1"}).status_code == 200


def test_admin_user_management(client):
    register(client, "alice@example.com", "Alice")  # admin
    register(client, "bob@example.com", "Bob")      # logged in as Bob

    # Non-admin is rejected across the board.
    assert client.post("/api/admin/users", json={
        "email": "x@example.com", "display_name": "X", "password": "password1"}).status_code == 403
    assert client.post("/api/admin/users/1/password", json={
        "new_password": "hackerman1"}).status_code == 403
    assert client.post("/api/admin/users/1/delete").status_code == 403

    login(client, "alice@example.com")

    # Add a user directly (no invite code needed).
    r = client.post("/api/admin/users", json={
        "email": "carol@example.com", "display_name": "Carol", "password": "password1"})
    assert r.status_code == 201, r.text
    carol_id = r.json()["id"]

    # Duplicate email rejected.
    assert client.post("/api/admin/users", json={
        "email": "carol@example.com", "display_name": "Carol2", "password": "password1"}).status_code == 400

    # Reset Carol's password; she can log in with the new one.
    assert client.post(f"/api/admin/users/{carol_id}/password", json={
        "new_password": "newpassword1"}).status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/login", json={
        "email": "carol@example.com", "password": "newpassword1"}).status_code == 200

    # Delete Carol (no history) — fine. Deleting a user with history — refused.
    login(client, "alice@example.com")
    assert client.post(f"/api/admin/users/{carol_id}/delete").status_code == 200
    client.post("/api/awards", json={"to_user_id": 2, "amount": 1, "reason": "x"})
    r = client.post("/api/admin/users/2/delete")
    assert r.status_code == 400
    assert "history" in r.json()["detail"]

    # Can't delete yourself.
    assert client.post("/api/admin/users/1/delete").status_code == 400


def test_admin_invite_code_change(client):
    register(client, "alice@example.com", "Alice")

    assert client.post("/api/admin/invite-code", json={"code": "secret-snack"}).status_code == 200

    # Old code no longer works; new one does.
    client.cookies.clear()
    r = client.post("/api/auth/register", json={
        "email": "bob@example.com", "display_name": "Bob", "password": "password1",
        "invite_code": "test-invite"})
    assert r.status_code == 400
    register(client, "bob@example.com", "Bob", invite_code="secret-snack")

    # Non-admin can't change it.
    assert client.post("/api/admin/invite-code", json={"code": "bobs-code"}).status_code == 403


def test_admin_category_management(client):
    register(client, "alice@example.com", "Alice")

    r = client.post("/api/categories", json={"name": "Snacks"})
    assert r.status_code == 201
    assert r.json()["name"] == "snacks"  # normalized to lowercase
    assert "snacks" in client.get("/api/categories").json()

    # Duplicate rejected.
    assert client.post("/api/categories", json={"name": "snacks"}).status_code == 400

    assert client.post(f"/api/categories/{r.json()['id']}/delete").status_code == 200
    assert "snacks" not in client.get("/api/categories").json()

    # Non-admin can't manage categories.
    register(client, "bob@example.com", "Bob")
    assert client.post("/api/categories", json={"name": "sneaky"}).status_code == 403


def test_notifications_flow(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    client.post("/api/awards", json={"to_user_id": 2, "amount": 3, "reason": "kindness"})

    login(client, "bob@example.com")
    assert client.get("/api/notifications/unread-count").json()["unread"] == 1
    notes = client.get("/api/notifications").json()
    assert len(notes) == 1
    assert "awarded you +3 BP" in notes[0]["text"]
    assert notes[0]["was_unread"] is True
    # Viewing marked them read.
    assert client.get("/api/notifications/unread-count").json()["unread"] == 0


def test_nudge_flow_and_cooldown(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    client.post("/api/awards", json={"to_user_id": 2, "amount": 5, "reason": "gift"})

    login(client, "bob@example.com")
    r = client.post("/api/redemptions", json={"grantor_id": 1, "amount": 2, "reason": "dinner"}).json()

    # Can't nudge while merely PENDING.
    assert client.post(f"/api/redemptions/{r['id']}/nudge").status_code == 400

    login(client, "alice@example.com")
    client.post(f"/api/redemptions/{r['id']}/approve")
    # Grantor can't nudge — only the requester.
    assert client.post(f"/api/redemptions/{r['id']}/nudge").status_code == 403

    login(client, "bob@example.com")
    assert client.post(f"/api/redemptions/{r['id']}/nudge").status_code == 200
    # Cooldown: second nudge within 24h rejected.
    resp = client.post(f"/api/redemptions/{r['id']}/nudge")
    assert resp.status_code == 400
    assert "one nudge per day" in resp.json()["detail"]

    login(client, "alice@example.com")
    texts = [n["text"] for n in client.get("/api/notifications").json()]
    assert any("gently reminds you" in t for t in texts)


def test_bets_api_lifecycle(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    login(client, "alice@example.com")
    client.post("/api/awards", json={"to_user_id": 2, "amount": 10, "reason": "fund"})
    login(client, "bob@example.com")
    client.post("/api/awards", json={"to_user_id": 1, "amount": 10, "reason": "fund"})

    # Bob challenges Alice.
    r = client.post("/api/bets", json={"opponent_id": 1, "stake": 4, "terms": "I'll win"})
    assert r.status_code == 201, r.text
    bet_id = r.json()["id"]
    assert me(client)["held"] == 4

    # Alice accepts, then concedes.
    login(client, "alice@example.com")
    assert client.post(f"/api/bets/{bet_id}/accept").status_code == 200
    assert me(client)["held"] == 4
    assert client.post(f"/api/bets/{bet_id}/concede").status_code == 200
    info = me(client)
    assert info["spendable_balance"] == 6
    assert info["held"] == 0

    login(client, "bob@example.com")
    info = me(client)
    assert info["spendable_balance"] == 14
    assert info["held"] == 0
    bet = client.get("/api/bets").json()[0]
    assert bet["status"] == "SETTLED"
    assert bet["winner_id"] == 2


def test_avatar_update(client):
    register(client, "alice@example.com", "Alice")
    assert me(client)["avatar"] == "🍫"
    r = client.post("/settings/avatar", data={"avatar": "🦖"})
    assert r.status_code == 200
    assert me(client)["avatar"] == "🦖"
    # Garbage rejected.
    assert "One emoji" in client.post("/settings/avatar", data={"avatar": "not an emoji"}).text
    assert me(client)["avatar"] == "🦖"


def test_daily_question_flow(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")  # logged in as Bob

    # Before answering: question visible, answers hidden.
    daily = client.get("/api/daily").json()
    assert daily["question"]
    assert daily["my_answer"] is None
    assert daily["answers"] == []

    # Answer earns +1 BP.
    assert client.post("/api/daily", json={"answer": "waffles, obviously"}).status_code == 201
    assert me(client)["spendable_balance"] == 1

    # No double answering.
    r = client.post("/api/daily", json={"answer": "changed my mind"})
    assert r.status_code == 400
    assert "already answered" in r.json()["detail"]

    # Alice got a "your turn" notification and can't see Bob's answer yet.
    login(client, "alice@example.com")
    texts = [n["text"] for n in client.get("/api/notifications").json()]
    assert any("your turn" in t for t in texts)
    assert client.get("/api/daily").json()["answers"] == []

    # Once Alice answers, both answers are visible to her.
    client.post("/api/daily", json={"answer": "pancakes forever"})
    answers = client.get("/api/daily").json()["answers"]
    assert len(answers) == 2


def test_sealed_gift_flow(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    # Alice sends Bob an immediately-openable gift and a locked one.
    login(client, "alice@example.com")
    g1 = client.post("/api/gifts", json={
        "recipient_id": 2, "amount": 3, "note": "open when you're stressed"})
    assert g1.status_code == 201, g1.text
    g2 = client.post("/api/gifts", json={
        "recipient_id": 2, "amount": 2, "note": "future you says hi",
        "unlock_at": "2099-01-01T00:00:00Z"})
    assert g2.status_code == 201

    # Validations: self-gift and empty note rejected.
    assert client.post("/api/gifts", json={
        "recipient_id": 1, "amount": 1, "note": "me"}).status_code == 400
    assert client.post("/api/gifts", json={
        "recipient_id": 2, "amount": 1, "note": "  "}).status_code == 400

    login(client, "bob@example.com")
    # Sealed: no points yet, contents hidden from recipient.
    assert me(client)["spendable_balance"] == 0
    gifts = client.get("/api/gifts").json()
    assert all("amount" not in g and "note" not in g for g in gifts)

    # Locked gift can't be opened yet; sender can't open at all.
    assert client.post(f"/api/gifts/{g2.json()['id']}/open").status_code == 400
    login(client, "alice@example.com")
    assert client.post(f"/api/gifts/{g1.json()['id']}/open").status_code == 403

    # Open the openable one: points land exactly once, ledger entry appears.
    login(client, "bob@example.com")
    opened = client.post(f"/api/gifts/{g1.json()['id']}/open")
    assert opened.status_code == 200
    assert opened.json()["amount"] == 3
    assert me(client)["spendable_balance"] == 3
    assert client.post(f"/api/gifts/{g1.json()['id']}/open").status_code == 400  # no re-open
    entries = client.get("/api/ledger").json()["entries"]
    assert entries[0]["entry_type"] == "AWARD"
    assert entries[0]["reason"] == "open when you're stressed"


def test_memory_wall(client):
    register(client, "alice@example.com", "Alice")
    register(client, "bob@example.com", "Bob")

    # Create one memory of each kind.
    login(client, "alice@example.com")
    client.post("/api/awards", json={"to_user_id": 2, "amount": 5, "reason": "fund"})
    client.post("/api/daily", json={"answer": "aisle"})
    login(client, "bob@example.com")
    client.post("/api/daily", json={"answer": "window"})
    r = client.post("/api/redemptions", json={"grantor_id": 1, "amount": 1, "reason": "high five"}).json()
    b = client.post("/api/bets", json={"opponent_id": 1, "stake": 1, "terms": "I blink first"}).json()
    login(client, "alice@example.com")
    client.post(f"/api/redemptions/{r['id']}/approve")
    client.post(f"/api/redemptions/{r['id']}/fulfill")
    client.post(f"/api/bets/{b['id']}/accept")
    client.post(f"/api/bets/{b['id']}/concede")
    g = client.post("/api/gifts", json={"recipient_id": 2, "amount": 1, "note": "surprise"}).json()
    login(client, "bob@example.com")
    client.post(f"/api/gifts/{g['id']}/open")

    page = client.get("/memories").text
    assert "high five" in page          # fulfilled redemption
    assert "I blink first" in page      # settled bet
    assert "surprise" in page           # opened gift
    assert "aisle" in page and "window" in page  # both daily answers

    # Add a note to the redemption; it renders.
    resp = client.post("/memories/note", data={
        "kind": "redemption", "ref_id": r["id"], "note": "best high five of my life"})
    assert "best high five of my life" in resp.text

    # Bad kind rejected.
    assert "annotate" in client.post("/memories/note", data={
        "kind": "nonsense", "ref_id": 1, "note": "x"}).text


def test_categories_seeded(client):
    assert set(client.get("/api/categories").json()) == {
        "favor", "chore", "apology", "gift", "bet", "other"}
