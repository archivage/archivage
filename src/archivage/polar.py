"""
Polar AccessLink API client — OAuth2 flow + exercise/HR retrieval.
"""

import json
import webbrowser
from base64 import b64encode
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from .config import getPolarTokens
from .log import logger


AUTH_URL  = 'https://flow.polar.com/oauth2/authorization'
TOKEN_URL = 'https://polarremote.com/v2/oauth2/token'
API_BASE  = 'https://www.polaraccesslink.com'


# ────────────
# Credentials (client_id + client_secret)

_CREDS_PATH = Path.home() / '.config/archivage/polar/credentials.json'


def loadCredentials() -> dict | None:
    if not _CREDS_PATH.exists():
        return None
    with open(_CREDS_PATH) as f:
        return json.load(f)


def saveCredentials(client_id: str, client_secret: str):
    _CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CREDS_PATH, 'w') as f:
        json.dump({'client_id': client_id, 'client_secret': client_secret}, f, indent=2)
    _CREDS_PATH.chmod(0o600)


# ────────────
# Token storage

def _tokensPath() -> Path:
    return getPolarTokens()


def loadTokens() -> dict | None:
    path = _tokensPath()
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def saveTokens(tokens: dict):
    path = _tokensPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(tokens, f, indent=2)
    path.chmod(0o600)


# ────────────
# OAuth2

def _basicAuth(client_id: str, client_secret: str) -> str:
    """HTTP Basic auth header value."""
    pair = f"{client_id}:{client_secret}"
    return 'Basic ' + b64encode(pair.encode()).decode()


def authUrl(client_id: str, redirect_uri: str) -> str:
    params = {
        'response_type': 'code',
        'client_id':     client_id,
        'redirect_uri':  redirect_uri,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchangeCode(code: str, client_id: str, client_secret: str,
                 redirect_uri: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        'grant_type':   'authorization_code',
        'code':         code,
        'redirect_uri': redirect_uri,
    }, headers={
        'Authorization': _basicAuth(client_id, client_secret),
        'Accept':        'application/json',
    })
    resp.raise_for_status()
    tokens = resp.json()
    # tokens: {access_token, token_type, x_user_id}
    # v3 tokens do not expire
    saveTokens(tokens)
    return tokens


def registerUser(access_token: str, user_id: int) -> bool:
    """Register user with AccessLink (one-time after auth).

    Returns True if registered, False if already registered.
    """
    resp = httpx.post(f"{API_BASE}/v3/users", headers={
        'Authorization': f"Bearer {access_token}",
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }, json={'member-id': str(user_id)})
    if resp.status_code in (200, 201):
        logger.info("Polar user registered")
        return True
    if resp.status_code == 409:
        logger.info("Polar user already registered")
        return False
    resp.raise_for_status()
    return False


def _accessToken() -> str:
    """Get access token. v3 tokens don't expire."""
    tokens = loadTokens()
    if not tokens:
        raise RuntimeError("No tokens found. Run 'archivage polar auth' first.")
    return tokens['access_token']


# ────────────
# API

def getExercises() -> list[dict]:
    """List exercises (last 30 days)."""
    access_token = _accessToken()
    resp = httpx.get(f"{API_BASE}/v3/exercises", headers={
        'Authorization': f"Bearer {access_token}",
        'Accept':        'application/json',
    })
    resp.raise_for_status()
    exercises = resp.json()
    logger.info(f"Fetched {len(exercises)} exercises from Polar")
    return exercises


def getExercise(exercise_id: str) -> dict:
    """Get single exercise summary."""
    access_token = _accessToken()
    resp = httpx.get(f"{API_BASE}/v3/exercises/{exercise_id}", headers={
        'Authorization': f"Bearer {access_token}",
        'Accept':        'application/json',
    })
    resp.raise_for_status()
    return resp.json()


def getExerciseHrSamples(exercise_id: str) -> list[int]:
    """Get per-second HR samples for an exercise.

    Returns flat list of HR values, one per second from exercise start.
    """
    access_token = _accessToken()
    resp = httpx.get(f"{API_BASE}/v3/exercises/{exercise_id}/samples/0", headers={
        'Authorization': f"Bearer {access_token}",
        'Accept':        'application/json',
    })
    if resp.status_code == 404:
        logger.info(f"No HR samples for exercise {exercise_id}")
        return []
    resp.raise_for_status()
    body = resp.json()

    hr_values = []
    for sample in body.get('samples', []):
        if sample.get('sample-type') != '0':
            continue
        data_str = sample.get('data', '')
        if data_str:
            hr_values.extend(int(v) for v in data_str.split(',') if v)
    logger.info(f"Fetched {len(hr_values)} HR samples for exercise {exercise_id}")
    return hr_values


# ────────────
# Local OAuth2 callback server

class _CallbackHandler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = qs.get('code', [None])[0]

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<html><body><h2>OK</h2>'
                         b'<p>You can close this tab.</p></body></html>')

    def log_message(self, format, *args):
        pass


def runAuthFlow(client_id: str, client_secret: str,
                port: int = 8585) -> dict:
    """Run full OAuth2 flow: open browser, wait for callback, exchange code."""
    redirect_uri = f"http://localhost:{port}/callback"

    url = authUrl(client_id, redirect_uri)
    print(f"Opening browser for Polar authorization...")
    print(f"  {url}")
    webbrowser.open(url)

    server = HTTPServer(('127.0.0.1', port), _CallbackHandler)
    print(f"Waiting for callback on port {port}...")
    server.handle_request()
    server.server_close()

    if not _CallbackHandler.code:
        raise RuntimeError("No authorization code received")

    print("Exchanging code for tokens...")
    tokens = exchangeCode(
        _CallbackHandler.code, client_id, client_secret, redirect_uri
    )
    print(f"Tokens saved to {_tokensPath()}")

    # Register user (one-time)
    registerUser(tokens['access_token'], tokens['x_user_id'])

    return tokens
