#!/usr/bin/env python3
"""Email a one-time recovery link for abandoned Stripe Checkout Sessions.

Requires env:
  STRIPE_SECRET_KEY
  RESEND_API_KEY
Optional:
  RESEND_FROM (default: DeltaIQ <support@getdeltaiq.com>)
  RECOVERY_LOOKBACK_HOURS (default: 36)
  RECOVERY_COOLDOWN_HOURS (default: 72)
  CHECKOUT_API (default: Railway create-checkout-session URL)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict


def _clean_secret(value: str) -> str:
    s = (value or "").strip().strip('"').strip("'").replace("\r", "").replace("\n", "")
    return s.strip()


STRIPE_KEY = _clean_secret(os.environ.get("STRIPE_SECRET_KEY", ""))
RESEND_KEY = _clean_secret(os.environ.get("RESEND_API_KEY", ""))
RESEND_FROM = _clean_secret(os.environ.get("RESEND_FROM", "")) or "DeltaIQ <support@getdeltaiq.com>"
LOOKBACK_H = int(os.environ.get("RECOVERY_LOOKBACK_HOURS", "36"))
COOLDOWN_H = int(os.environ.get("RECOVERY_COOLDOWN_HOURS", "72"))
CHECKOUT_API = os.environ.get(
    "CHECKOUT_API",
    "https://deltaiq-signal-engine-production.up.railway.app/api/stripe/create-checkout-session",
).strip()
SITE_RECOVER = "https://getdeltaiq.com/subscribe/recover.html"


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def stripe_auth_header() -> str:
    token = base64.b64encode(f"{STRIPE_KEY}:".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def stripe_get(path: str, params: dict | None = None) -> dict:
    qs = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"https://api.stripe.com/v1{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(
        url,
        headers={"Authorization": stripe_auth_header()},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        die(f"Stripe {e.code} {path}: {body}")


def create_checkout(email: str, plan: str) -> dict:
    body = json.dumps(
        {
            "plan": plan if plan in ("monthly", "annual") else "monthly",
            "ack_tos_privacy": True,
            "ack_disclosures": True,
            "ack_sms": False,
            "ack_age_us": True,
            "phone": "",
            "email": email,
            "customer_email": email,
        }
    ).encode()
    req = urllib.request.Request(
        CHECKOUT_API,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def send_email(to_email: str, recover_url: str, checkout_url: str) -> None:
    html = f"""
    <p>You started a DeltaIQ 14-day free trial but didn’t finish checkout.</p>
    <p><a href="{checkout_url}">Finish secure checkout</a></p>
    <p>Or reopen the trial form with your email saved:<br>
    <a href="{recover_url}">{recover_url}</a></p>
    <p>New customers: card required, not charged for 14 days, then $69/mo (or $690/yr). Cancel anytime in My Account.</p>
    <p>Questions? Email support@getdeltaiq.com<br>
    Prefer no signup reminders? Reply and ask to be removed, or ignore this message.</p>
    """
    payload = {
        "from": RESEND_FROM,
        "to": [to_email],
        "subject": "Finish your DeltaIQ 14-day free trial",
        "html": html,
        "text": (
            "You started a DeltaIQ 14-day free trial but didn’t finish checkout.\n\n"
            f"Finish secure checkout: {checkout_url}\n"
            f"Or reopen with your email: {recover_url}\n\n"
            "Questions: support@getdeltaiq.com"
        ),
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {RESEND_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
        print(f"sent email id={data.get('id')} to={to_email}")


def list_sessions(status: str, created_gte: int) -> list[dict]:
    out: list[dict] = []
    starting_after = None
    while True:
        params = {
            "status": status,
            "limit": 100,
            "created[gte]": created_gte,
        }
        if starting_after:
            params["starting_after"] = starting_after
        page = stripe_get("/checkout/sessions", params)
        batch = page.get("data") or []
        out.extend(batch)
        if not page.get("has_more") or not batch:
            break
        starting_after = batch[-1]["id"]
    return out


def main() -> None:
    if not STRIPE_KEY:
        die("STRIPE_SECRET_KEY missing — add it under repo Secrets to enable recovery emails", 0)
    if not RESEND_KEY:
        die("RESEND_API_KEY missing — add it under repo Secrets to enable recovery emails", 0)
    if STRIPE_KEY.startswith("pk_"):
        die("STRIPE_SECRET_KEY is a publishable key (pk_…). Replace it with the Secret key (sk_live_…) from Stripe Dashboard → Developers → API keys.")
    if not (STRIPE_KEY.startswith("sk_") or STRIPE_KEY.startswith("rk_")):
        die("STRIPE_SECRET_KEY should start with sk_live_ or rk_live_. Open Stripe Dashboard → Developers → API keys and copy Secret key.")
    print(f"stripe_key_prefix={STRIPE_KEY.split('_')[0]}_{STRIPE_KEY.split('_')[1] if '_' in STRIPE_KEY else '?'}")

    now = int(time.time())
    lookback = now - LOOKBACK_H * 3600
    cooldown = now - COOLDOWN_H * 3600

    expired = list_sessions("expired", lookback)
    open_sessions = list_sessions("open", cooldown)

    open_by_email: dict[str, list[dict]] = defaultdict(list)
    for s in open_sessions:
        email = (s.get("customer_email") or "").strip().lower()
        if email:
            open_by_email[email].append(s)

    # Most recent expired session per email in the lookback window.
    latest_expired: dict[str, dict] = {}
    for s in expired:
        email = (s.get("customer_email") or "").strip().lower()
        if not email or email.endswith("@example.com"):
            continue
        prev = latest_expired.get(email)
        if not prev or s.get("created", 0) > prev.get("created", 0):
            latest_expired[email] = s

    print(f"expired_with_email={len(latest_expired)} open={len(open_sessions)}")

    sent = 0
    skipped = 0
    for email, session in sorted(latest_expired.items()):
        # Skip if they already have a newer open checkout (still in progress / just recovered).
        newer_open = [
            o
            for o in open_by_email.get(email, [])
            if o.get("created", 0) >= session.get("created", 0)
        ]
        if newer_open:
            skipped += 1
            print(f"skip {email}: open session already exists")
            continue

        plan = (session.get("metadata") or {}).get("plan") or "monthly"
        try:
            created = create_checkout(email, plan)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            print(f"checkout_create_failed email={email} status={e.code} body={body[:300]}")
            continue
        if not created.get("ok") or not created.get("url"):
            print(f"checkout_create_failed email={email} resp={created}")
            continue

        recover_url = f"{SITE_RECOVER}?email={urllib.parse.quote(email)}&start-trial=1"
        try:
            send_email(email, recover_url, created["url"])
            sent += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            print(f"email_failed email={email} status={e.code} body={body[:300]}")

    print(f"done sent={sent} skipped={skipped}")


if __name__ == "__main__":
    main()
