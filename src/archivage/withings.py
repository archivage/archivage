"""
Withings API client — OAuth2 flow + measure retrieval.
"""

import json
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from .config import getWithingsTokens
from .log import logger


AUTH_URL      = 'https://account.withings.com/oauth2_user/authorize2'
TOKEN_URL     = 'https://wbsapi.withings.net/v2/oauth2'
MEASURE_URL   = 'https://wbsapi.withings.net/measure'
MEASURE_V2URL = 'https://wbsapi.withings.net/v2/measure'
SLEEP_V2URL   = 'https://wbsapi.withings.net/v2/sleep'

SCOPE = 'user.metrics,user.activity,user.sleepevents'

# Withings meastype → human-readable name
MEASURE_TYPES = {
    1:  'weight',
    5:  'fat_free_mass',
    6:  'fat_ratio',
    8:  'fat_mass',
    9:  'diastolic_bp',
    10: 'systolic_bp',
    11: 'heart_pulse',
    76: 'muscle_mass',
    77: 'hydration',
    88: 'bone_mass',
}


# ────────────
# Credentials (client_id + client_secret)

_CREDS_PATH = Path.home() / '.config/archivage/withings/credentials.json'


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
# Token storage (OAuth2 access + refresh tokens)

def _tokensPath() -> Path:
    return getWithingsTokens()


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

def authUrl(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        'response_type': 'code',
        'client_id':     client_id,
        'redirect_uri':  redirect_uri,
        'scope':         SCOPE,
        'state':         state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchangeCode(code: str, client_id: str, client_secret: str,
                 redirect_uri: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        'action':        'requesttoken',
        'grant_type':    'authorization_code',
        'client_id':     client_id,
        'client_secret': client_secret,
        'code':          code,
        'redirect_uri':  redirect_uri,
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get('status') != 0:
        raise RuntimeError(f"Token exchange failed: {body}")
    tokens = body['body']
    tokens['obtained_at'] = int(time.time())
    saveTokens(tokens)
    return tokens


def refreshTokens(client_id: str, client_secret: str) -> dict:
    tokens = loadTokens()
    if not tokens:
        raise RuntimeError("No tokens found. Run 'archivage withings auth' first.")

    resp = httpx.post(TOKEN_URL, data={
        'action':        'requesttoken',
        'grant_type':    'refresh_token',
        'client_id':     client_id,
        'client_secret': client_secret,
        'refresh_token': tokens['refresh_token'],
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get('status') != 0:
        raise RuntimeError(f"Token refresh failed: {body}")
    new_tokens = body['body']
    new_tokens['obtained_at'] = int(time.time())
    saveTokens(new_tokens)
    logger.info("Withings tokens refreshed")
    return new_tokens


def _accessToken(client_id: str, client_secret: str) -> str:
    """Get a valid access token, refreshing if expired."""
    tokens = loadTokens()
    if not tokens:
        raise RuntimeError("No tokens found. Run 'archivage withings auth' first.")

    expires_in  = tokens.get('expires_in', 10800)
    obtained_at = tokens.get('obtained_at', 0)
    if time.time() > obtained_at + expires_in - 300:
        tokens = refreshTokens(client_id, client_secret)

    return tokens['access_token']


# ────────────
# API

def getMeasures(client_id: str, client_secret: str,
                startdate: int = None, enddate: int = None) -> list[dict]:
    """Fetch body measures from Withings.

    Returns list of {datetime, type, value, grpid}.
    """
    access_token = _accessToken(client_id, client_secret)

    params = {
        'action':   'getmeas',
        'meastype': ','.join(str(t) for t in MEASURE_TYPES),
        'category': 1,  # real measures only (not objectives)
    }
    if startdate:
        params['startdate'] = startdate
    if enddate:
        params['enddate'] = enddate

    resp = httpx.post(MEASURE_URL, data=params, headers={
        'Authorization': f"Bearer {access_token}",
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get('status') != 0:
        raise RuntimeError(f"Getmeas failed: {body}")

    measures = []
    for grp in body['body'].get('measuregrps', []):
        grpid = grp['grpid']
        dt    = grp['date']

        for m in grp['measures']:
            mtype = MEASURE_TYPES.get(m['type'])
            if not mtype:
                continue
            # value = m['value'] * 10^m['unit']
            value = m['value'] * (10 ** m['unit'])
            measures.append({
                'datetime': dt,
                'type':     mtype,
                'value':    value,
                'grpid':    grpid,
            })

    logger.info(f"Fetched {len(measures)} measures from Withings")
    return measures


def getIntradayActivity(client_id: str, client_secret: str,
                        startdate: int, enddate: int) -> list[dict]:
    """Fetch intraday heart rate, steps, calories.

    Max 24h per request. Returns list of
    {datetime (unix), heart_rate, steps, calories, distance}.
    """
    access_token = _accessToken(client_id, client_secret)

    resp = httpx.post(MEASURE_V2URL, data={
        'action':      'getintradayactivity',
        'startdate':   startdate,
        'enddate':     enddate,
        'data_fields': 'heart_rate,steps,calories,distance',
    }, headers={'Authorization': f"Bearer {access_token}"})
    resp.raise_for_status()
    body = resp.json()
    if body.get('status') != 0:
        raise RuntimeError(f"Getintradayactivity failed: {body}")

    rows = []
    for ts, data in body['body'].get('series', {}).items():
        row = {'datetime': int(ts)}
        if 'heart_rate' in data:
            row['heart_rate'] = data['heart_rate']
        if 'steps' in data:
            row['steps'] = data['steps']
        if 'calories' in data:
            row['calories'] = data['calories']
        if 'distance' in data:
            row['distance'] = data['distance']
        rows.append(row)

    logger.info(f"Fetched {len(rows)} intraday points from Withings")
    return rows


def getWorkouts(client_id: str, client_secret: str,
                startdate: str = None, enddate: str = None,
                lastupdate: int = None) -> list[dict]:
    """Fetch workouts. Returns list of workout dicts."""
    access_token = _accessToken(client_id, client_secret)

    data_fields = (
        'calories,effduration,intensity,steps,distance,elevation,'
        'hr_average,hr_min,hr_max,hr_zone_0,hr_zone_1,hr_zone_2,hr_zone_3'
    )
    params = {'action': 'getworkouts', 'data_fields': data_fields}
    if startdate:
        params['startdateymd'] = startdate
    if enddate:
        params['enddateymd'] = enddate
    if lastupdate:
        params['lastupdate'] = lastupdate

    all_workouts = []
    offset = 0
    while True:
        params['offset'] = offset
        resp = httpx.post(MEASURE_V2URL, data=params,
                          headers={'Authorization': f"Bearer {access_token}"})
        resp.raise_for_status()
        body = resp.json()
        if body.get('status') != 0:
            raise RuntimeError(f"Getworkouts failed: {body}")

        for w in body['body'].get('series', []):
            all_workouts.append(w)

        if not body['body'].get('more'):
            break
        offset = body['body'].get('offset', offset + 1)

    logger.info(f"Fetched {len(all_workouts)} workouts from Withings")
    return all_workouts


def getSleepSummary(client_id: str, client_secret: str,
                    startdate: str = None, enddate: str = None) -> list[dict]:
    """Fetch sleep summaries from Sleep v2 API.

    startdate/enddate: YYYY-MM-DD strings.
    Returns list of sleep night dicts with durations, HR, scores, etc.
    """
    access_token = _accessToken(client_id, client_secret)

    data_fields = (
        'nb_rem_episodes,sleep_efficiency,sleep_latency,sleep_score,'
        'total_sleep_time,total_timeinbed,wakeup_latency,'
        'waso,deepsleepduration,lightsleepduration,remsleepduration,'
        'wakeupduration,wakeupcount,out_of_bed_count,'
        'hr_average,hr_min,hr_max,rr_average,rr_min,rr_max,'
        'breathing_disturbances_intensity,snoring,snoringepisodecount'
    )
    from datetime import datetime as _dt, timedelta
    if not startdate:
        startdate = (_dt.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if not enddate:
        enddate = _dt.now().strftime('%Y-%m-%d')

    params = {
        'action':       'getsummary',
        'startdateymd': startdate,
        'enddateymd':   enddate,
        'data_fields':  data_fields,
    }

    all_nights = []
    offset = 0
    while True:
        params['offset'] = offset
        resp = httpx.post(SLEEP_V2URL, data=params,
                          headers={'Authorization': f"Bearer {access_token}"})
        resp.raise_for_status()
        body = resp.json()
        if body.get('status') != 0:
            raise RuntimeError(f"Sleep getsummary failed: {body}")

        for s in body['body'].get('series', []):
            all_nights.append(s)

        if not body['body'].get('more'):
            break
        offset = body['body'].get('offset', offset + 1)

    logger.info(f"Fetched {len(all_nights)} sleep summaries from Withings")
    return all_nights


# ────────────
# Local OAuth2 callback server

class _CallbackHandler(BaseHTTPRequestHandler):
    code  = None
    state = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code  = qs.get('code', [None])[0]
        _CallbackHandler.state = qs.get('state', [None])[0]

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<html><body><h2>OK</h2>'
                         b'<p>You can close this tab.</p></body></html>')

    def log_message(self, format, *args):
        pass  # silence request logs


def runAuthFlow(client_id: str, client_secret: str,
                port: int = 8585) -> dict:
    """Run full OAuth2 flow: open browser, wait for callback, exchange code."""
    import secrets
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)

    url = authUrl(client_id, redirect_uri, state)
    print(f"Opening browser for Withings authorization...")
    print(f"  {url}")
    webbrowser.open(url)

    server = HTTPServer(('127.0.0.1', port), _CallbackHandler)
    print(f"Waiting for callback on port {port}...")
    server.handle_request()
    server.server_close()

    if not _CallbackHandler.code:
        raise RuntimeError("No authorization code received")
    if _CallbackHandler.state != state:
        raise RuntimeError("State mismatch — possible CSRF")

    print("Exchanging code for tokens...")
    tokens = exchangeCode(
        _CallbackHandler.code, client_id, client_secret, redirect_uri
    )
    print(f"Tokens saved to {_tokensPath()}")
    return tokens
