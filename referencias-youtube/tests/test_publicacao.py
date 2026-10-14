"""Material de publicação: capítulos pelas regras do YouTube, fonte legenda/roteiro e texto final (sem rede)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import publicacao  # noqa: E402
from test_gerador import FakeClient  # noqa: E402

RESP = {"descricao": "Quanto um CLT junta com R$ 300?\nNeste vídeo...", "comentario_fixado": "Quanto você guarda hoje?",
        "capitulos": [{"inicio_s": 5, "titulo": "A conta"}, {"inicio_s": 60, "titulo": "O erro"},
                      {"inicio_s": 64, "titulo": "colado demais"}, {"inicio_s": 200, "titulo": "O plano"}],
        "tags": ["renda extra", " ", "clt"], "hashtags": ["rendaextra", "#clt", "#dinheiro", "#mais"]}


class G:
    def get(self, url, p=None):
        return {"items": [{"id": "ABCDEFGHIJK", "snippet": {"title": "Quanto um CLT junta", "publishedAt": "2026-10-01T00:00:00Z"},
                           "contentDetails": {"duration": "PT10M"}, "status": {}, "statistics": {}}]}


class Publicacao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        o = Path(self.tmp.name)
        (o / "roteiros").mkdir()
        (o / "roteiros" / "ref1-1.json").write_text(json.dumps({"titulos": [{"titulo": "T"}], "titulo_recomendado": 0,
            "secoes": [{"nome": "Gancho", "fala": "a " * 10}, {"nome": "Conta", "fala": "b " * 40}, {"nome": "Fim", "fala": "c " * 50}]}))
        import transcricao
        self.orig = transcricao.fetch
        self.leg = None
        transcricao.fetch = lambda vid, titulo=None, force=False: self.leg

    def tearDown(self):
        import transcricao
        transcricao.fetch = self.orig
        self.tmp.cleanup()

    def test_video_id(self):
        self.assertEqual(publicacao.video_id("https://youtu.be/ABCDEFGHIJK"), "ABCDEFGHIJK")
        self.assertEqual(publicacao.video_id("ABCDEFGHIJK"), "ABCDEFGHIJK")
        with self.assertRaises(ValueError):
            publicacao.video_id("@primorico")

    def test_capitulos_regras_do_youtube(self):
        c = publicacao.capitulos_validos(RESP["capitulos"], 600)
        self.assertEqual([(x["inicio_s"], x["titulo"]) for x in c], [(0, "A conta"), (60, "O erro"), (200, "O plano")])
        self.assertEqual(publicacao.capitulos_validos(RESP["capitulos"][:2], 600), [])

    def test_gera_pela_legenda_com_links(self):
        leg = Path(self.tmp.name) / "leg.md"
        leg.write_text("[0:00] R$ 300 por mês\n[1:00] o erro")
        self.leg = leg
        publicacao.salvar_links("👉 Grupo: https://exemplo")
        c = FakeClient(RESP)
        p = publicacao.gerar("https://youtu.be/ABCDEFGHIJK", client=c, g=G())
        self.assertEqual((p["fonte"], p["titulo"], p["duracao_s"]), ("legenda", "Quanto um CLT junta", 600))
        self.assertIn("R$ 300 por mês", c.calls[0]["messages"][0]["content"])
        self.assertEqual(p["hashtags"], ["#rendaextra", "#clt", "#dinheiro"])
        self.assertEqual(p["tags"], ["renda extra", "clt"])
        d = p["descricao_completa"]
        self.assertTrue(d.startswith("Quanto um CLT junta com R$ 300?"))
        self.assertIn("0:00 A conta\n1:00 O erro\n3:20 O plano", d)
        self.assertIn("👉 Grupo: https://exemplo", d)
        self.assertTrue(d.endswith("#rendaextra #clt #dinheiro"))
        self.assertEqual(p["avisos"], [])
        txt = (publicacao.pasta() / "ABCDEFGHIJK.txt").read_text()
        self.assertIn("===== COMENTÁRIO FIXADO =====\nQuanto você guarda hoje?", txt)

    def test_sem_legenda_usa_roteiro_e_avisa(self):
        c = FakeClient(RESP)
        p = publicacao.gerar("ABCDEFGHIJK", ideia_id="ref1-1", client=c, g=G())
        self.assertEqual(p["fonte"], "roteiro")
        pedido = c.calls[0]["messages"][0]["content"]
        self.assertIn("[0:00] Gancho", pedido)
        self.assertIn("[1:00] Conta", pedido)  # 10 de 100 palavras -> 10% de 600 s
        self.assertTrue(any("estimados pelo roteiro" in a for a in p["avisos"]))
        self.assertTrue(any("Sem links fixos" in a for a in p["avisos"]))

    def test_sem_legenda_nem_roteiro(self):
        with self.assertRaisesRegex(ValueError, "ainda não gerou a legenda"):
            publicacao.gerar("ABCDEFGHIJK", client=FakeClient(RESP), g=G())


if __name__ == "__main__":
    unittest.main()
