"""Ideias dos comentários: coleta (API simulada), filtro de perguntas, grupos -> análises 'publico-*' (sem rede)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import comentarios  # noqa: E402
from test_gerador import FakeClient  # noqa: E402


class G:
    """Simula o yt-dlp (via_ytdlp): conta os vídeos pedidos e devolve comentários."""
    def __init__(self):
        self.vids = []

    def __call__(self, vid, n=100):
        self.vids.append(vid)
        if vid == "fechado":
            return []
        return [{"texto": t, "likes": l, "respostas": 0} for t, l in (
            ("Léo, com R$ 1.500 de salário dá pra começar?", 30), ("Top demais!", 99),
            ("Quanto precisa juntar pra sair do CLT?", 12), ("compra aqui https://golpe.com como?", 50))]


GRUPOS = {"grupos": [
    {"pergunta": "Com salário baixo dá pra começar?", "tema": "renda extra", "pessoas": 7,
     "exemplos": ["Léo, com R$ 1.500 de salário dá pra começar?"],
     "ideia": {"titulo": "Comecei com R$ 3 mil de salário: o que eu faria com R$ 1.500", "gancho": "R$ 1.500...",
               "topicos": ["conta"], "a_conferir": ""}},
    {"pergunta": "Pergunta isolada", "tema": "outro", "pessoas": 1, "exemplos": [],
     "ideia": {"titulo": "x", "gancho": "x", "topicos": [], "a_conferir": ""}}]}


class Comentarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        o = Path(self.tmp.name)
        (o / "analises").mkdir()
        (o / "meu-canal-dados.json").write_text(json.dumps({"videos": [{"video_id": "leo1", "title": "Motoboy"},
                                                                        {"video_id": "fechado", "title": "X"}]}))
        (o / "videos.json").write_text(json.dumps({
            "r1": {"video_id": "r1", "titulo": "Ref PT", "idioma": "pt", "comentarios": 50, "score": 9},
            "r2": {"video_id": "r2", "titulo": "Ref EN", "idioma": "en", "comentarios": 50, "score": 9},
            "r3": {"video_id": "r3", "titulo": "Ref fraca", "idioma": "pt", "comentarios": 50, "score": 9}}))
        for vid, rel in (("r1", 5), ("r2", 5), ("r3", 2)):
            (o / "analises" / f"{vid}.json").write_text(json.dumps({"relevancia": rel}))

        self.g = G()
        self.orig_yt = comentarios.via_ytdlp
        comentarios.via_ytdlp = self.g

    def tearDown(self):
        comentarios.via_ytdlp = self.orig_yt
        self.tmp.cleanup()

    def test_fontes_e_filtro(self):
        g = self.g
        c = comentarios.coletar(None, comentarios.R.config())
        self.assertEqual(g.vids, ["leo1", "fechado", "r1"])  # sem a referência em inglês e a de relevância baixa
        textos = [x["texto"] for x in c["leo"]]
        self.assertEqual(textos, ["Léo, com R$ 1.500 de salário dá pra começar?", "Quanto precisa juntar pra sair do CLT?"])
        comentarios.coletar(None, comentarios.R.config())
        self.assertEqual(g.vids.count("leo1"), 1)  # cache

    def test_gera_ideias_sem_repetir(self):
        c = FakeClient(GRUPOS)
        novos = comentarios.gerar(client=c)
        self.assertEqual(len(novos), 1)  # grupo de 1 pessoa fica de fora
        a = novos[0]
        self.assertEqual((a["origem"], a["relevancia"], a["pessoas"]), ("comentarios", 5, 7))
        self.assertTrue(a["ideias"][0]["id"].startswith("publico-"))
        self.assertNotIn("a_conferir", a["ideias"][0])
        self.assertIn("(30 curtidas) Léo, com R$ 1.500", c.calls[0]["messages"][0]["content"])
        c2 = FakeClient(GRUPOS)
        self.assertEqual(comentarios.gerar(client=c2), [])  # mesma pergunta não entra de novo
        self.assertIn("Com salário baixo dá pra começar?", c2.calls[0]["messages"][0]["content"].split("NÃO repetir")[1])
        import gerador
        self.assertIsNone(gerador.escolher_referencia(comentarios.R.load_analyses(), {}))  # não vira base do 👎

    def test_painel_mostra_como_referencia_do_publico(self):
        comentarios.gerar(client=FakeClient(GRUPOS))
        import painel
        d = painel.build_data(painel.R.config())
        self.assertEqual(len(d["publico"]), 1)
        self.assertEqual(d["publico"][0]["canal"], "Comentários do público")
        self.assertEqual(d["publico"][0]["score"], 5.5)


if __name__ == "__main__":
    unittest.main()
