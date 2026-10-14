"""Fase 4: pesos a partir de 👍/👎, resultado de vídeos postados (Analytics simulado) e aprendizados.md."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import aprendizado as A  # noqa: E402
import meu_canal  # noqa: E402
import referencias as R  # noqa: E402

NOW = datetime.now(timezone.utc)


class Fase4(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        o = Path(self.tmp.name)
        (o / "analises").mkdir()
        refs = {"refA": ("Caio Martins", "renda extra"), "refB": ("Alfredo Soares", "negócio/empreender")}
        videos = {}
        for vid, (canal, tema) in refs.items():
            (o / "analises" / f"{vid}.json").write_text(json.dumps({"relevancia": 5, "tema": tema, "ideias": [
                {"id": f"{vid}-{n}", "titulo": f"Criei um produto de R$ 19,90 ({n})" if vid == "refA" else f"Quanto PAGA ter seguidores {n}",
                 "gancho": "g", "topicos": []} for n in (1, 2)]}))
            videos[vid] = {"video_id": vid, "canal": canal, "canal_handle": "@" + vid, "formato": "longo", "views": 100,
                           "data": (NOW - timedelta(days=40)).isoformat()}
        (o / "videos.json").write_text(json.dumps(videos))
        own = [{"video_id": f"own{i:08d}", "title": f"Meu vídeo {i}", "formato": "longo", "duration_s": 600,
                "published_at": (NOW - timedelta(days=60 + i)).strftime("%Y-%m-%dT%H:%M:%SZ")} for i in range(3)]
        (o / "meu-canal-dados.json").write_text(json.dumps({"channel": {"id": "UCleo"}, "videos": own}))
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {
            "refA-1": {"voto": "up", "status": "postado", "url": "https://youtu.be/own00000000", "canal": "Caio Martins", "tema": "renda extra"},
            "refA-2": {"voto": "up", "canal": "Caio Martins", "tema": "renda extra"},
            "refB-1": {"voto": "down", "motivo": "assunto de influencer", "canal": "Alfredo Soares", "tema": "negócio/empreender"},
            "refB-2": {"voto": "down", "canal": "Alfredo Soares", "tema": "negócio/empreender"}}}))
        views = {"own00000000": 9000, "own00000001": 3000, "own00000002": 2000}
        self.orig = (meu_canal.metrics_window, meu_canal.retention_curve)
        meu_canal.metrics_window = lambda g, vid, s, e, with_impressions=True: {
            "views": views[vid] * (1 if (datetime.fromisoformat(e) - datetime.fromisoformat(s)).days < 10 else 3),
            "averageViewPercentage": 30.0, "subscribersGained": 10}
        meu_canal.retention_curve = lambda g, vid, s, e: [[x / 100, 1 - x / 200 - (0.3 if vid == "own00000000" else 0.4) * min(x / 5, 1), None] for x in range(1, 101)]

    def tearDown(self):
        meu_canal.metrics_window, meu_canal.retention_curve = self.orig
        os.environ["HOME"] = self.old_home or ""
        self.tmp.cleanup()
        self.home.cleanup()

    def test_pesos_resultados_e_markdown(self):
        A.main(g=object())
        o = Path(self.tmp.name)
        pesos = json.loads((o / "pesos.json").read_text())
        self.assertEqual(pesos["canal"]["Caio Martins"]["acertos"], 1)
        self.assertEqual(pesos["canal"]["Caio Martins"]["peso"], 1.33)   # 0,5 + (2 👍 + 2×1 acerto + 1) / (4 + 2)
        self.assertEqual(pesos["canal"]["Alfredo Soares"]["peso"], 0.75) # 0,5 + 1 / (2 👎 + 2)
        md = (o / "aprendizados.md").read_text()
        self.assertIn("| Caio Martins | 2 | 0 | 1 | 0 | 1.33 |", md)
        self.assertIn("✅ acerto", md)
        self.assertIn("3.6", md)  # 9000 / mediana(3000, 2000)=2500 aos 7 dias
        self.assertIn("assunto de influencer", md)
        self.assertIn("teste/desafio feito na tela", md)
        # análise escrita no bloco é preservada na próxima rodada
        md2 = md.replace("<!-- /CLAUDE:analise -->", "Repetir: conta na tela.\n<!-- /CLAUDE:analise -->")
        (o / "aprendizados.md").write_text(md2)
        A.main(g=object())
        self.assertIn("Repetir: conta na tela.", (o / "aprendizados.md").read_text())

    def test_peso_reordena_candidatos(self):
        A.main(g=object())
        videos = json.loads((Path(self.tmp.name) / "videos.json").read_text())
        for v in videos.values():
            v.update(outlier=True, score=10.0 if v["video_id"] == "refB" else 8.0)
        cands, _, _ = R.candidates(videos, R.load_analyses(), R.config())
        self.assertEqual([c["video_id"] for c in cands], ["refA", "refB"])  # 8×1,5×peso tema > 10×0,5×...

    def test_sem_postados_nao_chama_api(self):
        o = Path(self.tmp.name)
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {"refA-1": {"voto": "up"}}}))
        A.main(g=None)  # g=None: se tentasse a API, quebraria por falta de token
        self.assertIn("Nenhuma ideia marcada como postado", (o / "aprendizados.md").read_text())


if __name__ == "__main__":
    unittest.main()
