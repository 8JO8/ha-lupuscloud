"""Client for the (undocumented) Lupus Cloud REST API.

Authentication is done against AWS Cognito using the SRP protocol, exactly
like the lupuscloud.com web app (amazon-cognito-identity-js) does it.
The resulting ID token is sent as raw ``Authorization`` header to
``https://api.lupusiot.de/api/v1``.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json as json_lib
import logging
import os
import time
from typing import Any

import aiohttp

from .const import API_BASE_URL, COGNITO_CLIENT_ID, COGNITO_POOL_ID, COGNITO_URL

_LOGGER = logging.getLogger(__name__)

# RFC 5054 3072-bit group, as used by amazon-cognito-identity-js
_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B2251"
    "4A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C"
    "42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007C"
    "B8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED52907"
    "7096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC"
    "07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AAA"
    "C42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4"
    "C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64"
    "521F2B18177B200CBBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B"
    "82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
_G_HEX = "2"
_INFO_BITS = b"Caldera Derived Key"

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip

MFA_CHALLENGES = {
    "SOFTWARE_TOKEN_MFA": "SOFTWARE_TOKEN_MFA_CODE",
    "SMS_MFA": "SMS_MFA_CODE",
    "EMAIL_OTP": "EMAIL_OTP_CODE",
}


class LupusCloudError(Exception):
    """Generic Lupus Cloud error."""


class LupusCloudAuthError(LupusCloudError):
    """Invalid credentials or expired session."""


class LupusCloudMfaRequired(LupusCloudError):
    """A second factor is required to finish the login."""

    def __init__(self, challenge: str, session: str, username: str) -> None:
        """Store the challenge state."""
        super().__init__(challenge)
        self.challenge = challenge
        self.session = session
        self.username = username


class LupusCloudConnectionError(LupusCloudError):
    """Network problem."""


# --------------------------------------------------------------------------- SRP


def _hash_sha256(buf: bytes) -> str:
    value = hashlib.sha256(buf).hexdigest()
    return value.rjust(64, "0")


def _hex_hash(hex_string: str) -> str:
    return _hash_sha256(bytes.fromhex(hex_string))


def _pad_hex(value: int | str) -> str:
    hex_str = value if isinstance(value, str) else f"{value:x}"
    if len(hex_str) % 2 == 1:
        hex_str = "0" + hex_str
    elif hex_str[0] in "89ABCDEFabcdef":
        hex_str = "00" + hex_str
    return hex_str


def _compute_hkdf(ikm: bytes, salt: bytes) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, _INFO_BITS + b"\x01", hashlib.sha256).digest()[:16]


class _AwsSrp:
    """Minimal implementation of Cognito's SRP-6a variant."""

    def __init__(self, pool_id: str) -> None:
        self.pool_name = pool_id.split("_", 1)[1]
        self.big_n = int(_N_HEX, 16)
        self.g = int(_G_HEX, 16)
        self.k = int(_hex_hash("00" + _N_HEX + "0" + _G_HEX), 16)
        self.small_a = int.from_bytes(os.urandom(128), "big") % self.big_n
        self.large_a = pow(self.g, self.small_a, self.big_n)
        if self.large_a % self.big_n == 0:  # pragma: no cover - practically impossible
            raise ValueError("Safety check for A failed")

    @property
    def srp_a(self) -> str:
        return f"{self.large_a:x}"

    def _password_key(self, user_id: str, password: str, server_b: int, salt: str) -> bytes:
        u_value = int(_hex_hash(_pad_hex(self.large_a) + _pad_hex(server_b)), 16)
        if u_value == 0:
            raise ValueError("U cannot be zero")
        user_pass = f"{self.pool_name}{user_id}:{password}".encode()
        x_value = int(_hex_hash(_pad_hex(salt) + _hash_sha256(user_pass)), 16)
        g_mod_pow_xn = pow(self.g, x_value, self.big_n)
        base = server_b - self.k * g_mod_pow_xn
        s_value = pow(base, self.small_a + u_value * x_value, self.big_n)
        return _compute_hkdf(
            bytes.fromhex(_pad_hex(s_value)), bytes.fromhex(_pad_hex(f"{u_value:x}"))
        )

    def process_challenge(self, params: dict[str, str], password: str) -> dict[str, str]:
        user_id = params["USER_ID_FOR_SRP"]
        secret_block = params["SECRET_BLOCK"]
        now = datetime.now(UTC)
        timestamp = (
            f"{_WEEKDAYS[now.weekday()]} {_MONTHS[now.month - 1]} {now.day} "
            f"{now:%H:%M:%S} UTC {now.year}"
        )
        key = self._password_key(user_id, password, int(params["SRP_B"], 16), params["SALT"])
        msg = (
            self.pool_name.encode()
            + user_id.encode()
            + base64.standard_b64decode(secret_block)
            + timestamp.encode()
        )
        signature = base64.standard_b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()
        return {
            "TIMESTAMP": timestamp,
            "USERNAME": user_id,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
            "PASSWORD_CLAIM_SIGNATURE": signature,
        }


# ------------------------------------------------------------------------ tokens


@dataclass
class Tokens:
    """Cognito tokens."""

    id_token: str
    refresh_token: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return time.time() < self.expires_at - 120


def _jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json_lib.loads(base64.urlsafe_b64decode(payload))


class CognitoAuth:
    """Handle login, MFA and token refresh against Cognito."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _call(self, target: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}",
        }
        try:
            async with self._session.post(
                COGNITO_URL,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json(content_type=None)
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LupusCloudConnectionError(str(err)) from err
        if status >= 400:
            err_type = str(data.get("__type", "")).split("#")[-1]
            message = data.get("message") or data.get("Message") or err_type
            if err_type in (
                "NotAuthorizedException",
                "UserNotFoundException",
                "CodeMismatchException",
                "ExpiredCodeException",
                "PasswordResetRequiredException",
                "UserNotConfirmedException",
            ):
                raise LupusCloudAuthError(f"{err_type}: {message}")
            raise LupusCloudError(f"{err_type}: {message}")
        return data

    def _tokens(self, result: dict[str, Any], refresh_token: str | None = None) -> Tokens:
        auth = result["AuthenticationResult"]
        return Tokens(
            id_token=auth["IdToken"],
            refresh_token=auth.get("RefreshToken") or refresh_token or "",
            expires_at=time.time() + int(auth.get("ExpiresIn", 3600)),
        )

    async def login(self, username: str, password: str) -> Tokens:
        """Log in with username/password. Raises LupusCloudMfaRequired if needed."""
        srp = await asyncio.get_running_loop().run_in_executor(None, _AwsSrp, COGNITO_POOL_ID)
        init = await self._call(
            "InitiateAuth",
            {
                "AuthFlow": "USER_SRP_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {"USERNAME": username, "SRP_A": srp.srp_a},
                "ClientMetadata": {},
            },
        )
        if init.get("ChallengeName") != "PASSWORD_VERIFIER":
            raise LupusCloudError(f"Unexpected challenge {init.get('ChallengeName')}")
        params = init["ChallengeParameters"]
        responses = await asyncio.get_running_loop().run_in_executor(
            None, srp.process_challenge, params, password
        )
        result = await self._call(
            "RespondToAuthChallenge",
            {
                "ChallengeName": "PASSWORD_VERIFIER",
                "ClientId": COGNITO_CLIENT_ID,
                "ChallengeResponses": responses,
                "ClientMetadata": {},
            },
        )
        return self._handle_result(result, responses["USERNAME"])

    def _handle_result(self, result: dict[str, Any], username: str) -> Tokens:
        if "AuthenticationResult" in result:
            return self._tokens(result)
        challenge = result.get("ChallengeName", "")
        if challenge in MFA_CHALLENGES:
            raise LupusCloudMfaRequired(challenge, result["Session"], username)
        if challenge == "SELECT_MFA_TYPE":
            raise LupusCloudMfaRequired(challenge, result["Session"], username)
        raise LupusCloudError(f"Unsupported login challenge: {challenge}")

    async def respond_mfa(self, mfa: LupusCloudMfaRequired, code: str) -> Tokens:
        """Answer an MFA challenge with the given code."""
        challenge = mfa.challenge
        if challenge == "SELECT_MFA_TYPE":
            # Prefer the authenticator app
            result = await self._call(
                "RespondToAuthChallenge",
                {
                    "ChallengeName": "SELECT_MFA_TYPE",
                    "ClientId": COGNITO_CLIENT_ID,
                    "Session": mfa.session,
                    "ChallengeResponses": {
                        "USERNAME": mfa.username,
                        "ANSWER": "SOFTWARE_TOKEN_MFA",
                    },
                },
            )
            challenge = result.get("ChallengeName", "")
            mfa = LupusCloudMfaRequired(challenge, result["Session"], mfa.username)
        result = await self._call(
            "RespondToAuthChallenge",
            {
                "ChallengeName": challenge,
                "ClientId": COGNITO_CLIENT_ID,
                "Session": mfa.session,
                "ChallengeResponses": {
                    "USERNAME": mfa.username,
                    MFA_CHALLENGES.get(challenge, "SOFTWARE_TOKEN_MFA_CODE"): code.strip(),
                },
            },
        )
        return self._handle_result(result, mfa.username)

    async def refresh(self, refresh_token: str) -> Tokens:
        """Get a new ID token from a refresh token."""
        result = await self._call(
            "InitiateAuth",
            {
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {"REFRESH_TOKEN": refresh_token},
            },
        )
        return self._tokens(result, refresh_token)


# --------------------------------------------------------------------------- API


class LupusCloudApi:
    """REST client for api.lupusiot.de."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str | None,
        refresh_token: str | None,
    ) -> None:
        self._session = session
        self._auth = CognitoAuth(session)
        self._username = username
        self._password = password
        self._tokens: Tokens | None = Tokens("", refresh_token, 0) if refresh_token else None
        self._lock = asyncio.Lock()
        self.on_refresh_token_changed: Any = None

    @property
    def refresh_token(self) -> str | None:
        return self._tokens.refresh_token if self._tokens else None

    @property
    def account_uuid(self) -> str | None:
        if self._tokens and self._tokens.id_token:
            return _jwt_claims(self._tokens.id_token).get("custom:accountUuid")
        return None

    def set_tokens(self, tokens: Tokens) -> None:
        """Use tokens obtained elsewhere (e.g. from the config flow)."""
        self._tokens = tokens

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._lock:
            if self._tokens and self._tokens.valid and not force:
                return self._tokens.id_token
            old_refresh = self._tokens.refresh_token if self._tokens else None
            new: Tokens | None = None
            if old_refresh:
                try:
                    new = await self._auth.refresh(old_refresh)
                except LupusCloudAuthError as err:
                    _LOGGER.debug("Refresh token rejected: %s", err)
            if new is None:
                if not self._password:
                    raise LupusCloudAuthError("Session expired")
                # Raises LupusCloudMfaRequired if 2FA is active -> reauth
                new = await self._auth.login(self._username, self._password)
            self._tokens = new
            if new.refresh_token != old_refresh and self.on_refresh_token_changed:
                self.on_refresh_token_changed(new.refresh_token)
            return new.id_token

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Do an authenticated API request."""
        for attempt in range(2):
            token = await self._ensure_token(force=attempt > 0)
            try:
                async with self._session.request(
                    method,
                    f"{API_BASE_URL}/{path}",
                    params=params,
                    json=json,
                    headers={"Authorization": token, "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    text = await resp.text()
                    if resp.status == 401:
                        raise LupusCloudAuthError("Unauthorized")
                    if resp.status >= 400:
                        raise LupusCloudError(
                            f"{method} {path} failed ({resp.status}): {text[:300]}"
                        )
                    if not text:
                        return None
                    try:
                        return await resp.json(content_type=None)
                    except ValueError:
                        return text
            except (aiohttp.ClientError, TimeoutError) as err:
                raise LupusCloudConnectionError(str(err)) from err
        raise LupusCloudAuthError("Unauthorized")

    # -- endpoints -----------------------------------------------------------

    async def get_account(self) -> dict[str, Any]:
        return await self.request("GET", "account_details/account")

    async def get_location_tree(self) -> dict[str, Any]:
        return await self.request("GET", "location/tree") or {}

    async def get_building(self, building_uuid: str) -> dict[str, Any]:
        return await self.request("GET", f"location/buildings/{building_uuid}")

    async def get_devices(self, building_uuid: str, personal: bool) -> list[dict]:
        devices: list[dict] = []
        page = 0
        while True:
            data = await self.request(
                "GET",
                "location/devices/filteredSearchPaginated",
                params={
                    "page": page,
                    "size": 200,
                    "sortField": "DEVICE_ID",
                    "sortType": "ASC",
                    "personal": "true" if personal else "false",
                    "pathUuids": building_uuid,
                },
            )
            devices.extend(data.get("content") or [])
            if data.get("last", True) or not data.get("content"):
                return devices
            page += 1

    async def get_alarm_devices(self) -> list[dict]:
        data = await self.request("GET", "location/devices/alarm") or {}
        return data.get("content") or []

    async def get_device_history(self, device_uuid: str, size: int = 5) -> list[dict]:
        data = await self.request(
            "GET", f"devices/history/log/{device_uuid}", params={"page": 0, "size": size}
        )
        return (data or {}).get("content") or []

    async def get_notifications(self, size: int = 20) -> list[dict]:
        data = await self.request("GET", "notifications/ws", params={"page": 0, "size": size})
        return (data or {}).get("content") or []

    async def get_unread_count(self) -> int:
        data = await self.request("GET", "notifications/unreadCount")
        if isinstance(data, dict):
            for key in ("count", "unreadCount", "unread"):
                if key in data:
                    return int(data[key])
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0

    async def acknowledge_alarm(self, device_uuid: str) -> Any:
        return await self.request(
            "PUT", "devices/acknowledgeAlarm", json={"deviceUuids": [device_uuid]}
        )

    async def acknowledge_device(self, device_uuid: str) -> Any:
        return await self.request("PUT", "devices/acknowledge", json={"deviceUuids": [device_uuid]})

    # -- webhooks --------------------------------------------------------------

    async def get_webhooks(self, node_uuid: str) -> list[dict]:
        data = await self.request("GET", f"service/webhook/{node_uuid}")
        found: list[dict] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                if "url" in obj and "uuid" in obj:
                    found.append(obj)
                    return
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(data)
        return found

    async def add_webhook(self, node_uuid: str, url: str, bearer: str) -> Any:
        return await self.request(
            "POST",
            "service/webhook",
            json={
                "nodeUuid": node_uuid,
                "url": url,
                "username": None,
                "password": None,
                "bearer": bearer,
                "custom": None,
                "enabled": True,
                "filter": [],
            },
        )

    async def update_webhook(self, webhook_uuid: str, url: str, bearer: str) -> Any:
        return await self.request(
            "PUT",
            f"service/webhook/{webhook_uuid}",
            json={
                "url": url,
                "username": None,
                "password": None,
                "bearer": bearer,
                "custom": None,
                "enabled": True,
                "filter": [],
            },
        )

    async def delete_webhook(self, webhook_uuid: str) -> Any:
        return await self.request("DELETE", f"service/webhook/{webhook_uuid}")

    async def test_webhook(self, webhook_uuid: str) -> Any:
        return await self.request("GET", f"service/webhook/test/{webhook_uuid}")
