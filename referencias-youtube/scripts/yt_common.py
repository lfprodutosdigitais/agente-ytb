"""Utilidades compartilhadas: .env, HTTP, OAuth do Google, caminhos e estatística.

Só biblioteca padrão do Python (3.9+). Dependência externa única do projeto: yt-dlp (CLI).
"""
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
CLIENT_SECRET_PATH = SKILL_DIR / "client_secret.json"
CONFIG_PATH = SKILL_DIR / "config.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def out_dir():
    """Pasta de saída: ./referencias/ relativa ao diretório atual (ou REFERENCIAS_DIR)."""
    d = Path(os.environ.get("REFERENCIAS_DIR", "referencias")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- .env

def load_env(path=ENV_PATH):
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def save_env_var(key, value, path=ENV_PATH):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [l for l in lines if not l.strip().startswith(key + "=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    os.environ[key] = value


def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg


# ---------------------------------------------------------------- HTTP

class HttpError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} em {url.split('?')[0]}: {body[:500]}")
        self.status, self.body, self.url = status, body, url


def http(method, url, params=None, data=None, headers=None, raw=False, retries=3, timeout=60):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = None
    headers = dict(headers or {})
    if data is not None:
        if isinstance(data, (dict, list)) and headers.get("Content-Type") != "application/x-www-form-urlencoded":
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
        else:
            body = data
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                content = r.read()
                return content if raw else (json.loads(content) if content else {})
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise HttpError(e.code, text, url) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < retries - 1 and method == "GET":
                time.sleep(2 ** (attempt + 1))
                continue
            raise


# ---------------------------------------------------------------- OAuth Google

def read_client_secret():
    if not CLIENT_SECRET_PATH.exists():
        raise SystemExit(f"Falta {CLIENT_SECRET_PATH}. Siga o passo 4 do guia (README.md).")
    data = json.loads(CLIENT_SECRET_PATH.read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


class Google:
    """Cliente mínimo: troca o refresh token por access token e chama as APIs."""

    def __init__(self):
        load_env()
        self.refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
        if not self.refresh_token:
            raise SystemExit("Sem GOOGLE_REFRESH_TOKEN no .env. Rode: python3 scripts/auth_google.py")
        self.cs = read_client_secret()
        self._token, self._exp = None, 0

    def token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        try:
            r = http("POST", self.cs.get("token_uri", "https://oauth2.googleapis.com/token"), data={
                "client_id": self.cs["client_id"], "client_secret": self.cs["client_secret"],
                "refresh_token": self.refresh_token, "grant_type": "refresh_token",
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        except HttpError as e:
            if "invalid_grant" in e.body:
                raise SystemExit("Refresh token inválido/expirado. Confira se o app está 'In production' "
                                 "e rode de novo: python3 scripts/auth_google.py")
            if "invalid_client" in e.body:
                raise SystemExit("client_secret.json inválido (cliente OAuth não encontrado). Baixe de novo o JSON "
                                 "da credencial 'Desktop app' (README, passo 4) e rode scripts/auth_google.py.")
            raise
        self._token, self._exp = r["access_token"], time.time() + int(r.get("expires_in", 3600))
        return self._token

    def get(self, url, params=None, raw=False):
        return http("GET", url, params=params, headers={"Authorization": f"Bearer {self.token()}"}, raw=raw)

    def post(self, url, data):
        return http("POST", url, data=data, headers={"Authorization": f"Bearer {self.token()}"})


# ---------------------------------------------------------------- utilidades

def parse_iso_duration(s):
    """PT1H2M3S -> 3723 segundos."""
    if not s or not s.startswith("P"):
        return 0
    total, num, in_time = 0, "", False
    units = {"D": 86400, "H": 3600, "M": 60, "S": 1}
    for ch in s[1:]:
        if ch == "T":
            in_time = True
        elif ch.isdigit() or ch == ".":
            num += ch
        else:
            if ch == "M" and not in_time:  # meses: ignora
                num = ""
                continue
            total += float(num or 0) * units.get(ch, 0)
            num = ""
    return int(total)


def parse_date(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def now():
    return datetime.now(timezone.utc)


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt_time(seconds):
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def read_json(path, default=None):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"aviso: {p} corrompido, ignorando")
    return default


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
