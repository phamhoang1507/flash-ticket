from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    auth_provider: Literal["jwt", "cognito"] = "jwt"
    jwt_public_key_path: str = "/app/keys/public.pem"
    jwt_issuer: str = "flash-ticket-local"
    jwt_audience: str = "flash-ticket-api"

    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_scope_prefix: str = "flash-ticket"


settings = AuthSettings()
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]


def principal_from_claims(claims: dict, prefix: str = "") -> Principal:
    subject = claims.get("sub")
    scope = claims.get("scope", "")

    if not isinstance(subject, str) or not 1 <= len(subject) <= 128:
        raise jwt.InvalidTokenError("Invalid subject")

    if not isinstance(scope, str) or claims.get("token_use") != "access":
        raise jwt.InvalidTokenError("Invalid access token")

    scopes = frozenset(scope.split())

    if prefix:
        scopes = frozenset(
            value[len(prefix):]
            for value in scopes
            if value.startswith(prefix)
        )

    return Principal(subject=subject, scopes=scopes)


class CustomJWTVerifier:
    def __init__(self):
        self.key = serialization.load_pem_public_key(
            Path(settings.jwt_public_key_path).read_bytes()
        )
        if not isinstance(self.key, RSAPublicKey) or self.key.key_size < 2048:
            raise ValueError("JWT requires an RSA public key of at least 2048 bits")

    def verify(self, token: str) -> Principal:
        claims = jwt.decode(
            token,
            self.key,
            algorithms=["RS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=5,
            options={
                "require": ["sub", "iss", "aud", "iat", "exp", "token_use"]
            },
        )
        return principal_from_claims(claims)


class CognitoVerifier:
    def __init__(self):
        if not settings.cognito_user_pool_id or not settings.cognito_client_id:
            raise ValueError("Cognito requires user pool ID and client ID")

        if not settings.cognito_scope_prefix:
            raise ValueError("Cognito requires a resource-server scope prefix")

        self.issuer = (
            f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
            f"{settings.cognito_user_pool_id}"
        )
        self.jwks = PyJWKClient(
            f"{self.issuer}/.well-known/jwks.json",
            cache_jwk_set=True,
            lifespan=300,
            timeout=3,
        )

    def verify(self, token: str) -> Principal:
        key = self.jwks.get_signing_key_from_jwt(token).key

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self.issuer,
            leeway=5,
            options={
                "verify_aud": False,
                "require": [
                    "sub", "iss", "iat", "exp", "token_use", "client_id"
                ],
            },
        )

        if claims["client_id"] != settings.cognito_client_id:
            raise jwt.InvalidTokenError("Unexpected Cognito client")

        prefix = settings.cognito_scope_prefix.rstrip("/") + "/"
        return principal_from_claims(claims, prefix)


@lru_cache(maxsize=1)
def get_verifier() -> CustomJWTVerifier | CognitoVerifier:
    if settings.auth_provider == "cognito":
        return CognitoVerifier()
    return CustomJWTVerifier()


def authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            401,
            "Access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return get_verifier().verify(credentials.credentials)
    except PyJWKClientConnectionError:
        raise HTTPException(
            503,
            "Authentication temporarily unavailable",
            headers={"Retry-After": "2"},
        ) from None
    except jwt.PyJWTError:
        raise HTTPException(
            401,
            "Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def require_scope(scope: str):
    def dependency(
        principal: Principal = Depends(authenticate),
    ) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(403, "Insufficient scope")
        return principal

    return dependency