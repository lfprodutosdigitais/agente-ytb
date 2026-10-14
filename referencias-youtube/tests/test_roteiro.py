"""Pacote do vídeo: títulos + roteiro (Claude simulado) e checagem das regras de retenção."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import roteiro  # noqa: E402
from test_gerador import FakeClient  # noqa: E402


def sec(nome, ini, fala, tela="planilha"):
    return {"nome": nome, "inicio_s": ini, "objetivo": "o", "fala": fala, "na_tela": tela}


BOM = {"titulos": [{"titulo": f"Título {n}", "por_que": "x"} for n in range(5)], "titulo_recomendado": 2,
       "promessa": "Você sai sabendo quanto junta", "preparar": ["planilha"], "a_conferir": ["valor exato do CDI"],
       "secoes": [sec("Gancho", 0, "R$ 3.600 em 12 meses, tropa."), sec("Conta", 30, "Com R$ 300 por mês você..."),
                  sec("Fim", 400, "Se inscreve que no próximo eu mostro os R$ 35 mil.")]}


class Roteiro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        o = Path(self.tmp.name)
        (o / "analises").mkdir()
        (o / "transcricoes").mkdir()
        (o / "analises" / "ref1.json").write_text(json.dumps({"estrutura": ["a", "b"], "por_que_assistiram": "prova",
            "ideias": [{"id": "ref1-1", "titulo": "Quanto um CLT junta", "gancho": "R$ 3 mil", "topicos": ["t"]}]}))
        (o / "videos.json").write_text(json.dumps({"ref1": {"canal": "Caio", "titulo": "Low ticket"}}))
        (o / "transcricoes" / "ref1.md").write_text("[0:00] fala da referência")
        (o / "meu-canal-dados.json").write_text(json.dumps({"videos": [
            {"formato": "longo", "duration_s": 900}, {"formato": "longo", "duration_s": 1100}, {"formato": "short", "duration_s": 40}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_gera_json_md_e_usa_duracao_do_canal(self):
        c = FakeClient(BOM)
        p = roteiro.gerar("ref1-1", client=c, cfg=roteiro.R.config())
        self.assertEqual(p["minutos_alvo"], 17)
        self.assertEqual(p["avisos"], [])
        pedido = c.calls[0]["messages"][0]["content"]
        self.assertIn("fala da referência", pedido)
        self.assertIn("~17 min", pedido)
        md = (roteiro.pasta() / "ref1-1.md").read_text()
        self.assertTrue(md.startswith("# Título 2"))
        self.assertIn("⭐ recomendado", md)
        self.assertIn("### [0:30] Conta", md)
        self.assertIn("valor exato do CDI", md)
        self.assertEqual(json.loads((roteiro.pasta() / "ref1-1.json").read_text())["ideia_id"], "ref1-1")

    def test_avisos_de_retencao(self):
        r = {"secoes": [sec("Abertura", 0, "Fala, tropa! Eu me chamo Léo, deixa o like", "eu na câmera"),
                        sec("Meio", 90, "Antes de tudo, se inscreve no canal", "logo"),
                        sec("Final", 400, "Valeu e se inscreve", "fim 1")]}
        avisos, palavras = roteiro.checar(r)
        texto = " | ".join(avisos)
        self.assertIn("Nenhum número nos primeiros 30 s", texto)
        self.assertIn("Pedido de like/inscrição em 0:00", texto)
        self.assertIn("Pedido de like/inscrição em 1:30", texto)
        self.assertNotIn("6:40", texto)  # depois da primeira entrega pode
        self.assertIn("Apresentação ou fé/propósito", texto)
        self.assertEqual(palavras, 20)

    def test_revisor_do_texto_do_leo(self):
        texto = "Fala tropa, eu me chamo Léo, deixa o like.\n\n" + "Hoje eu vou te mostrar " + "como juntar dinheiro " * 25 + "\n\nCom R$ 300 por mês você junta R$ 3.600."
        rev = {"nota": 6, "resumo": "Começo lento.", "gancho_sugerido": "R$ 3.600 em 12 meses.", "pontos_fortes": ["conta"],
               "problemas": [{"trecho": "eu me chamo Léo", "em": "0:00", "problema": "apresentação", "sugestao": "R$ 3.600..."}]}
        c = FakeClient(rev)
        r = roteiro.revisar("ref1-1", texto, client=c, cfg=roteiro.R.config())
        self.assertEqual(r["nota"], 6)
        avisos = " | ".join(r["avisos"])
        self.assertIn("Pedido de like/inscrição em 0:00", avisos)
        self.assertIn("parágrafos longos sem nenhum número", avisos)
        pedido = c.calls[0]["messages"][0]["content"]
        self.assertIn("[0:00] Fala tropa", pedido)
        self.assertIn("Título: Quanto um CLT junta", pedido)
        salvo = json.loads((roteiro.pasta() / "ref1-1-meu.json").read_text())
        self.assertEqual(salvo["texto"], texto.strip())
        with self.assertRaises(ValueError):
            roteiro.revisar("ref1-1", "curto demais", client=c)


if __name__ == "__main__":
    unittest.main()
