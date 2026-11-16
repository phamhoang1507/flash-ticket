import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

import jwt

parser = argparse.ArgumentParser()
parser.add_argument("--subject", default="local-user")
parser.add_argument("--admin", action="store_true")
args = parser.parse_args()

if os.getenv("AUTH_PROVIDER", "jwt") != "jwt":
    parser.error("Local token issuance requires AUTH_PROVIDER=jwt")

if not 1 <= len(args.subject) <= 128:
    parser.error("Subject must contain 1 to 128 characters")

private_key = sys.stdin.buffer.read()
now = datetime.now(UTC)

scopes = ["bookings:write"]
if args.admin:
    scopes.append("events:write")

token = jwt.encode(
    {
        "sub": args.subject,
        "iss": os.getenv("JWT_ISSUER", "flash-ticket-local"),
        "aud": os.getenv("JWT_AUDIENCE", "flash-ticket-api"),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "token_use": "access",
        "scope": " ".join(scopes),
    },
    private_key,
    algorithm="RS256",
)

print(token)