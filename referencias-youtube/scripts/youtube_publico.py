"""Coleta de canais públicos e descoberta de canais novos pela YouTube Data API v3 (oficial, grátis).

Usa a mesma autorização OAuth do canal do Léo (youtube.readonly também lê dados públicos de qualquer canal).
Cota diária grátis: 10.000 unidades. Custos aproximados:
  - coletar 1 canal (50 longos): ~3–8 unidades (channels 1 + playlistItems 1/50 vídeos + videos 1/50 vídeos)
  - descobrir: 100 unidades por termo de busca + ~1 por 50 vídeos/canais detalhados
"""
from datetime import timedelta

from yt_common import HttpError, log, now, out_dir, parse_iso_duration, read_json, write_json

DATA = "https://www.googleapis.com/youtube/v3"


class Quota:
    def __init__(self):
        self.units = 0

    def add(self, n):
        self.units += n


def resolve_channel(g, chan, quota, cache):
    """@handle -> dados do canal (id, título, inscritos, playlist de uploads). Cacheia o id."""
    key = chan["handle"].lower()
    p = {"part": "snippet,contentDetails,statistics"}
    if cache.get(key):
        p["id"] = cache[key]
    elif chan["handle"].startswith("@"):
        p["forHandle"] = chan["handle"]
    elif "/channel/" in chan["url"]:
        p["id"] = chan["url"].rstrip("/").split("/channel/")[1]
    else:
        p["forHandle"] = "@" + chan["handle"].lstrip("@")
    r = g.get(f"{DATA}/channels", p)
    quota.add(1)
    items = r.get("items") or []
    if not items:
        return None
    c = items[0]
    cache[key] = c["id"]
    return {"id": c["id"], "titulo": c["snippet"]["title"],
            "inscritos": int(c["statistics"]["subscriberCount"]) if "subscriberCount" in c["statistics"] else None,
            "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"]}


def video_details(g, ids, quota):
    out = []
    for i in range(0, len(ids), 50):
        r = g.get(f"{DATA}/videos", {"part": "snippet,contentDetails,statistics,liveStreamingDetails",
                                     "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        quota.add(1)
        out += r.get("items", [])
    return out


def to_record(v, chan, info, cfg, is_short_fn):
    sn, st = v["snippet"], v.get("statistics", {})
    dur = parse_iso_duration(v.get("contentDetails", {}).get("duration"))
    thumbs = sn.get("thumbnails", {})
    return {
        "video_id": v["id"],
        "url": f"https://www.youtube.com/watch?v={v['id']}",
        "titulo": sn.get("title"),
        "canal": info["titulo"] if info else sn.get("channelTitle"),
        "canal_handle": chan["handle"],
        "canal_url": chan["url"],
        "idioma": chan["lang"],
        "formato": "short" if is_short_fn(v["id"], dur, cfg["short_max_segundos"]) else "longo",
        "views": int(st["viewCount"]) if "viewCount" in st else None,
        "likes": int(st["likeCount"]) if "likeCount" in st else None,
        "comentarios": int(st["commentCount"]) if "commentCount" in st else None,
        "data": sn.get("publishedAt"),
        "duracao_s": dur,
        "thumbnail": (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or {}).get("url")
                     or f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg",
        "inscritos_canal": info["inscritos"] if info else None,
        "descricao": (sn.get("description") or "")[:1000],
    }


def is_live(v):
    return bool(v.get("liveStreamingDetails")) or v["snippet"].get("liveBroadcastContent") in ("live", "upcoming")


def collect_channel(g, chan, cfg, quota, cache, is_short_fn):
    """Últimos `max_longos` vídeos longos (e `max_shorts` Shorts) do canal, sem lives."""
    info = resolve_channel(g, chan, quota, cache)
    if not info:
        raise RuntimeError(f"canal não encontrado pela API: {chan['url']}")
    want_long, want_short = cfg["max_longos"], cfg["max_shorts"]
    longs, shorts, token, pages = [], [], None, 0
    while (len(longs) < want_long or len(shorts) < want_short) and pages < cfg["max_paginas_api"]:
        p = {"part": "contentDetails", "playlistId": info["uploads"], "maxResults": 50}
        if token:
            p["pageToken"] = token
        try:
            r = g.get(f"{DATA}/playlistItems", p)
        except HttpError as e:
            if e.status == 404:  # canal sem uploads públicos
                break
            raise
        quota.add(1)
        pages += 1
        ids = [it["contentDetails"]["videoId"] for it in r.get("items", [])]
        for v in video_details(g, ids, quota):
            if is_live(v):
                continue
            rec = to_record(v, chan, info, cfg, is_short_fn)
            if rec["formato"] == "short":
                if len(shorts) < want_short:
                    shorts.append(rec)
            elif len(longs) < want_long:
                longs.append(rec)
        token = r.get("nextPageToken")
        if not token:
            break
    return longs + shorts


def collect(chans, cfg, g=None, is_short_fn=None):
    """Coleta todos os canais pela API, salvando canal por canal. Devolve (records, unidades de cota)."""
    import referencias as R
    if g is None:
        from yt_common import Google
        g = Google()
    if is_short_fn is None:
        if cfg["max_shorts"]:
            from meu_canal import is_short as is_short_fn  # confere /shorts/ só quando Shorts interessam
        else:
            def is_short_fn(vid, dur, mx=180):  # Shorts desligados: duração basta, sem abrir páginas
                return dur <= mx
    p = R.paths()
    cache_f = p["out"] / "cache" / "canais-ids.json"
    cache = read_json(cache_f, {}) or {}
    quota = Quota()
    all_recs, falhas = [], []
    for c in chans:
        log(f"YouTube API: {c['handle']} (até {cfg['max_longos']} longos"
            f"{' + %d Shorts' % cfg['max_shorts'] if cfg['max_shorts'] else ', sem Shorts'})...")
        try:
            recs = collect_channel(g, c, cfg, quota, cache, is_short_fn)
        except (HttpError, RuntimeError, OSError) as e:
            log(f"  FALHOU {c['handle']}: {e}")
            falhas.append(c["handle"])
            continue
        R.store(recs)
        all_recs += recs
        log(f"  {len(recs)} vídeos ({quota.units} unidades de cota até aqui)")
    write_json(cache_f, cache)
    custos = read_json(p["custos"], []) or []
    custos.append({"data": now().isoformat(), "fonte": "youtube_api", "canais": len(chans) - len(falhas),
                   "itens": len(all_recs), "usd": 0.0, "cota_unidades": quota.units})
    write_json(p["custos"], custos)
    if falhas:
        log(f"Canais com falha nesta rodada: {', '.join(falhas)}")
    return all_recs, quota.units


# ---------------------------------------------------------------- descoberta

def discover(cfg, known_handles, g=None, own_channel_id=None):
    """Busca vídeos recentes que explodiram nos termos do nicho e sugere canais fora da lista.

    Critério: views do vídeo ÷ inscritos do canal (vídeo que foi muito além da base do canal), canais entre
    `descobrir_min_inscritos` e `descobrir_max_inscritos`. Grava ./referencias/canais-sugeridos.md e .json."""
    if g is None:
        from yt_common import Google
        g = Google()
    quota = Quota()
    after = (now() - timedelta(days=cfg["descobrir_dias"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    for termo in cfg["termos_descoberta"]:
        r = g.get(f"{DATA}/search", {"part": "snippet", "q": termo, "type": "video", "order": "viewCount",
                                     "publishedAfter": after, "regionCode": "BR", "relevanceLanguage": "pt",
                                     "videoDuration": "medium", "maxResults": 50})
        quota.add(100)
        for it in r.get("items", []):
            vid = it["id"].get("videoId")
            if vid:
                found.setdefault(vid, {"termo": termo, "canal_id": it["snippet"]["channelId"]})
    vids = video_details(g, list(found), quota)
    chan_ids = sorted({v["snippet"]["channelId"] for v in vids})
    chans = {}
    for i in range(0, len(chan_ids), 50):
        r = g.get(f"{DATA}/channels", {"part": "snippet,statistics", "id": ",".join(chan_ids[i:i + 50])})
        quota.add(1)
        for c in r.get("items", []):
            chans[c["id"]] = c
    known = {h.lower().lstrip("@") for h in known_handles}
    agg = {}
    for v in vids:
        sn = v["snippet"]
        if sn.get("categoryId") == "20":  # Games
            continue
        lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").lower()
        if lang and not lang.startswith(("pt", "en")):
            continue
        cid = sn["channelId"]
        c = chans.get(cid)
        if not c or cid == own_channel_id:
            continue
        handle = (c["snippet"].get("customUrl") or "").lstrip("@")
        if handle.lower() in known:
            continue
        subs = int(c["statistics"].get("subscriberCount") or 0)
        if not (cfg["descobrir_min_inscritos"] <= subs <= cfg["descobrir_max_inscritos"]):
            continue
        views = int(v.get("statistics", {}).get("viewCount") or 0)
        ratio = views / subs if subs else 0
        a = agg.setdefault(cid, {"canal": c["snippet"]["title"], "handle": "@" + handle if handle else None,
                                 "url": f"https://www.youtube.com/{'@' + handle if handle else 'channel/' + cid}",
                                 "inscritos": subs, "videos": [], "melhor_razao": 0})
        a["videos"].append({"titulo": v["snippet"]["title"], "url": f"https://www.youtube.com/watch?v={v['id']}",
                            "views": views, "views_por_inscrito": round(ratio, 2), "termo": found[v["id"]]["termo"]})
        a["melhor_razao"] = max(a["melhor_razao"], ratio)
    sugestoes = sorted(agg.values(), key=lambda a: (-a["melhor_razao"], -len(a["videos"])))[:cfg["descobrir_top"]]
    for s in sugestoes:
        s["videos"].sort(key=lambda x: -x["views_por_inscrito"])
        s["melhor_razao"] = round(s["melhor_razao"], 2)
    o = out_dir()
    write_json(o / "canais-sugeridos.json", {"gerado_em": now().isoformat(), "cota_unidades": quota.units,
                                             "termos": cfg["termos_descoberta"], "sugestoes": sugestoes})
    L = ["# Canais sugeridos", "",
         f"_Gerado em {now():%Y-%m-%d} buscando vídeos dos últimos {cfg['descobrir_dias']} dias: "
         f"{', '.join(cfg['termos_descoberta'])}._", "",
         "Critério: vídeo com muitas views em relação aos inscritos do canal (foi além da própria base). "
         "Para acompanhar um canal, copie a URL para o `canais.txt` (acrescente `| en` se for em inglês).", "",
         "| Canal | Inscritos | Melhor vídeo | Views/inscrito | Vídeos achados |", "|---|---|---|---|---|"]
    for s in sugestoes:
        top = s["videos"][0]
        L.append(f"| [{s['canal']}]({s['url']}) | {s['inscritos']:,} | [{top['titulo'][:60]}]({top['url']}) "
                 f"| {top['views_por_inscrito']}x | {len(s['videos'])} |".replace(",", "."))
    (o / "canais-sugeridos.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return sugestoes, quota.units
