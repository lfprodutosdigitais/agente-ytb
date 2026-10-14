"""Alerta de vídeo explodindo: confere os canais de referência várias vezes por dia e avisa na hora.

A cada rodada (agendada a cada 3 h pelo agendar_mac.sh): últimos uploads de cada canal (1 unidade da API por canal +
1 a cada 50 vídeos). Vídeo longo de até `alerta_max_dias` dias é comparado com a mediana dos vídeos longos do mesmo
canal (7–120 dias), descontando a idade: um vídeo com 1 dia ainda não teve tempo de chegar à mediana.

    ritmo = views ÷ (mediana do canal × curva(idade)),   curva(idade) = √(horas ÷ 168), no máximo 1

Ritmo ≥ `alerta_fator` (3x) e views ≥ `alerta_min_views` → alerta (uma vez por vídeo). Com a chave da Anthropic o
Claude já dá a nota e escreve as ideias; vídeo com nota baixa para o público do Léo não gera aviso.

Avisos: notificação do Mac (sempre), e-mail (se ALERTA_EMAIL_PARA + SMTP_* no .env) e faixa "Explodindo agora" no
painel. Saída: ./referencias/alertas.json.
"""
import os
import smtplib
import subprocess
import sys
from datetime import timedelta
from email.message import EmailMessage

from yt_common import HttpError, log, median, now, out_dir, parse_date, read_json, write_json

import referencias as R

DATA = "https://www.googleapis.com/youtube/v3"
PADRAO = {"alerta_fator": 3, "alerta_min_views": 1000, "alerta_max_dias": 7, "alerta_uploads": 10}


def arquivo():
    return out_dir() / "alertas.json"


def curva(horas):
    """Fração das views 'normais' que um vídeo costuma ter com essa idade (aproximação; 1 a partir de 7 dias)."""
    return min(1.0, max(horas, 1) / 168) ** 0.5


def medianas_por_canal(videos, hoje=None):
    hoje = hoje or now()
    por = {}
    for v in videos.values():
        if v.get("formato") == "short" or not v.get("data") or v.get("views") is None:
            continue
        idade = (hoje - parse_date(v["data"])).days
        if 7 <= idade <= 120:
            por.setdefault(v.get("canal_handle"), []).append(v["views"])
    return {h: median(vs) for h, vs in por.items() if len(vs) >= 3}


def ultimos_uploads(g, chans, cfg, quota):
    """[(canal, item da API)] dos últimos uploads de cada canal."""
    import youtube_publico as Y
    cache_f = out_dir() / "cache" / "canais-ids.json"
    cache = read_json(cache_f, {}) or {}
    pares = []
    for c in chans:
        try:
            cid = cache.get(c["handle"].lower())
            if not cid:
                info = Y.resolve_channel(g, c, quota, cache)
                if not info:
                    continue
                cid = info["id"]
            r = g.get(f"{DATA}/playlistItems", {"part": "contentDetails", "playlistId": "UU" + cid[2:],
                                                "maxResults": cfg["alerta_uploads"]})
            quota.add(1)
            pares += [(c, it["contentDetails"]["videoId"]) for it in r.get("items") or []]
        except (HttpError, OSError) as e:
            log(f"  {c['handle']}: {e}")
    write_json(cache_f, cache)
    por_id = {vid: c for c, vid in pares}
    return [(por_id[v["id"]], v) for v in Y.video_details(g, list(por_id), quota)]


def avaliar(recs, medianas, cfg, hoje=None):
    """Registros que estão explodindo: [(registro, ritmo, horas)]."""
    hoje = hoje or now()
    out = []
    for r in recs:
        if r.get("formato") == "short" or not r.get("data") or r.get("views") is None:
            continue
        horas = (hoje - parse_date(r["data"])).total_seconds() / 3600
        med = medianas.get(r.get("canal_handle"))
        if not med or horas > cfg["alerta_max_dias"] * 24 or r["views"] < cfg["alerta_min_views"]:
            continue
        ritmo = r["views"] / (med * curva(horas))
        if ritmo >= cfg["alerta_fator"]:
            out.append((r, round(ritmo, 1), round(horas)))
    return sorted(out, key=lambda x: -x[1])


def notificar_mac(titulo, texto):
    if sys.platform != "darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{esc(texto)}" with title "{esc(titulo)}" sound name "Glass"'],
                   capture_output=True)


def enviar_email(assunto, corpo):
    """E-mail pelo SMTP do .env (ex.: Gmail com senha de app). Devolve True se enviou."""
    para, usuario, senha = (os.environ.get(k) for k in ("ALERTA_EMAIL_PARA", "SMTP_USUARIO", "SMTP_SENHA"))
    if not (para and usuario and senha):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, usuario, para
    msg.set_content(corpo)
    host = os.environ.get("SMTP_SERVIDOR", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORTA", "465")), timeout=30) as s:
        s.login(usuario, senha)
        s.send_message(msg)
    return True


def texto_alerta(a):
    views = f"{a['views']:,}".replace(",", ".")
    ritmo = str(a["ritmo"]).replace(".", ",")
    linhas = [f"🔥 {a['titulo']}", f"{a['canal']} · {views} views em {a['horas']} h · {ritmo}x o ritmo normal do canal",
              a["url"], ""]
    if a.get("relevancia"):
        linhas.append(f"Nota para o canal do Léo: {a['relevancia']}/5 — {a.get('motivo') or ''}")
    for i in a.get("ideias") or []:
        linhas += [f"💡 {i['titulo']}", f"   Gancho: {i.get('gancho', '')}"]
    linhas += ["", "As ideias já estão no painel (aba Ideias para gravar)."]
    return "\n".join(linhas)


def rodar(g=None, cfg=None, analisar=None, avisar=None):
    """Uma rodada de conferência. Devolve os alertas novos."""
    import gerador
    import youtube_publico as Y
    cfg = {**PADRAO, **(cfg or R.config())}
    if g is None:
        from yt_common import Google
        g = Google()
    quota = Y.Quota()
    chans = R.read_channels()
    hoje = now()
    medianas = medianas_por_canal(read_json(R.paths()["videos"], {}) or {}, hoje)
    pares = ultimos_uploads(g, [c for c in chans if c["handle"] in medianas], cfg, quota)
    recs = [Y.to_record(v, c, None, cfg, lambda vid, dur, mx=180: dur <= mx) for c, v in pares if not Y.is_live(v)]
    R.store(recs)  # atualiza views e o histórico (serve também para o "em alta")
    estado = read_json(arquivo(), {}) or {}
    enviados = estado.setdefault("alertas", {})
    ok_ia, _ = gerador.disponivel()
    novos = []
    for r, ritmo, horas in avaliar(recs, medianas, cfg, hoje):
        vid = r["video_id"]
        if vid in enviados:
            enviados[vid].update(views=r["views"], ritmo=ritmo, horas=horas)
            continue
        a = {"video_id": vid, "titulo": r["titulo"], "canal": r["canal"], "url": r["url"], "thumb": r.get("thumbnail"),
             "views": r["views"], "ritmo": ritmo, "horas": horas, "em": hoje.isoformat()}
        videos = read_json(R.paths()["videos"], {}) or {}
        videos[vid] = {**videos.get(vid, {}), "alerta": True}
        write_json(R.paths()["videos"], videos)
        if ok_ia or analisar:
            try:
                an = (analisar or gerador.analisar)(vid, R.ratings_atuais(), None, cfg)
                a.update(relevancia=an.get("relevancia"), motivo=an.get("motivo_relevancia"), ideias=an.get("ideias") or [])
            except Exception as e:  # sem análise, avisa mesmo assim
                log(f"  análise de {vid}: {e}")
        enviados[vid] = a
        if a.get("relevancia") is not None and a["relevancia"] < cfg["relevancia_minima"]:
            a["silencioso"] = True  # explodiu, mas não serve para o público do Léo
            continue
        novos.append(a)
    estado.update(ultima_conferencia=hoje.isoformat(), cota=quota.units)
    write_json(arquivo(), estado)
    R.recalc(cfg, quiet=True)
    for a in novos:
        (avisar or avisar_padrao)(a)
    log(f"Alertas: {len(pares)} uploads conferidos em {len(medianas)} canais ({quota.units} unidades) · "
        f"{len(novos)} explodindo agora")
    return novos


def avisar_padrao(a):
    notificar_mac(f"🔥 {a['canal']} está explodindo", f"{a['titulo']} — {str(a['ritmo']).replace('.', ',')}x o normal em {a['horas']} h")
    try:
        if enviar_email(f"🔥 Explodindo agora: {a['titulo']}", texto_alerta(a)):
            log(f"  e-mail enviado: {a['titulo']}")
    except (OSError, smtplib.SMTPException) as e:
        log(f"  e-mail falhou ({e}); confira SMTP_USUARIO/SMTP_SENHA no .env")


def ativos(dias=3):
    """Alertas dos últimos `dias` dias que valem mostrar no painel."""
    lim = now() - timedelta(days=dias)
    est = read_json(arquivo(), {}) or {}
    return sorted((a for a in (est.get("alertas") or {}).values() if not a.get("silencioso") and parse_date(a["em"]) >= lim),
                  key=lambda a: -a["ritmo"])


def main():
    from yt_common import load_env
    load_env()
    if "--teste-email" in sys.argv:
        ok = enviar_email("Teste do alerta do Grana Própria", "Se chegou, o alerta por e-mail está funcionando.")
        log("E-mail de teste enviado." if ok else "Faltam ALERTA_EMAIL_PARA, SMTP_USUARIO e SMTP_SENHA no .env.")
        notificar_mac("Teste do alerta", "Se apareceu, a notificação do Mac está funcionando.")
        return
    rodar()


if __name__ == "__main__":
    main()
