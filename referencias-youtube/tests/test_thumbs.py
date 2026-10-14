"""Thumbs: conceitos (Claude simulado) e imagem (Gemini simulado), sem rede."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import thumbs  # noqa: E402
from test_gerador import FakeClient  # noqa: E402

C = {"nome": "Conta na tela", "texto_thumb": " R$ 35 MIL ", "expressao": "sério", "cena": "planilha à esquerda",
     "cores": "verde e preto", "por_que": "número grande", "prompt_imagem": "a man pointing at a spreadsheet"}


class Thumbs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = str(Path(self.tmp.name) / "referencias")
        o = Path(os.environ["REFERENCIAS_DIR"])
        (o / "analises").mkdir(parents=True)
        (o / "analises" / "ref1.json").write_text(json.dumps({"tema": "renda extra", "relevancia": 5,
            "ideias": [{"id": "ref1-1", "titulo": "Quanto um CLT junta", "gancho": "R$ 3 mil"}]}))
        (o / "analises" / "ref2.json").write_text(json.dumps({"tema": "renda extra"}))
        (o / "videos.json").write_text(json.dumps({
            "ref1": {"canal": "Caio", "titulo": "Low ticket", "thumbnail": "https://i.ytimg.com/ref1.jpg", "outlier": True},
            "ref2": {"canal": "Tilbury", "titulo": "Side hustle", "thumbnail": "https://i.ytimg.com/ref2.jpg",
                     "outlier": True, "score": 9},
            "ref3": {"canal": "X", "titulo": "outro tema", "thumbnail": "https://i.ytimg.com/ref3.jpg", "outlier": True}}))
        (o / "meu-canal-dados.json").write_text(json.dumps({"videos": [
            {"titulo": "Motoboy ao milhão", "views": 9000, "thumbnail": "https://i.ytimg.com/leo.jpg"}]}))
        self.baixadas = []
        self.orig = thumbs.baixar
        thumbs.baixar = lambda url: self.baixadas.append(url) or b"\xff\xd8jpg"

    def tearDown(self):
        thumbs.baixar = self.orig
        self.tmp.cleanup()

    def test_conceitos_olham_referencia_mesmo_tema_e_thumbs_do_leo(self):
        c = FakeClient({"conceitos": [C, C, C, C]})
        r = thumbs.conceitos("ref1-1", client=c)
        self.assertEqual(self.baixadas, ["https://i.ytimg.com/ref1.jpg", "https://i.ytimg.com/ref2.jpg",
                                         "https://i.ytimg.com/leo.jpg"])
        self.assertEqual(len(r["conceitos"]), 3)
        self.assertEqual(r["conceitos"][0]["texto_thumb"], "R$ 35 MIL")
        blocos = c.calls[0]["messages"][0]["content"]
        self.assertEqual(sum(1 for b in blocos if b["type"] == "image"), 3)
        self.assertIn("a Brazilian man", blocos[-1]["text"])  # sem fotos do Léo
        salvo = json.loads((thumbs.pasta() / "ref1-1.json").read_text())
        self.assertEqual(salvo["titulo"], "Quanto um CLT junta")

    def test_imagem_usa_fotos_e_guarda_cada_versao(self):
        thumbs.conceitos("ref1-1", client=FakeClient({"conceitos": [C]}))
        fotos = Path(self.tmp.name) / "fotos-leo"
        fotos.mkdir()
        (fotos / "a.jpg").write_bytes(b"foto")
        (fotos / "notas.txt").write_text("x")
        recebido = []

        def gemini(prompt, fotos_bytes, cfg):
            recebido.append((prompt, fotos_bytes))
            return b"\x89PNG"
        n1 = thumbs.imagem("ref1-1", 0, gemini=gemini)
        n2 = thumbs.imagem("ref1-1", 0, gemini=gemini)
        prompt, fb = recebido[0]
        self.assertIn('"R$ 35 MIL"', prompt)
        self.assertIn("same person", prompt)
        self.assertEqual(len(fb), 1)
        self.assertNotEqual(n1, n2)
        salvo = json.loads((thumbs.pasta() / "ref1-1.json").read_text())
        self.assertEqual(salvo["imagens"]["0"], [n1, n2])
        self.assertTrue((thumbs.pasta() / n1).exists())
        self.assertTrue(thumbs.ARQUIVO_OK.match(n1))
        with self.assertRaises(ValueError):
            thumbs.imagem("ref1-1", 5, gemini=gemini)

    def test_resposta_do_gemini(self):
        orig = thumbs.http
        try:
            thumbs.http = lambda *a, **k: {"candidates": [{"content": {"parts": [
                {"text": "ok"}, {"inlineData": {"mimeType": "image/png", "data": "iVBORw=="}}]}}]}
            os.environ["GEMINI_API_KEY"] = "x"
            self.assertEqual(thumbs.chamar_gemini("p", [], {}), b"\x89PNG")
            thumbs.http = lambda *a, **k: {"candidates": [{"finishReason": "IMAGE_SAFETY"}]}
            with self.assertRaisesRegex(ValueError, "IMAGE_SAFETY"):
                thumbs.chamar_gemini("p", [], {})
        finally:
            thumbs.http = orig
            os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
