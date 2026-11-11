"""Plays the payment provider: posts a signed webhook for a payment.

    python scripts/simulate_provider.py <paymentId> captured
    python scripts/simulate_provider.py <paymentId> failed card_declined

Env: PAYMENTS_URL (default http://localhost:3005), PAYMENT_WEBHOOK_SECRET.
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request

payment_id, outcome = sys.argv[1], sys.argv[2]
body = {"paymentId": payment_id, "outcome": outcome}
if len(sys.argv) > 3:
    body["reason"] = sys.argv[3]
raw = json.dumps(body).encode()
secret = os.environ.get("PAYMENT_WEBHOOK_SECRET", "dev-only-webhook-secret")
req = urllib.request.Request(
    os.environ.get("PAYMENTS_URL", "http://localhost:3005") + "/payments/webhook",
    data=raw,
    headers={"content-type": "application/json", "x-provider-signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()},
    method="POST",
)
try:
    with urllib.request.urlopen(req) as res:
        status, text = res.status, res.read().decode()
except urllib.error.HTTPError as e:
    status, text = e.code, e.read().decode()
print(status, text)
sys.exit(0 if status == 204 else 1)
