"""Testa tudo de verdade (chaves, Google, Claude, legendas, Meus vídeos, comentários, alerta, roteiro) e imprime um
relatório para colar na conversa. Não mostra nenhuma chave.

Uso (dentro de ~/GranaPropria):
  python3 ~/.claude/skills/referencias-youtube/scripts/testar.py           # tudo (~US$ 0,30–0,60 de Claude)
  python3 ~/.claude/skills/referencias-youtube/scripts/testar.py --rapido  # sem gastar Claude além de 1 teste curto
"""
import os
import sys
import time
import traceback

from yt_common import load_env, out_dir, read_json

import referencias as R

RES = []


def passo(nome):
    def deco(fn):
        def run(*a):
            t = time.time()
            try:
                msg = fn(*a)
                RES.append(("✅", nome, msg or "ok", time.time() - t))
            except BaseException as e:  # inclui SystemExit dos scripts
                if isinstance(e, KeyboardInterrupt):
                    raise
                det = "".join(traceback.format_exception_only(type(e), e)).strip()
                RES.append(("❌", nome, det[:400], time.time() - t))
            print(f"{RES[-1][0]} {nome}: {RES[-1][2]}", flush=True)
            return RES[-1][0] == "✅"
        return run
    return deco


@passo("Pacotes")
def pacotes():
    import importlib.util
    falta = [p for p in ("anthropic", "yt_dlp") if not importlib.util.find_spec(p)]
    import shutil
    if "yt_dlp" in falta and shutil.which("yt-dlp"):
        falta.remove("yt_dlp")
    if falta:
        raise RuntimeError("faltam: " + ", ".join(falta))
    import anthropic
    return f"anthropic {anthropic.__version__}, yt-dlp ok, Python {sys.version.split()[0]}"


@passo("Chaves no .env")
def chaves():
    load_env()
    tem = lambda k: bool(os.environ.get(k))
    obrig = [k for k in ("GOOGLE_REFRESH_TOKEN", "ANTHROPIC_API_KEY") if not tem(k)]
    opc = {"Gemini (imagem da thumb)": tem("GEMINI_API_KEY"),
           "e-mail do alerta": all(tem(k) for k in ("ALERTA_EMAIL_PARA", "SMTP_USUARIO", "SMTP_SENHA"))}
    if obrig:
        raise RuntimeError("faltam: " + ", ".join(obrig))
    return "Google e Anthropic ok · " + " · ".join(f"{k}: {'sim' if v else 'não'}" for k, v in opc.items())


@passo("Google (canal do Léo)")
def google(ctx):
    import meu_canal
    from yt_common import Google
    g = Google()
    ch = meu_canal.my_channel(g)
    ctx["g"] = g
    return f"{ch['snippet']['title']} · {ch['statistics'].get('subscriberCount', '?')} inscritos"


@passo("Claude (chamada curta)")
def claude(ctx):
    import gerador
    ok, motivo = gerador.disponivel()
    if not ok:
        raise RuntimeError(motivo)
    schema = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"], "additionalProperties": False}
    r, resp = gerador._chamar(None, R.config(), schema, "Responda ok='funcionando'.", max_tokens=2000)
    u = resp.usage
    return f"{r['ok']} · modelo {resp.model} · {u.input_tokens} tokens de entrada ({getattr(u, 'cache_read_input_tokens', 0) or 0} do cache)"


@passo("Legenda de um vídeo do Léo (yt-dlp)")
def legenda(ctx):
    import transcricao
    vs = (read_json(out_dir() / "meu-canal-dados.json", {}) or {}).get("videos") or []
    if not vs:
        raise RuntimeError("meu-canal-dados.json vazio: rode meu_canal.py")
    v = vs[0]
    p = transcricao.fetch(v["video_id"], v.get("title"))
    if not p:
        raise RuntimeError(f"sem legenda para {v['video_id']}")
    return f"{len(p.read_text(encoding='utf-8').splitlines())} linhas ({v.get('title', '')[:50]})"


@passo("Meus vídeos (Analytics 2/7/30 dias)")
def meus_videos(ctx):
    import postados
    rel = postados.atualizar(g=ctx.get("g"))
    vs = rel["videos"]
    if not vs:
        return "nenhum vídeo longo nos últimos 120 dias"
    v = vs[0]
    j = v.get("d7") or v.get("d2") or {}
    return (f"{len(vs)} vídeo(s); mais novo: “{v['titulo'][:40]}” · views 7d {j.get('views')} · CTR "
            f"{'ok' if j.get('ctr') is not None else 'não veio (normal neste canal)'} · curva {len(v.get('curva') or [])} pontos · "
            f"{len(v['retencao'].get('quedas') or [])} quedas")


@passo("Comentários (coleta, sem Claude)")
def comentarios_coleta(ctx):
    import comentarios
    c = comentarios.coletar(ctx.get("g"), R.config())
    ex = (c["leo"] + c["ref"])[:1]
    d = comentarios.DIAG
    return (f"{d['recebidos']} comentários lidos em {d['videos']} vídeos → {len(c['leo'])} perguntas no canal do Léo, "
            f"{len(c['ref'])} nas referências"
            + (f" · erro: {d['erro']}" if d["erro"] else "") + (f" · ex.: “{ex[0]['texto'][:60]}”" if ex else ""))


@passo("Alerta (conferência sem avisar)")
def alerta_seco(ctx):
    import alerta
    import youtube_publico as Y
    cfg = {**alerta.PADRAO, **R.config()}
    q = Y.Quota()
    meds = alerta.medianas_por_canal(read_json(R.paths()["videos"], {}) or {})
    chans = [c for c in R.read_channels() if c["handle"] in meds]
    pares = alerta.ultimos_uploads(ctx.get("g"), chans, cfg, q)
    recs = [Y.to_record(v, c, None, cfg, lambda vid, dur, mx=180: dur <= mx) for c, v in pares if not Y.is_live(v)]
    tops = alerta.avaliar(recs, meds, {**cfg, "alerta_fator": 0, "alerta_min_views": 0})[:3]
    acima = sum(1 for _, rt, _ in tops if rt >= cfg["alerta_fator"])
    return (f"{len(pares)} uploads de {len(chans)} canais ({q.units} unidades) · maiores ritmos: "
            + ", ".join(f"{r['canal']} {rt}x" for r, rt, _ in tops) + f" · {acima} acima de {cfg['alerta_fator']}x")


@passo("Ideias dos comentários (Claude)")
def comentarios_ideias(ctx):
    import comentarios
    novos = comentarios.gerar(g=ctx.get("g"))
    return f"{len(novos)} ideia(s) nova(s): " + " | ".join(a["ideias"][0]["titulo"][:60] for a in novos[:3])


@passo("Roteiro de uma ideia aprovada (Claude)")
def roteiro_real(ctx):
    import roteiro
    av = R.ratings_atuais()
    ids = [i for i, a in av.items() if a.get("voto") == "up"]
    if not ids:
        raise RuntimeError("nenhuma ideia aprovada ainda")
    p = roteiro.gerar(ids[-1])
    ctx["roteiro"] = ids[-1]
    return (f"“{p['titulos'][p['titulo_recomendado']]['titulo'][:60]}” · {len(p['secoes'])} seções · "
            f"~{p['minutos_estimados']} min · {len(p['avisos'])} aviso(s) de retenção")


@passo("Painel (monta os dados)")
def painel_dados(ctx):
    import painel
    d = painel.build_data(R.config())
    ideias = sum(len(r["ideias"]) for r in d["refs"] + d["publico"])
    return (f"{len(d['refs'])} referências, {len(d['publico'])} do público, {ideias} ideias, "
            f"{len(d['alertas'])} alerta(s), meus vídeos: {'sim' if d['meus_videos'] else 'não'}")


def main():
    rapido = "--rapido" in sys.argv
    print(f"Teste do sistema — pasta {out_dir().resolve()}\n", flush=True)
    ctx = {}
    pacotes()
    chaves()
    if google(ctx):
        legenda(ctx)
        meus_videos(ctx)
        comentarios_coleta(ctx)
        alerta_seco(ctx)
    if claude(ctx) and not rapido:
        if ctx.get("g"):
            comentarios_ideias(ctx)
        roteiro_real(ctx)
    painel_dados(ctx)
    ok = sum(1 for r in RES if r[0] == "✅")
    print(f"\n===== {ok} de {len(RES)} ok =====")
    for s, n, m, t in RES:
        print(f"{s} {n} ({t:.0f}s): {m}")
    print("\nCole tudo a partir de '=====' na conversa. Nenhuma chave aparece aqui.")


if __name__ == "__main__":
    main()
