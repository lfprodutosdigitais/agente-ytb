"""Testes da Fase 1 com um cliente Google falso (sem rede). Rodar: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import meu_canal  # noqa: E402
import transcricao  # noqa: E402
import yt_common  # noqa: E402
from yt_common import HttpError, now  # noqa: E402

VTT_AUTO = """WEBVTT
Kind: captions
Language: pt

00:00:00.000 --> 00:00:02.000
<c>Eu</c> saí do CLT

00:00:02.000 --> 00:00:04.000
Eu saí do CLT
com trinta anos

00:00:04.000 --> 00:00:20.000
com trinta anos
e hoje tenho três empresas

00:00:20.000 --> 00:00:25.000
e o primeiro passo foi o MEI
"""


def curve(points=100, hook_drop=0.3, mid_drop_at=0.5):
    c = []
    for i in range(1, points + 1):
        x = i / points
        y = 1.0 - hook_drop * min(x / 0.05, 1) - 0.2 * x
        if x > mid_drop_at:
            y -= 0.15
        c.append([x, y, 0.5])
    return c


class FakeGoogle:
    def __init__(self, impressions="analytics"):
        self.impressions = impressions
        self.calls = []
        now_ = now()
        self.videos = [
            ("v_long1", "Como saí do CLT", "PT12M", 9000, now_ - timedelta(days=400)),
            ("v_long2", "Renda extra com MEI", "PT10M", 3000, now_ - timedelta(days=60)),
            ("v_long3", "Investir 100 reais", "PT8M30S", 1000, now_ - timedelta(days=20)),
            ("v_short1", "1 dica de renda extra", "PT45S", 50000, now_ - timedelta(days=10)),
            ("v_short2", "CLT x PJ", "PT30S", 5000, now_ - timedelta(days=200)),
        ]

    def get(self, url, params=None, raw=False):
        self.calls.append((url, params))
        p = params or {}
        if url.endswith("/channels"):
            return {"items": [{"id": "UC1", "snippet": {"title": "Grana Própria"}, "statistics": {"subscriberCount": "1234"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if url.endswith("/playlistItems"):
            return {"items": [{"contentDetails": {"videoId": v[0]}} for v in self.videos]}
        if url.endswith("/videos"):
            ids = p["id"].split(",")
            return {"items": [{"id": v[0], "snippet": {"title": v[1], "publishedAt": v[4].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                       "liveBroadcastContent": "none", "thumbnails": {"high": {"url": "t"}}},
                               "contentDetails": {"duration": v[2]}, "statistics": {"viewCount": str(v[3]), "commentCount": "3"},
                               "status": {"privacyStatus": "public"}} for v in self.videos if v[0] in ids]}
        if url == meu_canal.ANALYTICS:
            vid = (p.get("filters") or "video==").split("==")[1]
            views = next((v[3] for v in self.videos if v[0] == vid), 100)
            if p.get("dimensions") == "insightTrafficSourceType":
                ads = views * 0.7 if vid == "v_long1" else 0
                return {"columnHeaders": [{"name": "insightTrafficSourceType"}, {"name": "views"}],
                        "rows": [["ADVERTISING", ads], ["YT_SEARCH", views - ads]]}
            if p.get("dimensions") == "elapsedVideoTimeRatio":
                c = curve(hook_drop=0.5 if vid == "v_long3" else 0.2)
                return {"columnHeaders": [{"name": n} for n in ("elapsedVideoTimeRatio", "audienceWatchRatio", "relativeRetentionPerformance")],
                        "rows": c}
            if "videoThumbnailImpressions" in p["metrics"]:
                if self.impressions != "analytics":
                    raise HttpError(400, "Unknown identifier (videoThumbnailImpressions)", url)
                return {"columnHeaders": [{"name": "videoThumbnailImpressions"}, {"name": "videoThumbnailImpressionsClickRate"}],
                        "rows": [[views * 20, 5.0]]}
            names = p["metrics"].split(",")
            vals = {"views": views, "estimatedMinutesWatched": views * 3, "averageViewDuration": 200,
                    "averageViewPercentage": 40.0 if vid != "v_long1" else 55.0, "subscribersGained": views // 100}
            return {"columnHeaders": [{"name": n} for n in names], "rows": [[vals.get(n, 0) for n in names]]}
        if url.endswith("/reportTypes"):
            return {"reportTypes": [{"id": "channel_basic_a2"}, {"id": "channel_reach_basic_a1"}]}
        if url.endswith("/jobs"):
            return {"jobs": [{"id": "J1", "reportTypeId": "channel_reach_basic_a1"}]} if self.impressions == "reporting" else {"jobs": []}
        if url.endswith("/jobs/J1/reports"):
            return {"reports": [{"id": "R1", "downloadUrl": "https://dl/R1"}]}
        if url == "https://dl/R1":
            return (b"date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
                    b"20260101,UC1,v_long1,1000,0.05\n20260102,UC1,v_long1,1000,0.03\n20260101,UC1,v_short1,500,0.1\n")
        raise AssertionError("URL inesperada " + url)

    def post(self, url, data):
        self.calls.append((url, data))
        return {"id": "JNEW", "reportTypeId": data["reportTypeId"]}


class Unit(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(yt_common.parse_iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(yt_common.parse_iso_duration("PT45S"), 45)
        self.assertEqual(yt_common.parse_iso_duration("P1DT1M"), 86460)
        self.assertEqual(yt_common.parse_iso_duration(None), 0)

    def test_vtt_dedup(self):
        cues = transcricao.parse_vtt(VTT_AUTO)
        text = " ".join(t for _, t in cues)
        self.assertEqual(text, "Eu saí do CLT com trinta anos e hoje tenho três empresas e o primeiro passo foi o MEI")
        self.assertEqual(transcricao.first_seconds(cues, 15), "Eu saí do CLT com trinta anos e hoje tenho três empresas")

    def test_markdown_roundtrip(self):
        cues = transcricao.parse_vtt(VTT_AUTO)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.md"
            p.write_text(transcricao.to_markdown("x", cues, "pt"), encoding="utf-8")
            back = transcricao.read_cues(p)
        self.assertEqual(back[0][0], 0)
        self.assertIn("primeiro passo", back[-1][1])
        self.assertEqual(back[-1][0], 20)

    def test_pick_language(self):
        files = [Path("a.en.vtt"), Path("a.pt-orig.vtt"), Path("a.pt-BR.vtt")]
        self.assertEqual(transcricao._pick(files).name, "a.pt-BR.vtt")
        self.assertEqual(transcricao._pick([Path("a.en-orig.vtt"), Path("a.en.vtt")]).name, "a.en.vtt")

    def test_retention_summary(self):
        c = curve(hook_drop=0.5)
        r = meu_canal.retention_summary(c, 600, False)
        self.assertEqual(r["hook_s"], 30)
        self.assertLess(r["ret_gancho"], 0.6)
        self.assertTrue(r["quedas"])
        self.assertTrue(any(abs(q["pos"] - 0.48) < 0.05 for q in r["quedas"]), r["quedas"])

    def test_ratio_at(self):
        c = [[0.1, 1.0], [0.2, 0.5]]
        self.assertAlmostEqual(meu_canal.ratio_at(c, 0.15), 0.75)
        self.assertEqual(meu_canal.ratio_at(c, 0.05), 1.0)
        self.assertEqual(meu_canal.ratio_at(c, 0.9), 0.5)

    def test_reach_csv_percent(self):
        agg = {}
        meu_canal.merge_reach_csv("video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\nA,100,5\n", agg)
        self.assertAlmostEqual(agg["A"]["clicks"], 5)


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name
        meu_canal.is_short = lambda vid, dur, mx=180: dur <= mx
        transcricao.fetch = lambda vid, title=None, force=False: None

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, fake, **kw):
        meu_canal.Google = lambda: fake
        args = types.SimpleNamespace(check=False, refresh=False, no_transcripts=False, **kw)
        meu_canal.run(args)
        return json.loads((Path(self.tmp.name) / "meu-canal-dados.json").read_text()), \
            (Path(self.tmp.name) / "meu-canal.md").read_text()

    def test_full_run_analytics_impressions(self):
        data, md = self._run(FakeGoogle("analytics"))
        vids = {v["video_id"]: v for v in data["videos"]}
        self.assertEqual(vids["v_short1"]["formato"], "short")
        self.assertEqual(vids["v_long1"]["formato"], "longo")
        self.assertEqual(data["stats"]["longo"]["mediana_views"], 3000)
        self.assertEqual(vids["v_long1"]["vs_mediana"], 3.0)
        self.assertAlmostEqual(vids["v_long1"]["ctr"], 0.05)
        self.assertTrue(any("gancho fraco" in f for f in vids["v_long3"]["flags"]))
        self.assertIn("Longos acima da mediana (1 de 3)", md)
        self.assertIn("Cobertos nos últimos 3 meses", md)
        self.assertIn("Renda extra com MEI", md.split("Cobertos nos últimos 3 meses")[1])
        self.assertNotIn("Como saí do CLT", md.split("Cobertos nos últimos 3 meses")[1])

    def test_fallback_reporting(self):
        data, md = self._run(FakeGoogle("reporting"))
        vids = {v["video_id"]: v for v in data["videos"]}
        self.assertEqual(vids["v_long1"]["impressions"], 2000)
        self.assertAlmostEqual(vids["v_long1"]["ctr"], 0.04)
        self.assertIn("Reporting API", md)

    def test_fallback_creates_job_and_warns(self):
        fake = FakeGoogle("none")
        data, md = self._run(fake)
        self.assertTrue(any(c[0].endswith("/jobs") and isinstance(c[1], dict) and "reportTypeId" in c[1] for c in fake.calls))
        self.assertIn("Campos de impressões/CTR vazios", md)
        self.assertIsNone(data["videos"][0].get("impressions"))

    def test_cache_skips_mature_videos(self):
        fake = FakeGoogle("analytics")
        self._run(fake)
        n_first = sum(1 for c in fake.calls if c[0] == meu_canal.ANALYTICS)
        fake2 = FakeGoogle("analytics")
        self._run(fake2)
        n_second = sum(1 for c in fake2.calls if c[0] == meu_canal.ANALYTICS)
        # v_long1, v_short2 e v_long2 (>35 dias) não são re-consultados
        self.assertLess(n_second, n_first)
        self.assertEqual(n_second, 2 * 4)  # 2 vídeos novos x (métricas + impressões + origem + retenção)

    def test_flag_anuncio(self):
        data, md = self._run(FakeGoogle("analytics"))
        vids = {v["video_id"]: v for v in data["videos"]}
        self.assertEqual(vids["v_long1"]["pct_anuncio"], 0.7)
        self.assertTrue(any("anúncio" in f for f in vids["v_long1"]["flags"]))
        self.assertIn("% views de anúncio", md)

    def test_analise_preservada_entre_rodadas(self):
        _, md = self._run(FakeGoogle("analytics"))
        self.assertIn("<!-- /CLAUDE:tom -->", md)
        md_path = Path(self.tmp.name) / "meu-canal.md"
        filled = md.replace("<!-- /CLAUDE:tom -->", "Fala em segunda pessoa, informal.\n<!-- /CLAUDE:tom -->")
        md_path.write_text(filled, encoding="utf-8")
        _, md2 = self._run(FakeGoogle("analytics"))
        self.assertIn("Fala em segunda pessoa, informal.\n<!-- /CLAUDE:tom -->", md2)
        self.assertEqual(md2.count("Fala em segunda pessoa"), 1)


if __name__ == "__main__":
    unittest.main()
