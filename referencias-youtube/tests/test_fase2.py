"""Testes da Fase 2 (sem rede): Apify simulado, outliers por vizinhança, em alta, engajamento, temas."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import referencias as R  # noqa: E402

TODAY = datetime(2026, 10, 13, tzinfo=timezone.utc)
CFG = dict(R.DEFAULTS)


def vid(i, ch="@a", fmt="longo", views=1000, days_ago=30, comments=10):
    return {"video_id": f"{ch}{fmt}{i}", "canal_handle": ch, "canal": ch, "formato": fmt, "views": views,
            "comentarios": comments, "data": (TODAY - timedelta(days=days_ago)).isoformat(), "titulo": f"v{i}"}


def base(ch="@a", fmt="longo", n=20, views=1000):
    return {v["video_id"]: v for v in (vid(i, ch, fmt, views, days_ago=10 + i * 7) for i in range(n))}


class Normalize(unittest.TestCase):
    chan = {"url": "https://www.youtube.com/@x", "handle": "@x", "lang": "pt"}

    def test_video_item(self):
        r = R.normalize({"id": "abcdefghijk", "title": "T", "viewCount": 12345, "likes": 10, "commentsCount": 5,
                         "date": "2026-10-01T10:00:00.000Z", "duration": "00:12:31", "url": "https://www.youtube.com/watch?v=abcdefghijk",
                         "thumbnailUrl": "th", "numberOfSubscribers": 1000000, "channelName": "X"}, self.chan, CFG)
        self.assertEqual((r["formato"], r["duracao_s"], r["views"], r["comentarios"], r["inscritos_canal"]),
                         ("longo", 751, 12345, 5, 1000000))

    def test_short_by_url_and_text_numbers(self):
        r = R.normalize({"url": "https://www.youtube.com/shorts/ABCDEFGHIJK", "viewCount": "1.2M", "duration": "0:45"},
                        self.chan, CFG)
        self.assertEqual((r["video_id"], r["formato"], r["views"]), ("ABCDEFGHIJK", "short", 1200000))

    def test_short_by_type(self):
        r = R.normalize({"id": "abcdefghijk", "type": "shorts", "duration": "00:01:10"}, self.chan, CFG)
        self.assertEqual(r["formato"], "short")

    def test_item_sem_id_ignorado(self):
        self.assertIsNone(R.normalize({"title": "sem id"}, self.chan, CFG))


class Channels(unittest.TestCase):
    def test_read_and_match(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.txt"
            f.write_text("# comentário\nhttps://www.youtube.com/@primorico\nhttps://www.youtube.com/@marktilbury | en  # inglês\n\n")
            chans = R.read_channels(f)
        self.assertEqual([c["handle"] for c in chans], ["@primorico", "@marktilbury"])
        self.assertEqual(chans[1]["lang"], "en")
        self.assertEqual(R.match_channel("primorico", chans)["url"], "https://www.youtube.com/@primorico")
        self.assertEqual(R.match_channel("@novo", chans)["url"], "https://www.youtube.com/@novo")

    def test_input_encodes_accent(self):
        inp = R.channel_input({"url": "https://www.youtube.com/@GêmeosInvestem"}, CFG)
        self.assertEqual(inp["startUrls"][0]["url"], "https://www.youtube.com/@G%C3%AAmeosInvestem")
        self.assertEqual((inp["maxResults"], inp["maxResultsShorts"]), (50, 0))


class Metrics(unittest.TestCase):
    def test_outlier_vizinhanca(self):
        vs = base()
        hit = next(iter(vs.values()))
        hit["views"] = 3500
        R.compute(vs, {}, CFG, TODAY)
        self.assertEqual(hit["outlier_score"], 3.5)
        self.assertTrue(hit["outlier"])
        self.assertEqual(sum(v["outlier"] for v in vs.values()), 1)

    def test_vizinhos_usam_videos_proximos_na_data(self):
        # canal cresceu: vídeos antigos 100 views, recentes 10.000. Um recente com 12.000 não é outlier.
        vs = {}
        for i in range(30):
            v = vid(i, views=100 if i >= 15 else 10000, days_ago=10 + i * 10)
            vs[v["video_id"]] = v
        rec = vs["@alongo0"]
        rec["views"] = 12000
        R.compute(vs, {}, CFG, TODAY)
        self.assertLess(rec["outlier_score"], 3)
        self.assertFalse(rec["outlier"])

    def test_corte_short_e_jovem(self):
        vs = base(fmt="short")
        a, b = list(vs.values())[:2]
        a["views"], a["data"] = 4000, (TODAY - timedelta(days=20)).isoformat()   # 4x < 5x do short
        b["views"], b["data"] = 9000, (TODAY - timedelta(days=3)).isoformat()    # 9x mas tem 3 dias
        R.compute(vs, {}, CFG, TODAY)
        self.assertFalse(a["outlier"])
        self.assertTrue(b["jovem"])
        self.assertFalse(b["outlier"])

    def test_formatos_separados(self):
        vs = {**base(fmt="longo", views=1000), **base(fmt="short", views=100000)}
        R.compute(vs, {}, CFG, TODAY)
        self.assertFalse(any(v["outlier"] for v in vs.values()))

    def test_engajamento_soma(self):
        vs = base()
        a, b = list(vs.values())[:2]
        a["views"] = b["views"] = 4000
        a["comentarios"], b["comentarios"] = 120, 40  # média do canal ~1%; a = 3%
        R.compute(vs, {}, CFG, TODAY)
        self.assertGreater(a["score"], b["score"])
        self.assertLessEqual(a["score"], a["outlier_score"] * (1 + CFG["engajamento_bonus_max"]))

    def test_em_alta(self):
        vs = base()
        fast = next(iter(vs.values()))
        fast["data"] = (TODAY - timedelta(days=5)).isoformat()
        hist = {v["video_id"]: [{"data": "2026-10-06", "views": 900}, {"data": "2026-10-13", "views": 970}] for v in vs.values()}
        hist[fast["video_id"]] = [{"data": "2026-10-06", "views": 100}, {"data": "2026-10-13", "views": 1000}]
        R.compute(vs, hist, CFG, TODAY)
        self.assertTrue(fast["em_alta"])
        self.assertFalse(fast["outlier"])
        self.assertEqual(sum(v["em_alta"] for v in vs.values()), 1)


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_um_por_dia(self):
        r = {"video_id": "x", "views": 10}
        R.store([r], TODAY)
        R.store([{**r, "views": 12}], TODAY)
        R.store([{**r, "views": 20}], TODAY + timedelta(days=7))
        hist = json.loads((Path(self.tmp.name) / "historico.json").read_text())
        self.assertEqual(hist["x"], [{"data": "2026-10-13", "views": 12}, {"data": "2026-10-20", "views": 20}])

    def test_temas_validados_e_pendencias(self):
        vs = {**base("@a"), **base("@b")}
        a1, b1 = vs["@alongo0"], vs["@blongo0"]
        a1["views"] = b1["views"] = 5000
        R.compute(vs, {}, CFG, TODAY)
        an = Path(self.tmp.name) / "analises"
        an.mkdir()
        (an / "@alongo0.json").write_text(json.dumps({"relevancia": 5, "tema": "sair do CLT"}))
        (an / "@blongo0.json").write_text(json.dumps({"relevancia": 2, "tema": "sair do CLT", "ideias": []}))
        analyses = R.load_analyses()
        cands, pend_rel, pend_anal = R.candidates(vs, analyses, CFG)
        self.assertEqual(len(cands), 2)
        self.assertEqual(pend_rel, [])
        self.assertEqual(pend_anal, ["@alongo0"])
        temas = R.validated_themes(vs, analyses, CFG, TODAY)
        self.assertEqual(len(temas), 1)
        self.assertEqual((temas[0]["tema"], temas[0]["canais"]), ("sair do CLT", ["@a", "@b"]))
        self.assertEqual(sorted(temas[0]["videos"]), ["@alongo0", "@blongo0"])

    def test_teste_apify_fluxo_completo(self):
        calls = []

        def fake_http(method, url, params=None, data=None, **k):
            calls.append((method, url))
            if url.endswith("/runs"):
                self.assertEqual(data["startUrls"][0]["url"], "https://www.youtube.com/@primorico")
                return {"data": {"id": "RUN1", "status": "RUNNING", "defaultDatasetId": "DS1"}}
            if url.endswith("/actor-runs/RUN1"):
                return {"data": {"id": "RUN1", "status": "SUCCEEDED", "defaultDatasetId": "DS1", "usageTotalUsd": 0.42}}
            if url.endswith("/datasets/DS1/items"):
                items = [{"id": f"vid{i:08d}", "title": f"Vídeo {i}", "viewCount": 1000 + (9000 if i == 3 else 0),
                          "commentsCount": 10, "date": (TODAY - timedelta(days=10 + i * 3)).isoformat(),
                          "duration": "00:10:00", "url": f"https://www.youtube.com/watch?v=vid{i:08d}"} for i in range(20)]
                items += [{"id": f"sho{i:08d}", "type": "shorts", "viewCount": 5000, "commentsCount": 2,
                           "date": (TODAY - timedelta(days=10 + i)).isoformat(), "duration": "0:40",
                           "url": f"https://www.youtube.com/shorts/sho{i:08d}"} for i in range(10)]
                return items
            raise AssertionError(url)

        R.http = fake_http
        recs, usd = R.collect([R.match_channel("@primorico", [])], {**CFG, "max_shorts": 50}, "TOKEN")
        self.assertEqual(usd, 0.42)
        self.assertEqual(sum(r["formato"] == "short" for r in recs), 10)
        recs, _ = R.collect([R.match_channel("@primorico", [])], CFG, "TOKEN")  # padrão: sem Shorts
        self.assertEqual(sum(r["formato"] == "short" for r in recs), 0)
        self.assertEqual(len(recs), 20)
        cands = R.recalc(CFG, quiet=True)  # Shorts da 1ª coleta ficam fora dos candidatos
        self.assertEqual([c["video_id"] for c in cands], ["vid00000003"])
        cand = json.loads((Path(self.tmp.name) / "candidatos.json").read_text())
        self.assertEqual(cand["pendentes_relevancia"], ["vid00000003"])
        custos = json.loads((Path(self.tmp.name) / "custos-apify.json").read_text())
        self.assertEqual(custos[0]["usd"], 0.42)


if __name__ == "__main__":
    unittest.main()
