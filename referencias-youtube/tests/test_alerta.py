"""Alerta de vídeo explodindo: ritmo pela idade, uma vez por vídeo, nota baixa não avisa (API simulada)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import alerta  # noqa: E402
from yt_common import now  # noqa: E402

AGORA = now()


def api_video(vid, horas, views, dur="PT12M"):
    return {"id": vid, "snippet": {"title": f"Vídeo {vid}", "channelTitle": "Caio", "publishedAt": (AGORA - timedelta(hours=horas)).isoformat(),
                                   "thumbnails": {}, "liveBroadcastContent": "none"},
            "contentDetails": {"duration": dur}, "statistics": {"viewCount": str(views)}}


class G:
    def __init__(self, itens):
        self.itens, self.calls = itens, []

    def get(self, url, p):
        self.calls.append(url.rsplit("/", 1)[1])
        if url.endswith("playlistItems"):
            return {"items": [{"contentDetails": {"videoId": v["id"]}} for v in self.itens]}
        return {"items": [v for v in self.itens if v["id"] in p["id"].split(",")]}


class Alerta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = str(Path(self.tmp.name) / "referencias")
        o = Path(os.environ["REFERENCIAS_DIR"])
        (o / "cache").mkdir(parents=True)
        (o / "analises").mkdir()
        (o / "cache" / "canais-ids.json").write_text(json.dumps({"@caio": "UCabc"}))
        videos = {f"old{i}": {"video_id": f"old{i}", "canal_handle": "@caio", "formato": "longo", "views": 10000,
                              "data": (AGORA - timedelta(days=10 + i * 5)).isoformat()} for i in range(5)}
        (o / "videos.json").write_text(json.dumps(videos))
        (o / "historico.json").write_text("{}")
        self.orig = alerta.R.read_channels
        alerta.R.read_channels = lambda path=None: [{"handle": "@caio", "url": "https://www.youtube.com/@caio", "lang": "pt"}]
        import gerador
        self.orig_disp = gerador.disponivel
        gerador.disponivel = lambda: (False, "")

    def tearDown(self):
        alerta.R.read_channels = self.orig
        import gerador
        gerador.disponivel = self.orig_disp
        self.tmp.cleanup()

    def test_curva(self):
        self.assertAlmostEqual(alerta.curva(42), 0.5)
        self.assertEqual(alerta.curva(500), 1.0)

    def test_avisa_uma_vez_e_ignora_normais(self):
        itens = [api_video("fogo", 24, 20000),      # 20000 / (10000 × 0,378) = 5,3x
                 api_video("normal", 24, 3000),     # 0,8x
                 api_video("velho", 24 * 20, 90000),  # fora da janela de 7 dias
                 api_video("short", 5, 50000, "PT40S")]
        avisos = []
        novos = alerta.rodar(g=G(itens), cfg={**alerta.R.config()}, avisar=avisos.append)
        self.assertEqual([a["video_id"] for a in novos], ["fogo"])
        self.assertEqual(novos[0]["ritmo"], 5.3)
        self.assertEqual(len(avisos), 1)
        self.assertIn("20.000 views em 24 h · 5,3x o ritmo normal", alerta.texto_alerta(novos[0]))
        videos = json.loads((Path(os.environ["REFERENCIAS_DIR"]) / "videos.json").read_text())
        self.assertTrue(videos["fogo"]["alerta"])
        g = G(itens)
        self.assertEqual(alerta.rodar(g=g, avisar=avisos.append), [])  # não repete
        self.assertEqual(g.calls, ["playlistItems", "videos"])  # 1 canal: 2 unidades, id do canal em cache
        self.assertEqual([a["video_id"] for a in alerta.ativos()], ["fogo"])

    def test_nota_baixa_nao_avisa(self):
        avisos = []
        an = lambda vid, ratings, client, cfg: {"relevancia": 1, "motivo_relevancia": "fora do público", "ideias": []}
        novos = alerta.rodar(g=G([api_video("fogo", 24, 20000)]), analisar=an, avisar=avisos.append)
        self.assertEqual((novos, avisos), ([], []))
        self.assertEqual(alerta.ativos(), [])

    def test_email_so_com_configuracao(self):
        for k in ("ALERTA_EMAIL_PARA", "SMTP_USUARIO", "SMTP_SENHA"):
            os.environ.pop(k, None)
        self.assertFalse(alerta.enviar_email("a", "b"))


if __name__ == "__main__":
    unittest.main()
