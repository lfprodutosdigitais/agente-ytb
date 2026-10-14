"""FASE 2 — Referências: coleta (Apify), histórico, outliers por vizinhança, em alta, engajamento,
transcrições e temas validados. A parte de julgamento (relevância, análise, ideias) é feita pelo Claude,
que grava ./referencias/analises/{video_id}.json — ver SKILL.md.

Uso:
  python3 scripts/referencias.py teste @primorico   # coleta 1 canal (e, no Apify, mostra custo + estimativa)
  python3 scripts/referencias.py coletar            # todos os canais de canais.txt, depois calcula
  python3 scripts/referencias.py descobrir          # sugere canais novos (busca da API) em canais-sugeridos.md
  python3 scripts/referencias.py calcular           # só recalcula scores com os dados já coletados
  python3 scripts/referencias.py transcrever        # baixa legendas dos candidatos com relevância >= 3
  python3 scripts/referencias.py temas              # recalcula temas validados a partir das análises
"""
import argparse
import re
import time
import urllib.parse
from datetime import timedelta

from yt_common import (SKILL_DIR, HttpError, http, load_config, load_env, log, median, now, out_dir,
                       parse_date, read_json, write_json)

APIFY = "https://api.apify.com/v2"
DEFAULTS = {
    "fonte": "youtube_api",  # "youtube_api" (oficial, grátis) ou "apify" (reserva, pago)
    "max_paginas_api": 6,
    "termos_descoberta": ["sair do CLT", "renda extra", "como ganhar dinheiro", "primeiro negócio",
                          "low ticket", "largar o emprego", "empreender do zero", "dinheiro na internet"],
    "descobrir_dias": 90,
    "descobrir_min_inscritos": 5000,
    "descobrir_max_inscritos": 2000000,
    "descobrir_top": 20,
    "apify_actor": "streamers~youtube-scraper",
    "apify_input_extra": {},
    "max_longos": 50,
    "max_shorts": 0,
    "reaproveitar_horas": 20,
    "short_max_segundos": 180,
    "vizinhos": 10,
    "corte_longo": 3.0,
    "corte_short": 5.0,
    "idade_minima_dias": 7,
    "em_alta_fator": 3.0,
    "em_alta_max_dias": 30,
    "em_alta_min_pares": 3,
    "engajamento_peso": 0.25,
    "engajamento_bonus_max": 0.5,
    "relevancia_minima": 3,
    "tema_validado_canais": 2,
    "tema_validado_dias": 90,
}


def config():
    return {**DEFAULTS, **load_config()}


# ---------------------------------------------------------------- canais

def read_channels(path=None):
    """canais.txt: uma URL por linha; '| en' marca canal em inglês; '#' comenta."""
    path = path or SKILL_DIR / "canais.txt"
    chans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        lang = "en" if len(parts) > 1 and parts[1].lower().startswith("en") else "pt"
        handle = url.rstrip("/").split("/")[-1]
        chans.append({"url": url, "handle": handle, "lang": lang})
    return chans


def match_channel(arg, chans):
    a = arg.lower().lstrip("@")
    for c in chans:
        if c["handle"].lower().lstrip("@") == a or c["url"].lower() == arg.lower():
            return c
    return {"url": arg if arg.startswith("http") else f"https://www.youtube.com/@{a}",
            "handle": "@" + a, "lang": "pt"}


# ---------------------------------------------------------------- Apify

def _apify_error(e):
    if e.status == 401:
        raise SystemExit("Token do Apify inválido. Copie de novo em https://console.apify.com/settings/integrations "
                         "e cole na linha APIFY_TOKEN= do arquivo ~/.claude/skills/referencias-youtube/.env "
                         "(sem espaços nem aspas).")
    if e.status == 402:
        raise SystemExit("Apify sem crédito/plano para rodar o actor. Veja https://console.apify.com/billing")
    raise e


def apify_start(token, actor, run_input):
    """Dispara o run e volta na hora (sem retry: repetir um POST criaria um run pago duplicado)."""
    try:
        return http("POST", f"{APIFY}/acts/{actor}/runs", params={"token": token}, data=run_input,
                    retries=1, timeout=60)["data"]
    except HttpError as e:
        _apify_error(e)


def apify_wait(token, run, timeout_s=1800):
    """Espera o run terminar consultando de tempos em tempos e devolve (run, items)."""
    t0 = time.time()
    while run["status"] in ("READY", "RUNNING"):
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"Apify demorou demais (run {run['id']}). Veja https://console.apify.com/actors/runs/{run['id']}")
        run = http("GET", f"{APIFY}/actor-runs/{run['id']}", params={"token": token, "waitForFinish": 45},
                   timeout=120)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify terminou com status {run['status']}: https://console.apify.com/actors/runs/{run['id']}")
    items = http("GET", f"{APIFY}/datasets/{run['defaultDatasetId']}/items",
                 params={"token": token, "clean": "true", "format": "json"}, timeout=180)
    # o custo final às vezes só consolida segundos depois do fim
    if not run.get("usageTotalUsd"):
        time.sleep(3)
        run = http("GET", f"{APIFY}/actor-runs/{run['id']}", params={"token": token})["data"]
    return run, items


def _norm_url(u):
    return urllib.parse.unquote(u or "").rstrip("/").lower()


def recent_runs(token, actor, hours):
    """Runs recentes (sucesso ou em andamento) por URL do canal, para reaproveitar sem pagar de novo."""
    if not hours:
        return {}
    try:
        lst = http("GET", f"{APIFY}/acts/{actor}/runs", params={"token": token, "desc": 1, "limit": 100})["data"]["items"]
    except HttpError as e:
        _apify_error(e)
    lim = now() - timedelta(hours=hours)
    out = {}
    for r in lst:
        if r.get("status") not in ("SUCCEEDED", "RUNNING", "READY") or not r.get("startedAt"):
            continue
        if parse_date(r["startedAt"]) < lim:
            continue
        if not r.get("defaultKeyValueStoreId"):
            r = http("GET", f"{APIFY}/actor-runs/{r['id']}", params={"token": token})["data"]
        try:
            inp = http("GET", f"{APIFY}/key-value-stores/{r['defaultKeyValueStoreId']}/records/INPUT",
                       params={"token": token})
        except HttpError:
            continue
        for su in (inp or {}).get("startUrls", []):
            out.setdefault(_norm_url(su.get("url")), r)  # lista vem do mais novo pro mais antigo
    return out


def channel_input(chan, cfg):
    url = urllib.parse.quote(chan["url"], safe=":/@?=&")
    return {"startUrls": [{"url": url}], "maxResults": cfg["max_longos"], "maxResultsShorts": cfg["max_shorts"],
            "maxResultStreams": 0, **cfg["apify_input_extra"]}


def _first(d, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _int(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip().lower().replace(",", "").replace(" ", "")
    m = re.match(r"([\d.]+)([kmb])?", s)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2), 1))


def _duration(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    parts = [int(p) for p in str(x).split(":") if p.isdigit()]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs if parts else None


def _video_id(item):
    vid = _first(item, "id", "videoId")
    if vid:
        return vid
    url = item.get("url") or ""
    m = re.search(r"(?:v=|/shorts/|youtu\.be/)([\w-]{11})", url)
    return m.group(1) if m else None


def normalize(item, chan, cfg):
    """Item do Apify -> registro padrão. Tolerante a variações de nome de campo do actor."""
    vid = _video_id(item)
    if not vid:
        return None
    url = item.get("url") or f"https://www.youtube.com/watch?v={vid}"
    dur = _duration(_first(item, "duration", "lengthSeconds", "durationSeconds"))
    typ = str(_first(item, "type", "videoType") or "").lower()
    is_short = "short" in typ or "/shorts/" in url or (not typ and dur is not None and dur <= cfg["short_max_segundos"])
    return {
        "video_id": vid,
        "url": url,
        "titulo": item.get("title"),
        "canal": _first(item, "channelName", "channelTitle") or chan["handle"],
        "canal_handle": chan["handle"],
        "canal_url": chan["url"],
        "idioma": chan["lang"],
        "formato": "short" if is_short else "longo",
        "views": _int(_first(item, "viewCount", "views")),
        "likes": _int(_first(item, "likes", "likeCount")),
        "comentarios": _int(_first(item, "commentsCount", "commentCount", "comments")),
        "data": _first(item, "date", "uploadDate", "publishedAt"),
        "duracao_s": dur,
        "thumbnail": _first(item, "thumbnailUrl", "thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "inscritos_canal": _int(_first(item, "numberOfSubscribers", "channelSubscribers", "subscriberCount")),
        "descricao": (item.get("text") or item.get("description") or "")[:1000],
    }


# ---------------------------------------------------------------- armazenamento

def paths():
    o = out_dir()
    return {"videos": o / "videos.json", "historico": o / "historico.json", "candidatos": o / "candidatos.json",
            "analises": o / "analises", "temas": o / "temas-validados.json", "custos": o / "custos-apify.json",
            "out": o}


def store(records, today=None):
    """Atualiza videos.json e grava snapshot de views do dia em historico.json."""
    p = paths()
    today = (today or now()).date().isoformat()
    videos = read_json(p["videos"], {}) or {}
    hist = read_json(p["historico"], {}) or {}
    for r in records:
        prev = videos.get(r["video_id"], {})
        videos[r["video_id"]] = {**prev, **{k: v for k, v in r.items() if v is not None}, "atualizado_em": today}
        if r.get("views") is not None:
            snaps = [s for s in hist.get(r["video_id"], []) if s["data"] != today]
            snaps.append({"data": today, "views": r["views"]})
            hist[r["video_id"]] = sorted(snaps, key=lambda s: s["data"])
    write_json(p["videos"], videos)
    write_json(p["historico"], hist)
    return videos, hist


def collect(chans, cfg, token):
    """Coleta canal por canal, salvando a cada canal. Reaproveita runs das últimas horas (sem custo novo)."""
    p = paths()
    reuse = recent_runs(token, cfg["apify_actor"], cfg["reaproveitar_horas"])
    all_records, total_usd, falhas = [], 0.0, []
    for c in chans:
        log(f"Apify: {c['handle']} (até {cfg['max_longos']} longos"
            f"{' + %d Shorts' % cfg['max_shorts'] if cfg['max_shorts'] else ', sem Shorts'})...")
        prev = reuse.get(_norm_url(channel_input(c, cfg)["startUrls"][0]["url"]))
        try:
            if prev:
                log(f"  reaproveitando coleta de {prev['startedAt'][:16].replace('T', ' ')} (run {prev['id']}, sem custo novo)")
                run, items = apify_wait(token, prev)
                usd_new = 0.0
            else:
                run, items = apify_wait(token, apify_start(token, cfg["apify_actor"], channel_input(c, cfg)))
                usd_new = run.get("usageTotalUsd") or 0.0
        except (HttpError, RuntimeError, OSError) as e:
            log(f"  FALHOU {c['handle']}: {e}. Os outros canais continuam; rode 'coletar' de novo depois "
                "(o que já foi coletado é reaproveitado sem custo).")
            falhas.append(c["handle"])
            continue
        recs = [r for r in (normalize(i, c, cfg) for i in items) if r]
        if not cfg["max_shorts"]:  # Shorts desligados: descarta se o actor devolver mesmo assim
            recs = [r for r in recs if r["formato"] != "short"]
        total_usd += usd_new
        custos = read_json(p["custos"], []) or []
        custos.append({"data": now().isoformat(), "canal": c["handle"], "itens": len(recs), "usd": usd_new,
                       "run": run["id"], "reaproveitado": bool(prev)})
        write_json(p["custos"], custos)
        store(recs)  # salva já: uma falha adiante não perde o que foi pago
        log(f"  {len(recs)} vídeos — custo: US$ {usd_new:.4f}" + (" (reaproveitado)" if prev else ""))
        if items and not recs:
            log(f"  ATENÇÃO: o actor devolveu {len(items)} itens mas nenhum foi reconhecido. Campos: {sorted(items[0])}")
        all_records += recs
    if falhas:
        log(f"Canais com falha nesta rodada: {', '.join(falhas)}")
    return all_records, total_usd


# ---------------------------------------------------------------- métricas

def compute(videos, hist, cfg, today=None):
    """Outlier por vizinhança, engajamento, velocidade/em alta. Função pura (testável)."""
    today = today or now()
    by_group, by_chan = {}, {}
    for v in videos.values():
        if v.get("views") is None or not v.get("data"):
            continue
        v["_dt"] = parse_date(v["data"])
        by_group.setdefault((v["canal_handle"], v["formato"]), []).append(v)
        by_chan.setdefault(v["canal_handle"], []).append(v)

    # engajamento médio por canal
    eng_avg = {}
    for ch, vs in by_chan.items():
        ratios = [v["comentarios"] / v["views"] for v in vs if v.get("comentarios") is not None and v["views"]]
        eng_avg[ch] = sum(ratios) / len(ratios) if ratios else None

    # velocidade (views/dia entre os 2 últimos snapshots de datas diferentes)
    for v in videos.values():
        snaps = hist.get(v["video_id"], [])
        v["velocidade"] = None
        if len(snaps) >= 2:
            a, b = snaps[-2], snaps[-1]
            days = (parse_date(b["data"]) - parse_date(a["data"])).days
            if days > 0:
                v["velocidade"] = round((b["views"] - a["views"]) / days, 1)
    for vs in by_chan.values():
        for v in vs:
            v["idade_dias"] = (today - v["_dt"]).days

    def vel_baseline(v, ch):
        """Mediana da velocidade de vídeos do mesmo canal com idade parecida (vídeo novo sempre cresce mais
        rápido que vídeo antigo; comparar com qualquer vídeo marcaria todo lançamento como 'em alta')."""
        age = v["idade_dias"]
        lo, hi = age / 2 - 1, age * 2 + 1
        peers = [o["velocidade"] for o in by_chan[ch] if o is not v and o.get("velocidade") is not None
                 and o["formato"] == v["formato"] and lo <= o["idade_dias"] <= hi]
        return median(peers) if len(peers) >= cfg["em_alta_min_pares"] else None

    k = cfg["vizinhos"]
    for (ch, fmt), vs in by_group.items():
        corte = cfg["corte_short"] if fmt == "short" else cfg["corte_longo"]
        for v in vs:
            peers = sorted((o for o in vs if o is not v), key=lambda o: abs((o["_dt"] - v["_dt"]).total_seconds()))[:k]
            med = median([o["views"] for o in peers])
            v["mediana_vizinhos"] = med
            v["outlier_score"] = round(v["views"] / med, 2) if med else None
            v["jovem"] = v["idade_dias"] < cfg["idade_minima_dias"]
            v["outlier"] = bool(v["outlier_score"] and not v["jovem"] and v["outlier_score"] >= corte)
            # engajamento soma no score
            ea = eng_avg.get(ch)
            if ea and v.get("comentarios") is not None and v["views"]:
                v["engajamento_x"] = round((v["comentarios"] / v["views"]) / ea, 2)
            else:
                v["engajamento_x"] = None
            bonus = 0.0
            if v["engajamento_x"] and v["engajamento_x"] > 1:
                bonus = min((v["engajamento_x"] - 1) * cfg["engajamento_peso"], cfg["engajamento_bonus_max"])
            v["score"] = round((v["outlier_score"] or 0) * (1 + bonus), 2)
            # em alta
            va = vel_baseline(v, ch) if v.get("velocidade") is not None else None
            v["velocidade_x"] = round(v["velocidade"] / va, 2) if va else None
            v["em_alta"] = bool(v["velocidade_x"] and v["idade_dias"] <= cfg["em_alta_max_dias"]
                                and v["velocidade_x"] >= cfg["em_alta_fator"])
    for v in videos.values():
        v.pop("_dt", None)
    return videos


def load_analyses():
    d = paths()["analises"]
    out = {}
    if d.exists():
        for f in d.glob("*.json"):
            a = read_json(f, None)
            if isinstance(a, dict):
                out[f.stem] = a
    return out


def load_weights():
    """Pesos da Fase 4 (aprendizado.py): {"canal": {nome: {"peso": x}}, "tema": {...}}."""
    return read_json(paths()["out"] / "pesos.json", {}) or {}


def weight(v, analysis, pesos):
    pc = ((pesos.get("canal") or {}).get(v.get("canal")) or {}).get("peso", 1.0)
    pt = ((pesos.get("tema") or {}).get(analysis.get("tema")) or {}).get("peso", 1.0)
    return round(pc * pt, 3)


def candidates(videos, analyses, cfg):
    cands = [v for v in videos.values() if v.get("manual") or ((v.get("outlier") or v.get("em_alta"))
             and (cfg["max_shorts"] or v.get("formato") != "short"))]
    pesos = load_weights()
    for v in cands:
        v["peso"] = weight(v, analyses.get(v["video_id"], {}), pesos)
    cands.sort(key=lambda v: -((v.get("score") or 0) * v["peso"]))
    pend_rel, pend_anal = [], []
    for v in cands:
        a = analyses.get(v["video_id"], {})
        v["relevancia"] = a.get("relevancia")
        v["analisado"] = bool(a.get("ideias"))
        if v["relevancia"] is None:
            pend_rel.append(v["video_id"])
        elif v["relevancia"] >= cfg["relevancia_minima"] and not v["analisado"]:
            pend_anal.append(v["video_id"])
    return cands, pend_rel, pend_anal


def validated_themes(videos, analyses, cfg, today=None):
    """Mesmo tema outlier em 2+ canais nos últimos N dias."""
    today = today or now()
    lim = today - timedelta(days=cfg["tema_validado_dias"])
    temas = {}
    for vid, a in analyses.items():
        v = videos.get(vid)
        tema = a.get("tema")
        if not v or not tema or not (v.get("outlier") or v.get("em_alta")):
            continue
        if not v.get("data") or parse_date(v["data"]) < lim:
            continue
        t = temas.setdefault(tema, {"tema": tema, "canais": set(), "videos": []})
        t["canais"].add(v["canal_handle"])
        t["videos"].append(vid)
    out = [{"tema": t["tema"], "canais": sorted(t["canais"]), "videos": t["videos"]}
           for t in temas.values() if len(t["canais"]) >= cfg["tema_validado_canais"]]
    return sorted(out, key=lambda t: -len(t["canais"]))


def recalc(cfg, quiet=False):
    p = paths()
    videos = read_json(p["videos"], {}) or {}
    hist = read_json(p["historico"], {}) or {}
    compute(videos, hist, cfg)
    write_json(p["videos"], videos)
    analyses = load_analyses()
    cands, pend_rel, pend_anal = candidates(videos, analyses, cfg)
    temas = validated_themes(videos, analyses, cfg)
    write_json(p["temas"], temas)
    write_json(p["candidatos"], {
        "gerado_em": now().isoformat(),
        "cortes": {"longo": cfg["corte_longo"], "short": cfg["corte_short"]},
        "pendentes_relevancia": pend_rel,
        "pendentes_analise": pend_anal,
        "temas_validados": temas,
        "candidatos": [{k: v.get(k) for k in (
            "video_id", "url", "titulo", "canal", "canal_handle", "idioma", "formato", "data", "idade_dias", "views",
            "comentarios", "duracao_s", "thumbnail", "mediana_vizinhos", "outlier_score", "engajamento_x", "score",
            "outlier", "velocidade", "velocidade_x", "em_alta", "relevancia", "analisado", "peso", "manual")} for v in cands],
    })
    if not quiet:
        n_out = sum(1 for v in cands if v.get("outlier"))
        n_alta = sum(1 for v in cands if v.get("em_alta"))
        log(f"{len(videos)} vídeos na base · {n_out} outliers · {n_alta} em alta · "
            f"{len(pend_rel)} aguardando nota de relevância · {len(pend_anal)} aguardando análise · "
            f"{len(temas)} temas validados")
        for v in cands[:10]:
            tag = "EM ALTA " if v.get("em_alta") else ""
            log(f"  {tag}{v['score']:>5}x  [{v['formato']}] {v['canal']}: {(v.get('titulo') or '')[:70]}")
    return cands


def transcribe(cfg):
    """Baixa a legenda (pt/en) de quem tem relevância >= mínima e grava o gancho literal (0–15 s) na análise."""
    import transcricao
    videos = read_json(paths()["videos"], {}) or {}
    adir = paths()["analises"]
    for vid, a in load_analyses().items():
        if (a.get("relevancia") or 0) < cfg["relevancia_minima"] or vid not in videos:
            continue
        try:
            p = transcricao.fetch(vid, videos[vid].get("titulo"))
        except Exception as e:  # transcrição nunca derruba a rodada
            log(f"  {vid}: falhou ({e})")
            continue
        if not p:
            log(f"  {vid}: sem legenda pt/en")
            continue
        gancho = transcricao.first_seconds(transcricao.read_cues(p), 15)
        if gancho and a.get("gancho_literal") != gancho:
            a["gancho_literal"] = gancho
            write_json(adir / f"{vid}.json", a)
        log(f"  {vid}: ok")


# ---------------------------------------------------------------- main

def append_channel(chan, lang="pt", path=None):
    """Acrescenta o canal ao canais.txt se ainda não estiver lá. Devolve True se acrescentou."""
    path = path or SKILL_DIR / "canais.txt"
    atuais = read_channels(path) if path.exists() else []
    key = chan["handle"].lower().lstrip("@")
    if any(c["handle"].lower().lstrip("@") == key or c["url"].lower() == chan["url"].lower() for c in atuais):
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write(("" if not path.exists() or path.read_text(encoding="utf-8").endswith("\n") else "\n")
                + chan["url"] + (" | en" if lang == "en" else "") + "\n")
    return True


def add_reference(entrada, cfg, lang="pt", g=None, is_short_fn=None):
    """Adiciona na mão: @canal / link de canal (entra no canais.txt e é coletado agora) ou link de vídeo
    (entra como referência manual). Devolve um resumo para mostrar ao usuário."""
    import youtube_publico as Y
    tipo, val = Y.parse_entrada(entrada)
    if g is None:
        from yt_common import Google
        g = Google()
    if tipo == "video":
        rec = Y.fetch_manual_video(g, val, cfg, is_short_fn)
        store([rec])
        recalc(cfg, quiet=True)
        return {"tipo": "video", "id": rec["video_id"], "titulo": rec["titulo"], "canal": rec["canal"],
                "msg": f"Vídeo adicionado: “{rec['titulo']}” ({rec['canal']}). Ele aparece em “Adicionadas por você”."}
    chan = {**val, "lang": lang}
    novo = append_channel(chan, lang)
    recs, _ = Y.collect([chan], cfg, g=g, is_short_fn=is_short_fn)
    if not recs:
        raise ValueError(f"Não achei vídeos longos públicos em {chan['handle']}. Confira o @ ou o link.")
    cands = recalc(cfg, quiet=True)
    outs = [c for c in cands if c.get("canal_handle") == chan["handle"] and c.get("outlier")]
    return {"tipo": "canal", "handle": chan["handle"], "canal": recs[0]["canal"], "videos": len(recs),
            "outliers": len(outs), "novo": novo,
            "msg": f"{recs[0]['canal']}: {len(recs)} vídeos coletados, {len(outs)} outliers"
                   + ("" if novo else " (já estava na lista; dados atualizados)") + "."}


def process_queue(cfg, queue, g=None):
    """Pedidos de 'adicionar' feitos no painel aberto como arquivo (vêm no avaliacoes.json)."""
    feitos = []
    for item in queue or []:
        try:
            feitos.append(add_reference(item.get("entrada", ""), cfg, item.get("lang", "pt"), g=g)["msg"])
        except (ValueError, HttpError, OSError, RuntimeError) as e:
            feitos.append(f"Falhou '{item.get('entrada')}': {e}")
    for f in feitos:
        log("  " + f)
    return feitos


def run_collect(chans, cfg):
    """Coleta pela fonte configurada. Devolve (records, texto de custo)."""
    import os
    if cfg["fonte"] == "apify":
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            raise SystemExit("Falta APIFY_TOKEN no .env da skill (~/.claude/skills/referencias-youtube/.env).")
        recs, usd = collect(chans, cfg, token)
        return recs, f"custo Apify US$ {usd:.4f}", usd
    import youtube_publico
    recs, units = youtube_publico.collect(chans, cfg)
    return recs, f"grátis (YouTube API: {units} de 10.000 unidades diárias)", 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["teste", "coletar", "calcular", "transcrever", "temas", "descobrir", "adicionar"])
    ap.add_argument("--en", action="store_true", help="canal em inglês (com 'adicionar')")
    ap.add_argument("canal", nargs="?")
    args = ap.parse_args()
    cfg = config()
    load_env()
    chans = read_channels()

    if args.acao in ("teste", "coletar"):
        if args.acao == "teste":
            c = match_channel(args.canal or "@primorico", chans)
            recs, custo, usd = run_collect([c], cfg)
            log("")
            log(f"Teste com {c['handle']}: {len(recs)} vídeos coletados — {custo}.")
            if recs and cfg["fonte"] == "apify":
                n = len(chans)
                log(f"Estimativa para os {n} canais de canais.txt: ~US$ {usd * n:.2f} por rodada "
                    f"(~US$ {usd * n * 4:.2f}/mês rodando 1x por semana).")
            for r in recs[:3]:
                log(f"  [{r['formato']}] {r['views']} views · {r['comentarios']} coment. · {r['data']} · {r['titulo']}")
        else:
            recs, custo, _ = run_collect(chans, cfg)
            log(f"Rodada: {len(recs)} vídeos de {len(chans)} canais — {custo}")
        recalc(cfg)
    elif args.acao in ("calcular", "temas"):
        recalc(cfg)
    elif args.acao == "transcrever":
        transcribe(cfg)
    elif args.acao == "adicionar":
        if not args.canal:
            raise SystemExit("Uso: referencias.py adicionar @canal | link do canal | link do vídeo [--en]")
        try:
            log(add_reference(args.canal, cfg, "en" if args.en else "pt")["msg"])
        except ValueError as e:
            raise SystemExit(str(e))
    elif args.acao == "descobrir":
        import youtube_publico
        own = (read_json(paths()["out"] / "meu-canal-dados.json", {}) or {}).get("channel", {}).get("id")
        sugs, units = youtube_publico.discover(cfg, [c["handle"] for c in chans], own_channel_id=own)
        log(f"{len(sugs)} canais sugeridos ({units} unidades de cota) — veja ./referencias/canais-sugeridos.md")
        for s_ in sugs[:10]:
            top = s_["videos"][0]
            log(f"  {s_['canal']} ({s_['inscritos']:,} inscritos) — {top['views_por_inscrito']}x: {top['titulo'][:60]}")


if __name__ == "__main__":
    main()
