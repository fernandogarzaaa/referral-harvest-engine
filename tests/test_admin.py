"""Admin dashboard tests: login, auth gating, CSRF, rewards queue actions."""

from itsdangerous import URLSafeSerializer

from referral_harvest.models import Referral, Reward, RewardKind, RewardStatus

ADMIN_COOKIE = "rh_admin"


def _login(client, token="test-admin-token"):
    return client.post("/admin/login", data={"token": token}, follow_redirects=False)


def _csrf_from_cookie(client):
    raw = client.cookies.get(ADMIN_COOKIE)
    assert raw
    return URLSafeSerializer("test-admin-token", salt="rh-admin").loads(raw)["csrf"]


def _seed_reward(app_db, tenant):
    from referral_harvest.models import ReferralStatus
    from tests.conftest import seed_customer

    seed_customer(app_db, tenant)
    referral = Referral(
        tenant_id=tenant.id,
        ask_id=None,
        code="MIKE-ADM1",
        referee_name="Jane",
        referee_phone_e164="+15550009999",
        status=ReferralStatus.claimed,
    )
    app_db.add(referral)
    app_db.flush()
    reward = Reward(
        referral_id=referral.id,
        kind=RewardKind.cash,
        amount_cents=2500,
        status=RewardStatus.pending,
    )
    app_db.add(reward)
    app_db.commit()
    return reward


def test_login_page_renders(client):
    r = client.get("/admin/login")
    assert r.status_code == 200
    assert "Operator login" in r.text


def test_login_wrong_token_401(client):
    r = _login(client, token="nope")
    assert r.status_code == 401


def test_login_sets_cookie_and_overview_loads(client, app_tenant):
    _, tenant = app_tenant
    r = _login(client)
    assert r.status_code == 303
    assert ADMIN_COOKIE in r.cookies or client.cookies.get(ADMIN_COOKIE)
    r = client.get("/admin/")
    assert r.status_code == 200
    assert tenant.name in r.text


def test_overview_requires_login(client):
    client.cookies.clear()
    r = client.get("/admin/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/admin/login")


def test_asks_and_referrals_pages(client, app_tenant):
    _login(client)
    for path in ("/admin/asks", "/admin/referrals", "/admin/rewards", "/admin/tenants"):
        r = client.get(path)
        assert r.status_code == 200, path


def test_reward_approve_and_fulfill_flow(client, app_tenant, app_db):
    _, tenant = app_tenant
    reward = _seed_reward(app_db, tenant)
    _login(client)
    csrf = _csrf_from_cookie(client)

    r = client.post(
        f"/admin/rewards/{reward.id}/approve", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    app_db.expire_all()
    assert app_db.get(Reward, reward.id).status == RewardStatus.approved

    r = client.post(
        f"/admin/rewards/{reward.id}/fulfill", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    app_db.expire_all()
    done = app_db.get(Reward, reward.id)
    assert done.status == RewardStatus.fulfilled
    assert done.fulfilled_via == "dashboard"
    assert done.fulfilled_at is not None


def test_csrf_rejected(client, app_tenant, app_db):
    _, tenant = app_tenant
    reward = _seed_reward(app_db, tenant)
    _login(client)
    r = client.post(
        f"/admin/rewards/{reward.id}/approve",
        data={"csrf_token": "forged"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    app_db.expire_all()
    assert app_db.get(Reward, reward.id).status == RewardStatus.pending


def test_tenant_settings_update(client, app_tenant, app_db):
    _, tenant = app_tenant
    _login(client)
    csrf = _csrf_from_cookie(client)
    r = client.post(
        f"/admin/tenants/{tenant.id}/settings",
        data={
            "csrf_token": csrf,
            "setting_trade": "Plumbing",
            "setting_reward_amount_cents": "5000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    app_db.expire_all()
    t = app_db.get(type(tenant), tenant.id)
    assert t.settings["trade"] == "Plumbing"
    assert t.settings["reward_amount_cents"] == 5000


def test_logout_clears_session(client):
    _login(client)
    csrf = _csrf_from_cookie(client)
    r = client.post("/admin/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/admin/", follow_redirects=False)
    assert r.status_code == 303  # back to login
