"""Gerador de ideias na hora (cliente Anthropic simulado, sem rede)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gerador  # noqa: E402


class FakeClient:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.calls = payload, stop, []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(
            stop_reason=self.stop, content=[types.SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=types.SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=900))


NOVA = {"titulo": "Quanto um CLT junta em 12 meses com R$ 300", "gancho": "R$ 3.600. É isso que…",
        "topicos": ["conta na tela", "  "], "a_conferir": "valor do CDI hoje", "por_que_diferente": "sem bets"}


class Gerador(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        o = Path(self.tmp.name)
        (o / "analises").mkdir()
        (o / "analises" / "refA.json").write_text(json.dumps({"relevancia": 5, "tema": "renda extra", "gancho": "g",
            "ideias": [{"id": "refA-1", "titulo": "Ideia aprovada A"}, {"id": "refA-2", "titulo": "Ideia rejeitada A"}]}))
        (o / "analises" / "refB.json").write_text(json.dumps({"relevancia": 4, "tema": "notícia",
            "ideias": [{"id": "refB-1", "titulo": "Bets acabaram"}]}))
        (o / "videos.json").write_text(json.dumps({"refA": {"canal": "Caio Martins", "score": 5, "titulo": "Low ticket"},
                                                   "refB": {"canal": "Nerds", "score": 9, "titulo": "Fim das bets"}}))
        (o / "meu-canal.md").write_text("<!-- CLAUDE:tom x -->\nFala em 2ª pessoa.\n<!-- /CLAUDE:tom -->\n")
        self.ratings = {"refA-1": {"voto": "up"}, "refA-2": {"voto": "down"}, "refB-1": {"voto": "down"}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_gera_grava_e_monta_pedido(self):
        c = FakeClient(NOVA)
        ref, ideia = gerador.gerar(self.ratings, rejeitada_id="refB-1", client=c)
        self.assertEqual(ref, "refA")  # refB tem relevância 4 mas teve a ideia rejeitada: peso menor
        self.assertEqual(ideia["id"], "refA-g1")
        self.assertEqual(ideia["topicos"], ["conta na tela"])
        self.assertEqual((ideia["origem"], ideia["substitui"], ideia["a_conferir"]), ("gerada", "refB-1", "valor do CDI hoje"))
        salvo = json.loads((Path(self.tmp.name) / "analises" / "refA.json").read_text())
        self.assertEqual(salvo["ideias"][-1]["id"], "refA-g1")
        kw = c.calls[0]
        pedido = kw["messages"][0]["content"]
        self.assertIn("Ideia aprovada A", pedido.split("APROVOU")[1].split("REJEITOU")[0])
        self.assertIn("Bets acabaram", pedido.split("Acabou de rejeitar")[1])
        self.assertIn("Fala em 2ª pessoa.", kw["system"][0]["text"])
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(kw["output_config"]["format"]["schema"], gerador.SCHEMA)
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual((kw["fallbacks"], kw["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        ref2, ideia2 = gerador.gerar(self.ratings, ref_id="refA", client=FakeClient(NOVA))
        self.assertEqual(ideia2["id"], "refA-g2")

    def test_recusa_vira_erro_amigavel(self):
        with self.assertRaises(ValueError):
            gerador.gerar(self.ratings, client=FakeClient(NOVA, stop="refusal"))

    def test_sem_chave(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None), os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        orig = gerador.load_env
        gerador.load_env = lambda: {}
        try:
            ok, motivo = gerador.disponivel()
        finally:
            gerador.load_env = orig
            if old[0]:
                os.environ["ANTHROPIC_API_KEY"] = old[0]
        self.assertFalse(ok)
        self.assertIn("ANTHROPIC", motivo)


if __name__ == "__main__":
    unittest.main()
