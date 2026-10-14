"""Gera UMA ideia nova na hora (quando o Léo dá 👎 no painel), a partir de uma referência já analisada.

Usa a API da Anthropic (SDK oficial `anthropic`) com saída JSON garantida. Opcional: sem ANTHROPIC_API_KEY
(ou sem o pacote instalado) o painel só troca pela próxima ideia da fila.

O pedido leva: briefing (história, tom, regras e o que o Léo aprovou/reprovou), o diagnóstico do canal e os
aprendizados (parte fixa, em cache), mais a referência escolhida, as ideias que ela já tem, exemplos de 👍 e 👎
e a ideia que acabou de ser rejeitada — para a nova ideia evitar o mesmo erro.
"""
import json
import os
import re

from yt_common import SKILL_DIR, load_env, log, now, out_dir, read_json, write_json

import referencias as R

MODELO_PADRAO = "claude-opus-5-5"
SCHEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "gancho": {"type": "string"},
        "topicos": {"type": "array", "items": {"type": "string"}},
        "a_conferir": {"type": "string"},
        "por_que_diferente": {"type": "string"},
    },
    "required": ["titulo", "gancho", "topicos", "a_conferir", "por_que_diferente"],
    "additionalProperties": False,
}


def disponivel():
    """(True, "") se dá para gerar; senão (False, motivo para mostrar no painel)."""
    load_env()
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "Falta o pacote da Anthropic: rode  python3 -m pip install --user anthropic"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, "Falta a chave ANTHROPIC_API_KEY no .env da skill (console.anthropic.com → API Keys)."
    return True, ""


def _bloco(md_text, chave):
    m = re.search(rf"<!-- CLAUDE:{chave} .*?-->\n(.*?)<!-- /CLAUDE:{chave} -->", md_text or "", re.S)
    return m.group(1).strip() if m else ""


def contexto_fixo():
    """Parte estável do pedido (fica em cache entre ideias)."""
    o = out_dir()
    briefing = (SKILL_DIR / "briefing.md").read_text(encoding="utf-8") if (SKILL_DIR / "briefing.md").exists() else ""
    briefing = briefing.split("## Saída")[0].strip()
    mc = (o / "meu-canal.md").read_text(encoding="utf-8") if (o / "meu-canal.md").exists() else ""
    ap = (o / "aprendizados.md").read_text(encoding="utf-8") if (o / "aprendizados.md").exists() else ""
    partes = [
        "Você cria ideias de vídeo para o canal do YouTube Grana Própria (Léo Freitas). Responda só com o JSON pedido, "
        "em português do Brasil.",
        briefing,
        "## Diagnóstico do canal do Léo\n" + "\n\n".join(x for x in (
            _bloco(mc, "tom"), _bloco(mc, "retencao"), _bloco(mc, "cobertos")) if x),
        "## Aprendizados com as avaliações e os vídeos postados\n" + _bloco(ap, "analise"),
    ]
    return "\n\n".join(p for p in partes if p.strip())


def escolher_referencia(analyses, ratings, rejeitada_ref=None):
    """Referência com melhor prioridade (score × relevância × peso) que ainda não tem muitas ideias pendentes."""
    videos = read_json(R.paths()["videos"], {}) or {}
    pesos = R.load_weights()
    melhor, melhor_p = None, -1
    for vid, a in analyses.items():
        if not a.get("ideias") or (a.get("relevancia") or 0) < 4:
            continue
        pend = sum(1 for i in a["ideias"] if not (ratings.get(i["id"]) or {}).get("voto"))
        if pend >= 2:
            continue
        v = videos.get(vid, {})
        p = (v.get("score") or 1) * (a.get("relevancia") or 0) * R.weight(v, a, pesos)
        if vid == rejeitada_ref:
            p *= 0.6  # tenta variar a referência, sem proibir
        if p > melhor_p:
            melhor, melhor_p = vid, p
    return melhor


def montar_pedido(ref_id, a, v, ratings, analyses, rejeitada=None):
    titulos = {i["id"]: i["titulo"] for an in analyses.values() for i in an.get("ideias") or []}
    aprovadas = [titulos[i] for i, r in ratings.items() if r.get("voto") == "up" and i in titulos][-15:]
    rejeitadas = [titulos[i] for i, r in ratings.items() if r.get("voto") == "down" and i in titulos][-15:]
    ref = {k: a.get(k) for k in ("tema", "gancho", "estrutura", "por_que_clicaram", "por_que_assistiram")}
    ref.update(titulo=v.get("titulo"), canal=v.get("canal"), idioma=v.get("idioma"), score=v.get("score"))
    linhas = [
        "## Referência (vídeo que foi outlier no canal dele)", json.dumps(ref, ensure_ascii=False, indent=1),
        "## Ideias que já existem para essa referência (NÃO repetir o ângulo)",
        *[f"- {i['titulo']}" for i in a.get("ideias") or []],
        "## Ideias que o Léo APROVOU (modelo do que funciona para ele)", *[f"- {t}" for t in aprovadas],
        "## Ideias que o Léo REJEITOU (descubra o padrão e evite)", *[f"- {t}" for t in rejeitadas],
    ]
    if rejeitada:
        linhas += ["## Acabou de rejeitar (sem dizer o motivo)", f"- {rejeitada}",
                   "Deduza o motivo provável pelo padrão das rejeições e faça algo claramente diferente."]
    linhas += ["## Tarefa",
               "Crie UMA ideia nova de vídeo para o Léo baseada nessa referência, contada a partir da história dele, "
               "no tom dele e seguindo as regras de roteiro. Título no estilo YouTube BR; gancho = fala dos primeiros "
               "15 s em 1ª pessoa com um número logo de cara; 4–7 tópicos com números concretos. Fatos da vida do "
               "Léo que não estão no briefing vão em a_conferir (nunca afirme). Em por_que_diferente, explique em "
               "1 frase por que ela evita o que foi rejeitado."]
    return "\n".join(linhas)


def gerar(ratings, rejeitada_id=None, ref_id=None, client=None, cfg=None):
    """Gera 1 ideia, grava em analises/{ref}.json e devolve (ref_id, ideia)."""
    cfg = cfg or R.config()
    analyses = R.load_analyses()
    titulos = {i["id"]: (vid, i["titulo"]) for vid, an in analyses.items() for i in an.get("ideias") or []}
    rej_ref, rej_titulo = titulos.get(rejeitada_id, (None, None))
    ref_id = ref_id or escolher_referencia(analyses, ratings, rej_ref)
    if not ref_id:
        raise ValueError("Nenhuma referência analisada disponível para gerar ideias novas.")
    a = analyses[ref_id]
    v = (read_json(R.paths()["videos"], {}) or {}).get(ref_id, {})
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    params = dict(
        model=cfg.get("modelo_ideias", MODELO_PADRAO),
        max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": contexto_fixo(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": montar_pedido(ref_id, a, v, ratings, analyses, rej_titulo)}],
    )
    try:  # com fallback automático se o modelo recusar (SDK recente)
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    except TypeError:  # SDK antigo sem esses parâmetros
        resp = client.messages.create(**params)
    if resp.stop_reason == "refusal":
        raise ValueError("O modelo recusou gerar essa ideia. Tente de novo.")
    if resp.stop_reason == "max_tokens":
        raise ValueError("A resposta foi cortada. Tente de novo.")
    texto = next((b.text for b in resp.content if b.type == "text"), "")
    nova = json.loads(texto)
    n = 1 + sum(1 for i in a.get("ideias") or [] if i["id"].startswith(f"{ref_id}-g"))
    ideia = {"id": f"{ref_id}-g{n}", "titulo": nova["titulo"].strip(), "gancho": nova["gancho"].strip(),
             "topicos": [t.strip() for t in nova["topicos"] if t.strip()], "origem": "gerada",
             "gerada_em": now().isoformat(), "substitui": rejeitada_id}
    if nova.get("a_conferir", "").strip():
        ideia["a_conferir"] = nova["a_conferir"].strip()
    if nova.get("por_que_diferente", "").strip():
        ideia["por_que_diferente"] = nova["por_que_diferente"].strip()
    a.setdefault("ideias", []).append(ideia)
    write_json(R.paths()["analises"] / f"{ref_id}.json", a)
    u = resp.usage
    log(f"Ideia gerada ({ref_id}): {ideia['titulo']} — tokens: {u.input_tokens} entrada, "
        f"{getattr(u, 'cache_read_input_tokens', 0) or 0} do cache, {u.output_tokens} saída")
    return ref_id, ideia
