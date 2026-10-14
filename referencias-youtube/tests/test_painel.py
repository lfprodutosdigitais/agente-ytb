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
        painel.main([])
        o = Path(self.tmp.name)
        html = (o / "painel.html").read_text()
        self.assertIn("Referências do Grana Própria", html)
        self.assertIn("[hidden]{display:none!important}", html)  # senão a janela escondida bloqueia os cliques
        self.assertIn("Qualquer score", html)
        self.assertIn('data-view="ideias"', html)
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
        painel.main([])
        av = json.loads((o / "avaliacoes.json").read_text())
        self.assertEqual(av["ideias"]["v0000000000-1"]["voto"], "up")
        self.assertIn("👍", (o / "ideias.md").read_text())
        self.assertIn("status: postado · vídeo: https://youtu.be/meu", (o / "ideias.md").read_text())

    def test_merge_ratings_vence_a_mais_recente(self):
        o = Path(self.tmp.name)
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {"a": {"voto": "up", "atualizado": "2026-10-14T10:00"},
                                                                  "b": {"voto": "down", "atualizado": "2026-10-14T10:00"}}}))
        out = painel.merge_ratings(o, {"a": {"voto": "down", "atualizado": "2026-10-14T09:00"},
                                       "b": {"voto": "up", "atualizado": "2026-10-14T11:00"}, "c": {"voto": "up"}})
        self.assertEqual({k: v["voto"] for k, v in out["ideias"].items()}, {"a": "up", "b": "up", "c": "up"})

    def test_fila_do_painel_arquivo_e_processada_uma_vez(self):
        o = Path(self.tmp.name)
        (o / "avaliacoes.json").write_text(json.dumps({"ideias": {}, "adicionar": [{"entrada": "@novo", "lang": "pt"}]}))
        chamadas = []
        orig = painel.R.process_queue
        painel.R.process_queue = lambda cfg, fila, g=None: chamadas.append([x["entrada"] for x in fila])
        try:
            painel.main([])
            (o / "avaliacoes.json").write_text(json.dumps({"ideias": {}, "adicionar": [{"entrada": "@novo"}]}))
            painel.main([])
        finally:
            painel.R.process_queue = orig
        self.assertEqual(chamadas, [["@novo"]])
        self.assertNotIn("adicionar", json.loads((o / "avaliacoes.json").read_text()))
        self.assertIn('"fila_processada": ["@novo"]', (o / "painel.html").read_text())

    def test_analise_em_segundo_plano(self):
        import threading
        cfg = painel.R.config()
        job, chamados = {}, []

        def fake(vid, ratings, client, cfg):
            chamados.append(vid)
            if vid == "v0000000002":
                raise RuntimeError("sem rede")
            return {"ideias": [{}, {}]}
        painel.start_analysis(job, ["v0000000001", "v0000000002"], cfg, "TOK", threading.Lock(), analisar=fake).join(30)
        self.assertFalse(job["rodando"])
        self.assertEqual(chamados, ["v0000000001", "v0000000002"])
        self.assertEqual((job["feitos"], job["ideias"], len(job["erros"])), (2, 2, 1))
        self.assertTrue((Path(self.tmp.name) / "painel.html").exists())
        self.assertIn("acompanhar()", (Path(self.tmp.name) / "painel.html").read_text())

    def test_servidor_local(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        cfg = painel.R.config()
        painel.generate(cfg, "TOK")
        orig = painel.R.add_reference
        painel.R.add_reference = lambda entrada, cfg, lang="pt", g=None: {"tipo": "canal", "msg": f"ok {entrada} {lang}"}
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), painel.make_handler(cfg, "TOK", threading.Lock()))
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(path, body=None, token="TOK"):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", "X-Token": token},
                                         method="POST" if body is not None else "GET")
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        try:
            st, html = call("/")
            self.assertEqual(st, 200)
            self.assertIn(b"const TOKEN = 'TOK'", html)
            self.assertEqual(call("/api/avaliacoes", {"ideias": {"x": {"voto": "up"}}}, token="errado")[0], 403)
            self.assertEqual(call("/api/avaliacoes", {"ideias": {"x": {"voto": "up", "atualizado": "z"}}})[0], 200)
            saved = json.loads((Path(self.tmp.name) / "avaliacoes.json").read_text())
            self.assertEqual(saved["ideias"]["x"]["voto"], "up")
            st, body = call("/api/adicionar", {"entrada": "@canal", "lang": "en"})
            self.assertEqual(json.loads(body)["msg"], "ok @canal en")
            st, body = call("/api/dados")
            self.assertTrue(json.loads(body)["ok"])
            st, body = call("/api/status")
            self.assertEqual(json.loads(body)["job"]["rodando"], False)
            self.assertEqual(call("/api/status", token="errado")[0], 403)
            self.assertEqual(call("/api/thumb", {"id": "../x", "acao": "ver"})[0], 400)
            st, body = call("/api/thumb", {"id": "v0000000000-1", "acao": "ver"})
            self.assertIsNone(json.loads(body)["thumb"])
            (Path(self.tmp.name) / "thumbs").mkdir()
            (Path(self.tmp.name) / "thumbs" / "v0000000000-1-1-1.jpg").write_bytes(b"jpg")
            self.assertEqual(call("/thumbs/v0000000000-1-1-1.jpg"), (200, b"jpg"))
            self.assertEqual(call("/thumbs/..%2Fpainel.html")[0], 404)
            st, body = call("/api/roteiro", {"id": "v0000000000-1", "acao": "ver"})
            self.assertIsNone(json.loads(body)["roteiro"])
            self.assertEqual(call("/api/roteiro", {"id": "a/b", "acao": "ver"})[0], 400)
            (Path(self.tmp.name) / "roteiros").mkdir()
            (Path(self.tmp.name) / "roteiros" / "v0000000000-1.md").write_text("# r")
            self.assertEqual(call("/roteiros/v0000000000-1.md"), (200, b"# r"))
            self.assertEqual(call("/roteiros/..%2Favaliacoes.json")[0], 404)
            self.assertEqual(json.loads(call("/api/publicacao", {"acao": "ver", "video": "@canal"})[1])["ok"], False)
            st, body = call("/api/publicacao", {"acao": "ver", "video": "https://youtu.be/ABCDEFGHIJK"})
            self.assertIsNone(json.loads(body)["publicacao"])
            call("/api/publicacao", {"acao": "salvar_links", "links": "👉 https://x"})
            self.assertEqual(json.loads(call("/api/publicacao", {"acao": "links"})[1])["links"], "👉 https://x")
            (Path(self.tmp.name) / "publicacao" / "ABCDEFGHIJK.txt").write_text("ok")
            self.assertEqual(call("/publicacao/ABCDEFGHIJK.txt"), (200, b"ok"))
            self.assertEqual(call("/publicacao/links.txt")[0], 404)
        finally:
            painel.R.add_reference = orig
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
