from __future__ import annotations

import base64
import json
import time
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests

from . import AuthProvider

_DEFAULT_TOKEN_CACHE = ".token_cache"


def _decode_jwt_exp(token: str) -> int | None:
    """Extract the exp claim from a JWT without verification."""
    try:
        payload_b64 = token.split(".")[1]
        # Pad to multiple of 4
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("exp")
    except Exception:
        return None


class OAuth2UserTokenExchange(AuthProvider):
    """OAuth2 User Token Exchange flow for SAP BTP ABAP systems.

    Performs: browser login -> auth code -> user token -> jwt-bearer exchange.
    Caches tokens locally to avoid repeated browser logins.
    """

    def __init__(
        self,
        service_key_path: str = ".default_key",
        redirect_port: int = 8088,
        token_cache_path: str | None = _DEFAULT_TOKEN_CACHE,
    ):
        key_path = Path(service_key_path).resolve()
        self._config = json.loads(key_path.read_text())
        self._redirect_port = redirect_port
        self._access_token: str | None = None
        self._user_token: str | None = None
        if token_cache_path is not None:
            self._cache_path = key_path.parent / token_cache_path
        else:
            self._cache_path = None

        uaa = self._config["uaa"]
        self._token_url = uaa["url"] + "/oauth/token"
        self._authorize_url = uaa["url"] + "/oauth/authorize"
        self._client_id = uaa["clientid"]
        self._client_secret = uaa["clientsecret"]

        self._load_cache()

    @property
    def base_url(self) -> str:
        return self._config["url"]

    @property
    def catalog_path(self) -> str:
        return self._config["catalogs"]["abap"]["path"]

    def authenticate(self, session: requests.Session) -> None:
        if not self._is_token_valid(self._access_token):
            self._refresh_or_login()
        session.headers["Authorization"] = f"Bearer {self._access_token}"

    def _is_token_valid(self, token: str | None, margin: int = 60) -> bool:
        if token is None:
            return False
        exp = _decode_jwt_exp(token)
        if exp is None:
            return False
        return time.time() < (exp - margin)

    def _refresh_or_login(self) -> None:
        # Try re-exchanging the cached user token (it has a longer lifetime)
        if self._is_token_valid(self._user_token):
            print("Refreshing access token from cached user token...")
            self._access_token = self._exchange_jwt_bearer(self._user_token)
            self._save_cache()
            return
        # Full browser login needed
        self._do_login()

    def _do_login(self) -> None:
        auth_code = self._get_auth_code()
        self._user_token = self._exchange_code_for_token(auth_code)
        self._access_token = self._exchange_jwt_bearer(self._user_token)
        self._save_cache()

    def _get_auth_code(self) -> str:
        redirect_uri = f"http://localhost:{self._redirect_port}/callback"

        class _Handler(BaseHTTPRequestHandler):
            auth_code: str | None = None

            def do_GET(self):
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in params:
                    _Handler.auth_code = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h2>Login successful! You can close this tab.</h2>")
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(f"<h2>Error: {params}</h2>".encode())

            def log_message(self, *args):
                pass

        login_url = f"{self._authorize_url}?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
        })

        print("Opening browser for SAP login...")
        webbrowser.open(login_url)
        print(f"Waiting for callback on localhost:{self._redirect_port} ...")

        server = HTTPServer(("localhost", self._redirect_port), _Handler)
        server.handle_request()
        server.server_close()

        if not _Handler.auth_code:
            raise RuntimeError("No authorization code received from browser callback")
        return _Handler.auth_code

    def _exchange_code_for_token(self, auth_code: str) -> str:
        redirect_uri = f"http://localhost:{self._redirect_port}/callback"
        resp = requests.post(
            self._token_url,
            auth=(self._client_id, self._client_secret),
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _exchange_jwt_bearer(self, user_token: str) -> str:
        resp = requests.post(
            self._token_url,
            auth=(self._client_id, self._client_secret),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": user_token,
                "response_type": "token",
            },
        )
        if resp.status_code == 200:
            return resp.json()["access_token"]
        # Fall back to user token if exchange fails
        print(f"Warning: jwt-bearer exchange failed ({resp.status_code}), using user token")
        return user_token

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text())
            self._access_token = data.get("access_token")
            self._user_token = data.get("user_token")
            # Discard if both are expired
            if not self._is_token_valid(self._access_token) and not self._is_token_valid(self._user_token):
                self._access_token = None
                self._user_token = None
        except Exception:
            pass

    def _save_cache(self) -> None:
        if self._cache_path is None:
            return
        data = {
            "access_token": self._access_token,
            "user_token": self._user_token,
        }
        self._cache_path.write_text(json.dumps(data))
