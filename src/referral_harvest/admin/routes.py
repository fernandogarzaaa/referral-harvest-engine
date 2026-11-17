"""Admin dashboard (/admin): Jinja2 pages, ADMIN_TOKEN cookie auth, CSRF on POSTs."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from referral_harvest.api.deps import get_db
from referral_harvest.config import get_settings
from referral_harvest.engine.economics import compute_unit_economics
from referral_harvest.models import (
    AskStatus,
    Referral,
    ReferralAsk,
    ReferralStatus,
    Reward,
    RewardStatus,
    Tenant,
    tenant_setting,
)
from referral_harvest.transitions import IllegalTransition, transition
from referral_harvest.util import utcnow

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(__file__).rsplit("/", 1)[0] + "/templates")

COOKIE_NAME = "rh_admin"


def _signer() -> URLSafeSerializer:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin dashboard is disabled: set ADMIN_TOKEN.",
        )
    return URLSafeSerializer(token, salt="rh-admin")


def _session_csrf(request: Request) -> str | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = _signer().loads(raw)
    except BadSignature:
        return None
    return data.get("csrf")


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/login", status_code=303)


def _check_csrf(request: Request, form_token: str | None) -> str:
    csrf = _session_csrf(request)
    if not csrf or not form_token or form_token != csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad CSRF token")
    return csrf


def _tenant_or_404(db: Session, tenant_id: str | None) -> Tenant:
    q = select(Tenant).order_by(Tenant.created_at)
    tenant = db.scalar(q.where(Tenant.id == tenant_id)) if tenant_id else db.scalar(q)
    if tenant is None:
        raise HTTPException(status_code=404, detail="no tenants yet")
    return tenant


def _ctx(request: Request, db: Session, tenant: Tenant, csrf: str) -> dict:
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at)).all()
    return {
        "request": request,
        "tenant": tenant,
        "tenants": tenants,
        "csrf_token": csrf,
    }


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _session_csrf(request):
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, token: str = Form(...)):
    import secrets

    settings = get_settings()
    if not settings.admin_token or token != settings.admin_token:
        return templates.TemplateResponse(
            request, "login.html", {"request": request, "error": "Invalid token."}, status_code=401
        )
    payload = _signer().dumps({"csrf": secrets.token_urlsafe(24)})
    resp = RedirectResponse(url="/admin/", status_code=303)
    resp.set_cookie(COOKIE_NAME, payload, httponly=True, samesite="lax")
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(None)):
    _check_csrf(request, csrf_token)
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    tenant_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    csrf = _session_csrf(request)
    if not csrf:
        return _login_redirect()
    tenant = _tenant_or_404(db, tenant_id)
    now = utcnow()
    econ = compute_unit_economics(db, tenant, now - timedelta(days=30), now)
    funnel = {
        "asks": db.scalar(
            select(func.count()).select_from(ReferralAsk).where(ReferralAsk.tenant_id == tenant.id)
        ),
        "claimed": db.scalar(
            select(func.count()).select_from(Referral).where(Referral.tenant_id == tenant.id)
        ),
        "booked": db.scalar(
            select(func.count())
            .select_from(Referral)
            .where(
                Referral.tenant_id == tenant.id,
                Referral.status.in_([ReferralStatus.booked, ReferralStatus.completed]),
            )
        ),
        "completed": econ["referrals_completed"],
    }
    ctx = _ctx(request, db, tenant, csrf)
    ctx.update({"econ": econ, "funnel": funnel})
    return templates.TemplateResponse(request, "overview.html", ctx)


@router.get("/asks", response_class=HTMLResponse)
def asks_page(
    request: Request,
    tenant_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    csrf = _session_csrf(request)
    if not csrf:
        return _login_redirect()
    tenant = _tenant_or_404(db, tenant_id)
    asks = (
        db.scalars(
            select(ReferralAsk)
            .where(ReferralAsk.tenant_id == tenant.id)
            .order_by(ReferralAsk.created_at.desc())
            .limit(200)
        )
        .unique()
        .all()
    )
    ctx = _ctx(request, db, tenant, csrf)
    ctx.update({"asks": asks, "AskStatus": AskStatus})
    return templates.TemplateResponse(request, "asks.html", ctx)


@router.get("/referrals", response_class=HTMLResponse)
def referrals_page(
    request: Request,
    tenant_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    csrf = _session_csrf(request)
    if not csrf:
        return _login_redirect()
    tenant = _tenant_or_404(db, tenant_id)
    referrals = (
        db.scalars(
            select(Referral)
            .where(Referral.tenant_id == tenant.id)
            .order_by(Referral.created_at.desc())
            .limit(200)
        )
        .unique()
        .all()
    )
    ctx = _ctx(request, db, tenant, csrf)
    ctx.update({"referrals": referrals, "ReferralStatus": ReferralStatus})
    return templates.TemplateResponse(request, "referrals.html", ctx)


@router.get("/rewards", response_class=HTMLResponse)
def rewards_page(
    request: Request,
    tenant_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    csrf = _session_csrf(request)
    if not csrf:
        return _login_redirect()
    tenant = _tenant_or_404(db, tenant_id)
    rewards = (
        db.scalars(
            select(Reward)
            .join(Referral, Reward.referral_id == Referral.id)
            .where(Referral.tenant_id == tenant.id)
            .order_by(Reward.created_at.desc())
            .limit(200)
        )
        .unique()
        .all()
    )
    ctx = _ctx(request, db, tenant, csrf)
    ctx.update({"rewards": rewards, "RewardStatus": RewardStatus})
    return templates.TemplateResponse(request, "rewards.html", ctx)


def _reward_or_404(db: Session, reward_id: str) -> Reward:
    reward = db.get(Reward, reward_id)
    if reward is None:
        raise HTTPException(status_code=404, detail="reward not found")
    return reward


@router.post("/rewards/{reward_id}/approve")
def admin_approve_reward(
    request: Request, reward_id: str, csrf_token: str = Form(None), db: Session = Depends(get_db)
):
    _check_csrf(request, csrf_token)
    reward = _reward_or_404(db, reward_id)
    try:
        transition(reward, RewardStatus.approved)
        reward.approved_at = utcnow()
    except IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    return RedirectResponse(url="/admin/rewards", status_code=303)


@router.post("/rewards/{reward_id}/fulfill")
def admin_fulfill_reward(
    request: Request, reward_id: str, csrf_token: str = Form(None), db: Session = Depends(get_db)
):
    _check_csrf(request, csrf_token)
    reward = _reward_or_404(db, reward_id)
    try:
        transition(reward, RewardStatus.fulfilled)
        reward.fulfilled_at = utcnow()
        reward.fulfilled_via = "dashboard"
    except IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    return RedirectResponse(url="/admin/rewards", status_code=303)


@router.get("/tenants", response_class=HTMLResponse)
def tenants_page(request: Request, db: Session = Depends(get_db)):
    csrf = _session_csrf(request)
    if not csrf:
        return _login_redirect()
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at)).all()
    tenant = tenants[0] if tenants else None
    ctx = {"request": request, "tenants": tenants, "tenant": tenant, "csrf_token": csrf}
    return templates.TemplateResponse(request, "tenants.html", ctx)


EDITABLE_SETTINGS = [
    "trade",
    "reward_amount_cents",
    "reward_kind",
    "ask_cooldown_days",
    "ask_expiry_days",
    "quiet_hours_start",
    "quiet_hours_end",
    "min_rating",
    "sms_sender",
    "crm_adapter",
    "reward_fulfiller",
    "reward_webhook_url",
    "paid_cac_cents",
]


@router.post("/tenants/{tenant_id}/settings")
async def update_tenant_settings(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
):
    form = await request.form()
    _check_csrf(request, form.get("csrf_token"))
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    settings = dict(tenant.settings or {})
    for key in EDITABLE_SETTINGS:
        raw = form.get(f"setting_{key}")
        if raw is None or raw == "":
            continue
        default = tenant_setting(tenant, key)
        if isinstance(default, int):
            try:
                settings[key] = int(raw)
            except ValueError:
                continue
        elif isinstance(default, float):
            try:
                settings[key] = float(raw)
            except ValueError:
                continue
        else:
            settings[key] = raw
    tenant.settings = settings
    db.commit()
    return RedirectResponse(url="/admin/tenants", status_code=303)
