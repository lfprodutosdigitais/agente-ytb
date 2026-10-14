"""Coleta pela YouTube Data API e descoberta de canais, com cliente Google simulado (sem rede)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import referencias as R  # noqa: E402
import youtube_publico as Y  # noqa: E402

NOW = datetime.now(timezone.utc)
CFG = dict(R.DEFAULTS)


def vid_item(vid, dur="PT10M", views=1000, days=10, live=False, channel="UCx"):
    v = {"id": vid, "snippet": {"title": f"t {vid}", "publishedAt": (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "channelId": channel, "liveBroadcastContent": "none", "thumbnails": {"high": {"url": "th"}},
                                "description": "d"},
         "contentDetails": {"duration": dur}, "statistics": {"viewCount": str(views), "likeCount": "5", "commentCount": "2"}}
    if live:
        v["liveStreamingDetails"] = {"actualStartTime": "x"}
    return v


class FakeG:
    def __init__(self, pages):
        self.pages = pages  # lista de listas de itens de vídeo
        self.calls = []

    def get(self, url, params=None, raw=False):
        p = params or {}
        self.calls.append((url.rsplit("/", 1)[-1], p))
        if url.endswith("/channels"):
            if p.get("forHandle") == "@inexistente":
                return {"items": []}
            if "id" in p and "," in p["id"] or p.get("part") == "snippet,statistics":
                return {"items": [{"id": cid, "snippet": {"title": f"Canal {cid}", "customUrl": f"@{cid.lower()}"},
                                   "statistics": {"subscriberCount": "10000" if cid != "UCbig" else "5000000"}}
                                  for cid in p["id"].split(",")]}
            return {"items": [{"id": "UCx", "snippet": {"title": "Canal X"}, "statistics": {"subscriberCount": "123000"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]}
        if url.endswith("/playlistItems"):
            i = int(p.get("pageToken") or 0)
            items = [{"contentDetails": {"videoId": v["id"]}} for v in self.pages[i]]
            r = {"items": items}
            if i + 1 < len(self.pages):
                r["nextPageToken"] = str(i + 1)
            return r
        if url.endswith("/videos"):
            ids = p["id"].split(",")
            allv = {v["id"]: v for page in self.pages for v in page}
            return {"items": [allv[i] for i in ids if i in allv]}
        if url.endswith("/search"):
            return {"items": [{"id": {"videoId": v["id"]}, "snippet": {"channelId": v["snippet"]["channelId"]}}
                              for page in self.pages for v in page]}
        raise AssertionError(url)


def short_by_dur(vid, dur, mx=180):
    return dur <= mx


class ApiCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["REFERENCIAS_DIR"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_pega_50_longos_pula_shorts_e_lives(self):
        page1 = [vid_item(f"L{i:010d}", days=i) for i in range(30)] + [vid_item(f"S{i:010d}", "PT40S") for i in range(15)] \
            + [vid_item(f"V{i:010d}", live=True) for i in range(5)]
        page2 = [vid_item(f"M{i:010d}", days=40 + i) for i in range(50)]
        g = FakeG([page1, page2])
        recs, units = Y.collect([R.match_channel("@x", [])], CFG, g=g, is_short_fn=short_by_dur)
        self.assertEqual(len(recs), 50)
        self.assertTrue(all(r["formato"] == "longo" for r in recs))
        self.assertFalse(any(r["video_id"].startswith("V") for r in recs))
        self.assertEqual(recs[0]["inscritos_canal"], 123000)
        self.assertEqual(recs[0]["canal"], "Canal X")
        self.assertLess(units, 10)
        videos = json.loads((Path(self.tmp.name) / "videos.json").read_text())
        self.assertEqual(len(videos), 50)
        custos = json.loads((Path(self.tmp.name) / "custos-apify.json").read_text())
        self.assertEqual((custos[-1]["fonte"], custos[-1]["usd"]), ("youtube_api", 0.0))
        # 2ª rodada usa o id do canal em cache (sem forHandle)
        g2 = FakeG([page1, page2])
        Y.collect([R.match_channel("@x", [])], CFG, g=g2, is_short_fn=short_by_dur)
        ch_call = next(p for name, p in g2.calls if name == "channels")
        self.assertEqual(ch_call.get("id"), "UCx")

    def test_canal_inexistente_nao_derruba_os_outros(self):
        g = FakeG([[vid_item(f"L{i:010d}") for i in range(10)]])
        recs, _ = Y.collect([R.match_channel("@inexistente", []), R.match_channel("@x", [])], CFG, g=g,
                            is_short_fn=short_by_dur)
        self.assertEqual(len(recs), 10)

    def test_descobrir_sugere_canais_novos(self):
        g = FakeG([[vid_item("A0000000001", views=200000, channel="UCnovo"),
                    vid_item("A0000000002", views=5000, channel="UCoutro"),
                    vid_item("A0000000003", views=900000, channel="UCbig"),       # grande demais
                    vid_item("A0000000004", views=900000, channel="UCprimorico"),  # já está na lista
                    vid_item("A0000000005", views=900000, channel="UCleo"),        # canal do próprio Léo
                    {**vid_item("A0000000006", views=900000, channel="UCes"),
                     "snippet": {**vid_item("x")["snippet"], "channelId": "UCes", "defaultAudioLanguage": "es"}},
                    {**vid_item("A0000000007", views=900000, channel="UCgame"),
                     "snippet": {**vid_item("x")["snippet"], "channelId": "UCgame", "categoryId": "20"}}]])
        cfg = {**CFG, "termos_descoberta": ["sair do CLT"]}
        sugs, units = Y.discover(cfg, ["@ucprimorico"], g=g, own_channel_id="UCleo")
        self.assertEqual([s["canal"] for s in sugs], ["Canal UCnovo", "Canal UCoutro"])
        self.assertEqual(sugs[0]["videos"][0]["views_por_inscrito"], 20.0)
        self.assertGreaterEqual(units, 100)
        md = (Path(self.tmp.name) / "canais-sugeridos.md").read_text()
        self.assertIn("Canal UCnovo", md)
        self.assertNotIn("UCbig", md)


if __name__ == "__main__":
    unittest.main()
