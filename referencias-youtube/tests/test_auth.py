"""Simula o fluxo OAuth inteiro: navegador -> redirect local -> troca do código -> grava refresh token."""
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import auth_google  # noqa: E402


class AuthFlow(unittest.TestCase):
    def test_login_completo(self):
        saved, posted = {}, {}
        auth_google.read_client_secret = lambda: {"client_id": "cid", "client_secret": "sec"}

        def fake_browser(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual(q["scope"][0].split(), auth_google.SCOPES)
            self.assertEqual(q["access_type"][0], "offline")
            back = q["redirect_uri"][0] + "/?" + urllib.parse.urlencode({"code": "C0D3", "state": q["state"][0]})
            urllib.request.urlopen(back, timeout=5).read()

        def fake_post(method, url, data=None, headers=None, **k):
            posted.update(data)
            return {"refresh_token": "RT", "scope": " ".join(auth_google.SCOPES)}

        auth_google.webbrowser.open = fake_browser
        auth_google.http_request = fake_post
        auth_google.save_env_var = lambda k, v: saved.__setitem__(k, v)
        auth_google.main()
        self.assertEqual(saved, {"GOOGLE_REFRESH_TOKEN": "RT"})
        self.assertEqual(posted["code"], "C0D3")
        self.assertIn("code_verifier", posted)


if __name__ == "__main__":
    unittest.main()
