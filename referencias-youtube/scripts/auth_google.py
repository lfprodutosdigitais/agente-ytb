"""Autoriza o app (OAuth Desktop, loopback + PKCE) e grava GOOGLE_REFRESH_TOKEN no .env da skill.

Uso: python3 scripts/auth_google.py
Abre o navegador; faça login com a conta dona do canal (ou a conta da Brand Account do canal).
"""
import base64
import hashlib
import http.server as httpserver
import secrets
import threading
import urllib.parse
import webbrowser

from yt_common import SCOPES, HttpError, log, read_client_secret, save_env_var
from yt_common import http as http_request


def main():
    cs = read_client_secret()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(httpserver.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
                msg = "Pronto! Pode fechar esta aba e voltar ao terminal." if "code" in q else "Erro: " + q["error"][0]
            else:
                msg = "Aguardando..."
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2>{msg}</h2>".encode())

        def log_message(self, *a):
            pass

    server = httpserver.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = cs.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode({
        "client_id": cs["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    log("Abrindo o navegador. Se não abrir, cole esta URL:\n" + url + "\n")
    log("Aviso 'O Google não verificou este app' é normal (o app é seu): clique em Avançado > Acessar.")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    while "code" not in result and "error" not in result:
        server.handle_request()
    server.server_close()

    if "error" in result:
        raise SystemExit("Autorização negada: " + result["error"])
    if result.get("state") != state:
        raise SystemExit("State OAuth não confere; tente de novo.")
    try:
        tok = http_request("POST", cs.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "code": result["code"], "client_id": cs["client_id"], "client_secret": cs["client_secret"],
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    except HttpError as e:
        raise SystemExit(f"Falha ao trocar o código: {e}")
    if "refresh_token" not in tok:
        raise SystemExit("O Google não devolveu refresh_token. Remova o acesso do app em "
                         "https://myaccount.google.com/permissions e rode de novo.")
    granted = set(tok.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        log("ATENÇÃO: escopos não concedidos (marque todas as caixas na tela de consentimento): " + ", ".join(missing))
    save_env_var("GOOGLE_REFRESH_TOKEN", tok["refresh_token"])
    log("Refresh token salvo no .env da skill. Teste com: python3 scripts/meu_canal.py --check")


if __name__ == "__main__":
    main()
