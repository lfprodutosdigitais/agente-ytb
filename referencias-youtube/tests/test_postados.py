"""Relatório dos vídeos do Léo: janelas 2/7/30 dias, medianas, quedas -> seção do roteiro e lição (sem rede)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import postados  # noqa: E402
from test_gerador import FakeClient  # noqa: E402
from yt_common import now  # noqa: E402

VIEWS = {"novo": 3000, "a": 1000, "b": 1500, "novo1234567": 200}


class FakeG:
    def __init__(self):
        self.calls = []

    def get(self, url, p=None):
        self.calls.append((url, dict(p or {})))
        vid = (p or {}).get("filters", "video==?").split("==")[1]
        if "elapsedVideoTimeRatio" in (p or {}).get("dimensions", ""):
            base = 0.6 if vid == "novo" else 0.5
            rows = [[i / 20, round(1 - (i / 20) * base - (0.25 if vid == "novo" and i >= 8 else 0), 3), 0.5]
                    for i in range(21)]
            return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"},
                                      {"name": "relativeRetentionPerformance"}], "rows": rows}
        metrics = p["metrics"].split(",")
        vals = {"views": VIEWS[vid], "averageViewPercentage": 40.0, "videoThumbnailImpressions": 10000,
                "videoThumbnailImpressionsClickRate": 6.0 if vid == "novo" else 4.0}
        return {"columnHeaders": [{"name": m} for m in metrics], "rows": [[vals.get(m, 1) for m in metrics]]}


class Postados(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        o = Path(self.tmp.name)
        d = lambda n: (now() - timedelta(days=n)).isoformat()
        (o / "meu-canal-dados.json").write_text(json.dumps({"videos": [
            {"video_id": "novo", "title": "Quanto um CLT junta", "published_at": d(40), "duration_s": 600, "formato": "longo"},
            {"video_id": "a", "title": "A", "published_at": d(80), "duration_s": 600, "formato": "longo"},
            {"video_id": "b", "title": "B", "published_at": d(400), "duration_s": 600, "formato": "longo"},
            {"video_id": "s", "title": "Short", "published_at": d(5), "duration_s": 40, "formato": "short"}]}))
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {"ref1-1": {
            "voto": "up", "status": "postado", "url": "https://youtu.be/novo1234567"}}}))
        # o link da ideia aponta para "novo1234567", que não está nos dados: busca pela API de vídeos
        (o / "analises").mkdir()
        (o / "analises" / "ref1.json").write_text(json.dumps({"ideias": [{"id": "ref1-1", "titulo": "Ideia X"}]}))
        (o / "roteiros").mkdir()
        (o / "roteiros" / "ref1-1.json").write_text(json.dumps({"secoes": [
            {"nome": "Gancho", "fala": " ".join(["a"] * 10)}, {"nome": "Conta", "fala": " ".join(["b"] * 30)},
            {"nome": "Fim", "fala": " ".join(["c"] * 60)}]}))
        import meu_canal
        self.orig = meu_canal.video_details
        meu_canal.video_details = lambda g, ids: {"novo1234567": {
            "video_id": "novo1234567", "title": "Postado agora", "published_at": d(1), "duration_s": 700}}

    def tearDown(self):
        import meu_canal
        meu_canal.video_details = self.orig
        self.tmp.cleanup()

    def test_relatorio(self):
        g = FakeG()
        rel = postados.atualizar(g=g)
        ids = [v["video_id"] for v in rel["videos"]]
        self.assertEqual(ids, ["novo1234567", "novo", "a"])  # sem o Short e sem o antigo (>120 dias)
        novo = rel["videos"][1]
        self.assertFalse(novo["d7"]["parcial"])
        self.assertEqual(novo["d7"]["x_mediana"], 2.4)  # 3000 / mediana(1000, 1500)
        textos = " ".join(d["texto"] for d in novo["diagnostico"])
        self.assertIn("2,4x as views", textos)
        self.assertIn("Título/thumb funcionaram", textos)
        self.assertTrue(novo["retencao"]["quedas"])
        recem = rel["videos"][0]
        self.assertEqual(recem["ideia_id"], "ref1-1")
        self.assertEqual(recem["ideia_titulo"], "Ideia X")
        self.assertTrue(recem["d2"]["parcial"])
        self.assertTrue(rel["curva_media"])
        # janelas fechadas ficam no cache: a segunda rodada não pede de novo
        n = len(g.calls)
        postados.atualizar(g=g)
        janelas_2 = [c for c in g.calls[n:] if "dimensions" not in c[1] and c[1].get("filters") == "video==a"]
        self.assertEqual(janelas_2, [])

    def test_quedas_por_secao(self):
        rot = json.loads((Path(self.tmp.name) / "roteiros" / "ref1-1.json").read_text())
        q = postados.secoes_nas_quedas(rot, [{"pos": 0.05}, {"pos": 0.3}, {"pos": 0.9}])
        self.assertEqual([x["secao"] for x in q], ["Gancho", "Conta", "Fim"])

    def test_licao_e_regras_no_contexto(self):
        postados.atualizar(g=FakeG())
        import gerador
        orig = postados._trechos
        postados._trechos = lambda *a: ["[0:00–0:30] fala do Léo"]
        try:
            c = FakeClient({"resumo": "Foi bem.", "funcionou": ["número cedo"], "mudar": [],
                            "regra": "Mostrar a planilha antes de 0:15"})
            li = postados.licao("novo", client=c)
        finally:
            postados._trechos = orig
        self.assertEqual(li["regra"], "Mostrar a planilha antes de 0:15")
        self.assertIn("fala do Léo", c.calls[0]["messages"][0]["content"])
        self.assertIn("Mostrar a planilha antes de 0:15", gerador.contexto_fixo())
        with self.assertRaises(ValueError):
            postados.licao("naoexiste", client=c)


if __name__ == "__main__":
    unittest.main()
