"""Fase 3: painel.html, referencias.csv, ideias.md e importação de avaliações (sem rede)."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import painel  # noqa: E402

NOW = datetime.now(timezone.utc)


class Painel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        o = Path(self.tmp.name)
        videos = {}
        for i in range(15):
            vid = f"v{i:010d}"
            videos[vid] = {"video_id": vid, "canal_handle": "@a", "canal": "Canal A", "formato": "longo", "idioma": "pt",
                           "views": 50000 if i == 0 else 1000, "comentarios": 10, "titulo": f"Vídeo {i} <b>",
                           "data": (NOW - timedelta(days=20 + i * 5)).isoformat(), "url": f"https://youtu.be/{vid}"}
        (o / "videos.json").write_text(json.dumps(videos))
        (o / "historico.json").write_text("{}")
        (o / "analises").mkdir()
        (o / "analises" / "v0000000000.json").write_text(json.dumps({
            "relevancia": 5, "tema": "renda extra", "gancho": "in english", "gancho_literal": "Eu saí do CLT </script>",
            "estrutura": ["a"], "por_que_clicaram": "x", "por_que_assistiram": "y",
            "ideias": [{"id": "v0000000000-1", "titulo": "Ideia 1", "gancho": "g", "topicos": ["t"], "a_conferir": "c"}]}))

    def tearDown(self):
        os.environ["HOME"] = self.old_home or ""
        self.tmp.cleanup()
        self.home.cleanup()

    def test_gera_os_tres_arquivos(self):
        painel.main()
        o = Path(self.tmp.name)
        html = (o / "painel.html").read_text()
        self.assertIn("Painel de Referências", html)
        self.assertNotIn("Eu saí do CLT </script>", html)          # não quebra o <script> de dados
        self.assertIn("Eu saí do CLT <\\/script>", html)
        md = (o / "ideias.md").read_text()
        self.assertIn("## 1. Ideia 1", md)
        self.assertIn("conferir:** c", md)
        csv_text = (o / "referencias.csv").read_text(encoding="utf-8-sig")
        self.assertTrue(csv_text.startswith("id;url;titulo"))
        self.assertIn("v0000000000", csv_text)

    def test_importa_avaliacoes_mais_novas_de_downloads(self):
        o = Path(self.tmp.name)
        dl = Path(self.home.name) / "Downloads"
        dl.mkdir()
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {"v0000000000-1": {"voto": "down"}}}))
        time.sleep(0.01)
        (dl / "avaliacoes (1).json").write_text(json.dumps({"ideias": {"v0000000000-1": {"voto": "up", "status": "postado",
                                                                                       "url": "https://youtu.be/meu"}}}))
        os.utime(dl / "avaliacoes (1).json", (time.time() + 5, time.time() + 5))
        painel.main()
        av = json.loads((o / "avaliacoes.json").read_text())
        self.assertEqual(av["ideias"]["v0000000000-1"]["voto"], "up")
        self.assertIn("👍", (o / "ideias.md").read_text())
        self.assertIn("status: postado · vídeo: https://youtu.be/meu", (o / "ideias.md").read_text())


if __name__ == "__main__":
    unittest.main()
