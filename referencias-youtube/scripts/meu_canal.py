"""FASE 1 — Meu canal (API oficial do Google).

Puxa todos os vídeos do canal autorizado (Data API v3), as métricas por vídeo (Analytics API v2),
curva de retenção, impressões/CTR (Analytics -> fallback Reporting API) e transcrições (yt-dlp).
Gera ./referencias/meu-canal-dados.json e ./referencias/meu-canal.md (parte numérica; o Claude
completa a parte qualitativa — tom e temas — lendo as transcrições).

Uso:
  python3 scripts/meu_canal.py --check        # só testa o token
  python3 scripts/meu_canal.py                # rodada normal (usa cache)
  python3 scripts/meu_canal.py --refresh      # força re-puxar métricas de todos os vídeos
  python3 scripts/meu_canal.py --no-transcripts
"""
import argparse
import csv
import io
import re
import urllib.error
import urllib.request
from datetime import timedelta

from yt_common import (Google, HttpError, fmt_time, load_config, log, median, now, out_dir,
                       parse_date, parse_iso_duration, read_json, write_json)

DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
REPORTING = "https://youtubereporting.googleapis.com/v1"

BASE_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "subscribersGained"]
IMPRESSION_METRICS = ["videoThumbnailImpressions", "videoThumbnailImpressionsClickRate"]
MATURE_DAYS = 35  # depois disso as métricas do vídeo quase não mudam: fica no cache


# ---------------------------------------------------------------- Data API

def my_channel(g):
    r = g.get(f"{DATA}/channels", {"part": "snippet,contentDetails,statistics", "mine": "true"})
    if not r.get("items"):
        raise SystemExit("Nenhum canal nessa conta. Se o canal é uma Brand Account, escolha-a na tela de login.")
    return r["items"][0]


def list_upload_ids(g, uploads_playlist):
    ids, token = [], None
    while True:
        p = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
        if token:
            p["pageToken"] = token
        r = g.get(f"{DATA}/playlistItems", p)
        ids += [it["contentDetails"]["videoId"] for it in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            return ids


def video_details(g, ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = g.get(f"{DATA}/videos", {"part": "snippet,contentDetails,statistics,status",
                                     "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        for v in r.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            out[v["id"]] = {
                "video_id": v["id"],
                "title": sn.get("title"),
                "description": sn.get("description", "")[:1500],
                "tags": sn.get("tags", []),
                "published_at": sn.get("publishedAt"),
                "duration_s": parse_iso_duration(v["contentDetails"].get("duration")),
                "thumbnail": (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or {}).get("url"),
                "privacy": v.get("status", {}).get("privacyStatus"),
                "live": sn.get("liveBroadcastContent") != "none" or "liveStreamingDetails" in v,
                "public_views": int(st.get("viewCount", 0)),
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
            }
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def is_short(video_id, duration_s, max_short_s=180):
    """youtube.com/shorts/ID responde 200 para Shorts e redireciona (303) para vídeos longos."""
    if duration_s > max_short_s:
        return False
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(f"https://www.youtube.com/shorts/{video_id}", method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=15) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return False
    except Exception:
        pass
    return duration_s <= max_short_s


# ---------------------------------------------------------------- Analytics API

def analytics(g, metrics, start, end, video_id=None, dimensions=None, sort=None):
    p = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": ",".join(metrics)}
    if video_id:
        p["filters"] = f"video=={video_id}"
    if dimensions:
        p["dimensions"] = dimensions
    if sort:
        p["sort"] = sort
    r = g.get(ANALYTICS, p)
    cols = [c["name"] for c in r.get("columnHeaders", [])]
    return [dict(zip(cols, row)) for row in r.get("rows", []) or []]


def metrics_window(g, video_id, start_date, end_date, with_impressions=True):
    """Métricas de um vídeo num intervalo (usado também na Fase 4: 7 e 30 dias)."""
    rows = analytics(g, BASE_METRICS, start_date, end_date, video_id)
    m = rows[0] if rows else {k: 0 for k in BASE_METRICS}
    if with_impressions:
        try:
            r2 = analytics(g, IMPRESSION_METRICS, start_date, end_date, video_id)
            if r2:
                m["impressions"] = r2[0].get("videoThumbnailImpressions")
                m["ctr"] = r2[0].get("videoThumbnailImpressionsClickRate")
        except HttpError as e:
            if e.status not in (400, 403):
                raise
            m["impressions_error"] = e.body[:300]
    return m


def traffic_sources(g, video_id, start, end):
    """Views por origem de tráfego; ADVERTISING = views pagas (Google Ads)."""
    try:
        rows = analytics(g, ["views"], start, end, video_id, dimensions="insightTrafficSourceType", sort="-views")
    except HttpError as e:
        if e.status != 400:
            raise
        return {}
    return {r["insightTrafficSourceType"]: r["views"] for r in rows}


TRAFFIC_NAMES = {
    "SUBSCRIBER": "Navegação (página inicial/feed)", "RELATED_VIDEO": "Sugeridos", "YT_SEARCH": "Busca no YouTube",
    "YT_CHANNEL": "Página do canal", "EXT_URL": "Links externos", "NO_LINK_OTHER": "Direto/desconhecido",
    "YT_OTHER_PAGE": "Outras páginas do YouTube", "PLAYLIST": "Playlists", "NOTIFICATION": "Notificações",
    "ANNOTATION": "Cards", "END_SCREEN": "Tela final", "ADVERTISING": "Anúncio (Google Ads)", "SHORTS": "Feed de Shorts",
    "HASHTAGS": "Hashtags", "PROMOTED": "Promovido", "NO_LINK_EMBEDDED": "Player incorporado", "CAMPAIGN_CARD": "Campanha",
}


def top_sources(sources, n=3):
    total = sum(sources.values())
    if not total:
        return "—"
    top = sorted(sources.items(), key=lambda kv: -kv[1])[:n]
    return " · ".join(f"{TRAFFIC_NAMES.get(k, k)} {v / total * 100:.0f}%" for k, v in top)


def ad_share(sources):
    total = sum(sources.values())
    return round(sources.get("ADVERTISING", 0) / total, 3) if total else None


def retention_curve(g, video_id, start, end):
    try:
        rows = analytics(g, ["audienceWatchRatio", "relativeRetentionPerformance"], start, end, video_id,
                         dimensions="elapsedVideoTimeRatio", sort="elapsedVideoTimeRatio")
    except HttpError as e:
        if e.status != 400:
            raise
        rows = analytics(g, ["audienceWatchRatio"], start, end, video_id,
                         dimensions="elapsedVideoTimeRatio", sort="elapsedVideoTimeRatio")
    return [[round(r["elapsedVideoTimeRatio"], 4), round(r["audienceWatchRatio"], 4),
             round(r["relativeRetentionPerformance"], 4) if r.get("relativeRetentionPerformance") is not None else None]
            for r in rows]


# ---------------------------------------------------------------- Reporting API (fallback de impressões/CTR)

def reporting_impressions(g, cache_dir):
    """Soma impressões e CTR por vídeo a partir dos reports de 'reach' da Reporting API.

    Retorna (dict video_id -> {impressions, ctr}, mensagem de status)."""
    try:
        types = g.get(f"{REPORTING}/reportTypes").get("reportTypes", [])
    except HttpError as e:
        return {}, f"Reporting API indisponível ({e.status}). Ative 'YouTube Reporting API' no projeto do Google Cloud."
    reach = [t for t in types if "reach" in t["id"] and "channel" in t["id"]]
    if not reach:
        return {}, "Reporting API não oferece report de reach para este canal."
    rtype = sorted(reach, key=lambda t: ("combined" in t["id"], t["id"]))[0]["id"]
    jobs = g.get(f"{REPORTING}/jobs").get("jobs", [])
    job = next((j for j in jobs if j["reportTypeId"] == rtype), None)
    if not job:
        job = g.post(f"{REPORTING}/jobs", {"reportTypeId": rtype, "name": "referencias-youtube-reach"})
        return {}, (f"Criei o job '{rtype}' na Reporting API. O YouTube leva ~48h para gerar o primeiro report; "
                    "na próxima rodada impressões/CTR devem aparecer.")
    reports, token = [], None
    while True:
        r = g.get(f"{REPORTING}/jobs/{job['id']}/reports", {"pageToken": token} if token else None)
        reports += r.get("reports", [])
        token = r.get("nextPageToken")
        if not token:
            break
    if not reports:
        return {}, f"Job '{rtype}' existe, mas ainda sem reports gerados (aguarde até 48h)."
    agg = {}
    for rep in reports:
        f = cache_dir / f"{rep['id']}.csv"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(g.get(rep["downloadUrl"], raw=True))
        merge_reach_csv(f.read_text(encoding="utf-8"), agg)
    out = {vid: {"impressions": a["imp"], "ctr": round(a["clicks"] / a["imp"], 4) if a["imp"] else None}
           for vid, a in agg.items()}
    return out, f"Impressões/CTR via Reporting API ({rtype}, {len(reports)} reports diários)."


def merge_reach_csv(text, agg):
    rd = csv.DictReader(io.StringIO(text))
    cols = rd.fieldnames or []
    imp_col = next((c for c in cols if "impressions" in c and "ctr" not in c and "rate" not in c), None)
    ctr_col = next((c for c in cols if "impressions" in c and ("ctr" in c or "rate" in c)), None)
    if "video_id" not in cols or not imp_col:
        return
    for row in rd:
        try:
            imp = float(row[imp_col] or 0)
            ctr = float(row[ctr_col] or 0) if ctr_col else 0
        except ValueError:
            continue
        if ctr > 1:  # alguns reports vêm em %
            ctr /= 100
        a = agg.setdefault(row["video_id"], {"imp": 0, "clicks": 0})
        a["imp"] += imp
        a["clicks"] += imp * ctr


# ---------------------------------------------------------------- análise

def ratio_at(curve, x):
    """Interpola audienceWatchRatio na posição x (0-1) do vídeo."""
    if not curve:
        return None
    pts = [(c[0], c[1]) for c in curve]
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0) if x1 > x0 else 0)
    return pts[-1][1]


def retention_summary(curve, duration_s, is_short_video):
    """Resumo da curva: retenção no gancho e onde estão as maiores quedas."""
    if not curve or not duration_s:
        return {}
    hook_s = 3 if is_short_video else 30
    start = curve[0][1]
    r_hook = ratio_at(curve, min(hook_s / duration_s, 1))
    out = {
        "hook_s": hook_s,
        "ret_inicio": round(start, 3),
        "ret_gancho": round(r_hook, 3) if r_hook is not None else None,
        "ret_meio": round(ratio_at(curve, 0.5), 3),
        "ret_fim": round(ratio_at(curve, 0.95), 3),
    }
    # maiores quedas depois do gancho (janelas de ~3% do vídeo), ignorando os 5% finais (tela final)
    drops = []
    lo = hook_s / duration_s
    for i in range(len(curve) - 3):
        x0, y0 = curve[i][0], curve[i][1]
        x1, y1 = curve[i + 3][0], curve[i + 3][1]
        if x0 >= lo and x1 <= 0.95:
            drops.append((y0 - y1, x0))
    picked = []
    for d, x in sorted(drops, reverse=True):
        if d <= 0.02:
            break
        if all(abs(x - p[1]) > 0.08 for p in picked):
            picked.append((d, x))
        if len(picked) == 3:
            break
    out["quedas"] = [{"em": fmt_time(x * duration_s), "pos": round(x, 3), "queda": round(d, 3)} for d, x in picked]
    return out


def build(videos, reach_msg=None):
    """Calcula medianas por formato, razão vs mediana e flags de retenção. Pura (testável)."""
    for fmt in ("short", "longo"):
        vs = [v for v in videos if v["formato"] == fmt and v.get("views") is not None]
        med = median([v["views"] for v in vs])
        med_hook = median([(v.get("retencao") or {}).get("ret_gancho") for v in vs])
        med_avgpct = median([v.get("averageViewPercentage") for v in vs])
        med_ctr = median([v.get("ctr") for v in vs])
        for v in vs:
            v["mediana_formato"] = med
            v["vs_mediana"] = round(v["views"] / med, 2) if med else None
            ret = v.get("retencao") or {}
            flags = []
            if (v.get("pct_anuncio") or 0) >= 0.3:
                flags.append(f"{v['pct_anuncio'] * 100:.0f}% das views vieram de anúncio (retenção e % assistida distorcidas)")
            if ret.get("ret_gancho") is not None and med_hook and ret["ret_gancho"] < med_hook * 0.9:
                flags.append("gancho fraco (retenção no gancho abaixo da sua mediana)")
            if fmt == "longo" and ret.get("ret_gancho") is not None and ret["ret_gancho"] < 0.6:
                flags.append("queda forte nos primeiros 30 s")
            if v.get("averageViewPercentage") and med_avgpct and v["averageViewPercentage"] > med_avgpct * 1.15:
                flags.append("% assistida acima da mediana")
            if v.get("ctr") and med_ctr and v["ctr"] > med_ctr * 1.2:
                flags.append("CTR acima da mediana")
            if v.get("ctr") and med_ctr and v["ctr"] < med_ctr * 0.8:
                flags.append("CTR abaixo da mediana")
            v["flags"] = flags
    stats = {}
    for fmt in ("short", "longo"):
        vs = [v for v in videos if v["formato"] == fmt]
        stats[fmt] = {
            "qtd": len(vs),
            "mediana_views": median([v.get("views") for v in vs]),
            "mediana_pct_assistida": median([v.get("averageViewPercentage") for v in vs]),
            "mediana_duracao_media_s": median([v.get("averageViewDuration") for v in vs]),
            "mediana_ret_gancho": median([(v.get("retencao") or {}).get("ret_gancho") for v in vs]),
            "mediana_ctr": median([v.get("ctr") for v in vs]),
            "mediana_inscritos": median([v.get("subscribersGained") for v in vs]),
            "mediana_pct_anuncio": median([v.get("pct_anuncio") for v in vs]),
        }
    return {"stats": stats, "impressoes_status": reach_msg}


def _n(x, pct=False, dec=0):
    if x is None:
        return "—"
    if pct:
        return f"{x * 100:.1f}%" if x <= 1 else f"{x:.1f}%"
    return f"{x:,.{dec}f}".replace(",", ".")


BLOCK_RE = re.compile(r"<!-- CLAUDE:([\w-]+) .*?-->\n(.*?)<!-- /CLAUDE:\1 -->", re.S)


def previous_blocks(md_text):
    """Análises já escritas numa rodada anterior, por chave de bloco."""
    return {k: body.strip() for k, body in BLOCK_RE.findall(md_text or "") if body.strip()}


def block(key, instr, prev):
    body = prev.get(key, "")
    return [f"<!-- CLAUDE:{key} {instr} -->"] + ([body] if body else []) + [f"<!-- /CLAUDE:{key} -->", ""]


def render_md(channel, videos, summary, today, prev=None):
    prev = prev or {}
    st = summary["stats"]
    ninety = today - timedelta(days=90)
    L = [f"# Meu canal — {channel['title']}", "",
         f"_Atualizado em {today:%Y-%m-%d}. Inscritos: {_n(channel.get('subs'))}. Vídeos públicos analisados: {len(videos)}._", ""]
    if summary.get("impressoes_status"):
        L += [f"> **Impressões/CTR:** {summary['impressoes_status']}", ""]

    L += ["## Formato", "", "| Formato | Qtd | Mediana views | % views de anúncio (mediana) | % assistida (mediana) | Duração média (mediana) | Ret. no gancho (mediana) | CTR (mediana) | Inscritos/vídeo (mediana) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for fmt, nome in (("longo", "Longo"), ("short", "Shorts")):
        s = st[fmt]
        L.append(f"| {nome} | {s['qtd']} | {_n(s['mediana_views'])} | {_n(s['mediana_pct_anuncio'], pct=True)} | {_n(s['mediana_pct_assistida'], pct=True)} | "
                 f"{fmt_time(s['mediana_duracao_media_s']) if s['mediana_duracao_media_s'] else '—'} | "
                 f"{_n(s['mediana_ret_gancho'], pct=True)} | {_n(s['mediana_ctr'], pct=True)} | {_n(s['mediana_inscritos'])} |")
    L += [""] + block("formato", "2-3 frases sobre o formato dominante e o que os números dizem (separar views pagas de orgânicas).", prev)

    L += ["## Tom e jeito de falar", ""] + block("tom", "ler ./referencias/transcricoes/: pessoa (eu/você), vocabulário, ritmo, "
                                                 "como abre os vídeos, bordões, nível de prova (números, prints).", prev)

    L += ["## Temas", ""] + block("temas", "agrupar os vídeos por tema (sair do CLT, renda extra, negócio/empreender, investimento, "
                                    "mentalidade, história pessoal, dívida/orçamento, notícia, outro) com views medianas por tema.", prev)

    for fmt, nome in (("longo", "Longos"), ("short", "Shorts")):
        vs = sorted([v for v in videos if v["formato"] == fmt and v.get("vs_mediana")], key=lambda v: -v["vs_mediana"])
        if not vs:
            continue
        acima = [v for v in vs if v["vs_mediana"] > 1]
        L += [f"## {nome} acima da mediana ({len(acima)} de {len(vs)})", "",
              "| Vídeo | Publicado | Views | % anúncio | x mediana | % assistida | Ret. gancho | CTR | Inscritos | Sinais |", "|---|---|---|---|---|---|---|---|---|---|"]
        for v in acima:
            ret = v.get("retencao") or {}
            L.append(f"| [{v['title']}](https://youtu.be/{v['video_id']}) | {v['published_at'][:10]} | {_n(v['views'])} | "
                     f"{_n(v.get('pct_anuncio'), pct=True)} | {v['vs_mediana']}x | {_n(v.get('averageViewPercentage'), pct=True)} | {_n(ret.get('ret_gancho'), pct=True)} | "
                     f"{_n(v.get('ctr'), pct=True)} | {_n(v.get('subscribersGained'))} | {'; '.join(v.get('flags', []))} |")
        L += [""] + block(f"acima-{fmt}", f"por que os {nome.lower()} acima foram bem? Separar CLIQUE (título/thumb/CTR) de "
                          "RETENÇÃO (gancho/tema/roteiro). Citar o gancho literal das transcrições.", prev)

    L += ["## De onde vêm as views", "", "| Vídeo | Principais origens |", "|---|---|"]
    for v in sorted(videos, key=lambda v: v["published_at"], reverse=True):
        src = v.get("origem_trafego") or {}
        L.append(f"| [{v['title'][:70]}](https://youtu.be/{v['video_id']}) | "
                 f"{top_sources(src) if src else 'ainda sem dados (vídeo com menos de ~3 dias)'} |")
    L += [""]

    L += ["## Onde a retenção cai", "", "| Vídeo | Formato | % anúncio | Ret. no gancho | Meio | 95% | Maiores quedas (tempo, pontos) |", "|---|---|---|---|---|---|---|"]
    for v in sorted(videos, key=lambda v: (v.get("retencao") or {}).get("ret_gancho") or 9):
        ret = v.get("retencao") or {}
        if not ret:
            continue
        quedas = ", ".join(f"{q['em']} (-{q['queda'] * 100:.0f})" for q in ret.get("quedas", [])) or "—"
        L.append(f"| [{v['title'][:70]}](https://youtu.be/{v['video_id']}) | {v['formato']} | {_n(v.get('pct_anuncio'), pct=True)} | "
                 f"{_n(ret.get('ret_gancho'), pct=True)} (em {ret.get('hook_s')}s) | {_n(ret.get('ret_meio'), pct=True)} | "
                 f"{_n(ret.get('ret_fim'), pct=True)} | {quedas} |")
    L += ["", "_Retenção = audienceWatchRatio (fração da audiência que ainda está assistindo; em Shorts pode passar de 100% por causa do loop)._", "",
          ] + block("retencao", "padrão das quedas (gancho, transição, momento de 'venda', enrolação) olhando o que é dito "
                    "nas transcrições nesses tempos. Regras práticas pra roteiro.", prev)

    L += ["## Cobertos nos últimos 3 meses", ""]
    recent = [v for v in videos if parse_date(v["published_at"]) >= ninety]
    for v in sorted(recent, key=lambda v: v["published_at"], reverse=True):
        L.append(f"- {v['published_at'][:10]} [{v['formato']}] {v['title']} — {_n(v.get('views'))} views")
    if not recent:
        L.append("- (nenhum vídeo nos últimos 90 dias)")
    L += [""] + block("cobertos", "lista de TEMAS cobertos (a Fase 2 usa para não repetir).", prev)
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def run(args):
    cfg = load_config()
    g = Google()
    ch = my_channel(g)
    channel = {"id": ch["id"], "title": ch["snippet"]["title"], "subs": int(ch["statistics"].get("subscriberCount", 0)),
               "uploads": ch["contentDetails"]["relatedPlaylists"]["uploads"]}
    log(f"Canal: {channel['title']} ({channel['id']}) — {channel['subs']} inscritos")
    if args.check:
        today = now().date().isoformat()
        rows = analytics(g, ["views"], (now() - timedelta(days=28)).date().isoformat(), today)
        log(f"Analytics OK: {rows[0]['views'] if rows else 0} views nos últimos 28 dias.")
        return

    out = out_dir()
    cache_dir = out / "cache" / "meu-canal"
    ids = list_upload_ids(g, channel["uploads"])
    log(f"{len(ids)} uploads encontrados")
    details = video_details(g, ids)
    today = now()
    end = today.date().isoformat()
    impressions_supported = True
    max_short = cfg.get("short_max_segundos", 180)
    videos = []
    for i, vid in enumerate(ids, 1):
        d = details.get(vid)
        if not d or d["privacy"] != "public":
            continue
        cache_f = cache_dir / f"{vid}.json"
        c = read_json(cache_f, {}) or {}
        pub = parse_date(d["published_at"])
        age_at_fetch = (parse_date(c["fetched_at"]) - pub).days if c.get("fetched_at") else -1
        fresh = c.get("fetched_at") and age_at_fetch >= MATURE_DAYS and not args.refresh
        v = {**c, **d}
        if "formato" not in c:
            v["formato"] = "short" if is_short(vid, d["duration_s"], max_short) else "longo"
        if not fresh:
            log(f"  [{i}/{len(ids)}] métricas: {d['title'][:60]}")
            start = pub.date().isoformat()
            m = metrics_window(g, vid, start, end, with_impressions=impressions_supported)
            if "impressions_error" in m:
                impressions_supported = False
                v["impressions_error"] = m.pop("impressions_error")
            v.update({k: m.get(k) for k in BASE_METRICS})
            if m.get("impressions") is not None:
                v["impressions"], v["ctr"] = m["impressions"], m.get("ctr")
                if v["ctr"] and v["ctr"] > 1:  # API devolve em %
                    v["ctr"] = round(v["ctr"] / 100, 4)
            v["views"] = v.get("views") or d["public_views"]
            v["origem_trafego"] = traffic_sources(g, vid, start, end)
            v["pct_anuncio"] = ad_share(v["origem_trafego"])
            curve = retention_curve(g, vid, start, end)
            v["retencao_curva"] = curve
            v["retencao"] = retention_summary(curve, d["duration_s"], v["formato"] == "short")
            v["fetched_at"] = today.isoformat()
            write_json(cache_f, v)
        videos.append(v)

    reach_msg = None
    if not any(v.get("impressions") is not None for v in videos):
        log("Impressões/CTR não vieram da Analytics API; tentando a Reporting API...")
        reach, reach_msg = reporting_impressions(g, out / "cache" / "reporting")
        for v in videos:
            if v["video_id"] in reach:
                v.update(reach[v["video_id"]])
        if not reach:
            reach_msg = "Campos de impressões/CTR vazios. " + reach_msg
        log(reach_msg)

    if not args.no_transcripts:
        import transcricao
        for v in videos:
            try:
                p = transcricao.fetch(v["video_id"], v["title"])
                v["transcricao"] = str(p) if p else None
                if p:
                    v["gancho_literal"] = transcricao.first_seconds(transcricao.read_cues(p), 15)
            except Exception as e:  # transcrição nunca derruba a rodada
                log(f"  transcrição falhou para {v['video_id']}: {e}")

    summary = build(videos, reach_msg)
    write_json(out / "meu-canal-dados.json", {"channel": channel, "gerado_em": today.isoformat(), **summary,
                                               "videos": [{k: v for k, v in x.items() if k != "retencao_curva"} for x in videos]})
    md = out / "meu-canal.md"
    prev = previous_blocks(md.read_text(encoding="utf-8")) if md.exists() else {}
    md.write_text(render_md(channel, videos, summary, today, prev), encoding="utf-8")
    log(f"OK: {md}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-transcripts", action="store_true")
    run(ap.parse_args())
