"""Supervisor IA de predicciones (beta).

Cada `CADA_HORAS` la IA revisa cómo le fue a sus órdenes de predicción y puede
ajustar parámetros de Config → 🎲 Predicciones. Solo los de `AJUSTABLES`, dentro
de sus rangos, y el dinero solo puede BAJARLO: el monto por compra y el tope total
nunca pasan de lo que el usuario puso («techo»). Nunca cambia el modo (apagada /
propone / ejecuta sola) ni el modo real. La nube vuelve a validar todo.
"""
import statistics
import time

CADA_HORAS = 6

AJUSTABLES = {
    "pred_ventaja": (3, 40), "pred_conf_min": (50, 100), "pred_prob_min": (1, 50), "pred_prob_max": (50, 99),
    "pred_horas_min": (0, 48), "pred_max_ciclo": (1, 5), "pred_monto": (1, 500), "pred_max_total": (1, 5000),
}
CATEGORIAS = ("cripto_hoy", "cripto_mediano", "bolsa")
TECHOS = {"pred_monto": "pred_monto_techo", "pred_max_total": "pred_max_total_techo"}


def toca(cfg, ultimo_ts, ahora=None):
    if cfg.get("pred_supervisor") is False or cfg.get("pred_ia", "proponer") == "off" or cfg.get("ia") == "off":
        return False
    ahora = ahora or time.time() * 1000
    return not ultimo_ts or ahora - ultimo_ts >= CADA_HORAS * 3_600_000


def limpiar(cfg, cambios):
    """Solo cambios permitidos, en rango y sin subir el dinero por encima del techo."""
    out = {}
    for k, v in (cambios or {}).items():
        if k == "pred_categorias":
            if isinstance(v, list):
                l = sorted({str(x) for x in v if str(x) in CATEGORIAS})
                if l and l != sorted(cfg.get("pred_categorias") or []):
                    out[k] = l
            continue
        if k not in AJUSTABLES:
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = AJUSTABLES[k]
        if k in TECHOS:
            hi = min(hi, float(cfg.get(TECHOS[k]) or cfg.get(k) or hi))
        n = max(lo, min(hi, n))
        n = int(n) if k in ("pred_max_ciclo", "pred_conf_min", "pred_prob_min", "pred_prob_max") else round(n, 1)
        if cfg.get(k) is None or abs(float(cfg[k]) - n) > 1e-9:
            out[k] = n
    if out.get("pred_monto", cfg.get("pred_monto") or 11) > out.get("pred_max_total", cfg.get("pred_max_total") or 30):
        out.pop("pred_monto", None)
    if out.get("pred_prob_min", cfg.get("pred_prob_min") or 5) >= out.get("pred_prob_max", cfg.get("pred_prob_max") or 95):
        out.pop("pred_prob_min", None)
        out.pop("pred_prob_max", None)
    return out


def unir(cfg, respuestas):
    """Varias IAs revisan: un parámetro cambia solo si más de la mitad lo quiere cambiar,
    y al valor mediano de lo que propusieron (las categorías: solo las que todas ellas
    dejan). Todo vuelve a pasar por `limpiar`. Devuelve (cambios, a_favor por clave)."""
    propuestas = [limpiar(cfg, d.get("cambios")) for d in respuestas]
    out, apoyo = {}, {}
    for k in {k for p in propuestas for k in p}:
        vals = [p[k] for p in propuestas if k in p]
        if len(vals) * 2 <= len(respuestas):
            continue
        apoyo[k] = len(vals)
        if k == "pred_categorias":
            out[k] = sorted(set.intersection(*(set(v) for v in vals)))
        else:
            out[k] = statistics.median(vals)
    return limpiar(cfg, out), apoyo


def revisar(cfg, ordenes, posiciones, resumen, varias_json, feedback=None):
    """Devuelve {"ts", "cambios", "razon", "modelo"} (cambios puede venir vacío) o None si la IA falla.
    `varias_json(mensajes, valido)` → ([(modelo, datos)], error): con consenso, varias IAs."""
    actual = {k: cfg.get(k) for k in list(AJUSTABLES) + ["pred_categorias"]}
    techos = {k: cfg.get(t) or cfg.get(k) for k, t in TECHOS.items()}
    hist = [{k: o.get(k) for k in ("ts", "accion", "etiqueta", "unidades", "limite", "estado", "origen", "error", "resultado")} for o in (ordenes or [])][:20]
    pos = [{k: p.get(k) for k in ("pregunta", "lado_nombre", "cantidad", "precio", "valor", "vence")} for p in posiciones or []]
    mensajes = [
        {"role": "system", "content": "Eres el supervisor de riesgo de un bot de mercados de predicción. Prudente: prefieres no operar a perder. Respondes SOLO JSON válido en español."},
        {"role": "user", "content": (
            "Revisa cómo van las órdenes de predicción del bot y decide si conviene ajustar sus parámetros. "
            "Si todo está razonable, no cambies nada (cambios vacío). Cambia poco cada vez.\n"
            f"Parámetros actuales: {actual}\n"
            f"Techos del usuario (no puedes superarlos): {techos}\n"
            "Solo puedes hacer el bot MÁS prudente que lo que puso el usuario: más ventaja, más confianza, menos compras por ciclo, "
            "menos categorías, rango de probabilidad más estrecho. Lo más arriesgado lo rechaza la nube.\n"
            f"Rangos permitidos: {AJUSTABLES}; categorías posibles: {list(CATEGORIAS)}\n"
            "Significado: pred_ventaja = puntos mínimos entre modelo y mercado; pred_conf_min = confianza mínima de la IA; "
            "pred_prob_min/max = % del lado permitido; pred_horas_min = no entrar si falta menos; pred_max_ciclo = compras por ciclo; "
            "pred_monto = USDC por compra; pred_max_total = tope total.\n"
            f"Últimas órdenes de predicción: {hist}\n"
            f"Posiciones abiertas: {pos}\n"
            f"Revisión del último ciclo: {resumen}\n"
            + (f"{feedback}\n" if feedback else "") +
            'Formato: {"cambios":{"pred_ventaja":10},"razon":"máx. 40 palabras"}'
        )},
    ]
    ok, error = varias_json(mensajes, lambda d: isinstance(d, dict) and isinstance(d.get("cambios", {}), dict))
    if not ok:
        return None
    if len(ok) == 1:
        modelo, datos = ok[0]
        return {"ts": int(time.time() * 1000), "cambios": limpiar(cfg, datos.get("cambios")), "razon": str(datos.get("razon") or "")[:300], "modelo": modelo}
    cambios, apoyo = unir(cfg, [d for _, d in ok])
    corto = lambda m: str(m).split("/")[-1].replace(":free", "")
    detalle = ", ".join(f"{corto(m)}: {', '.join(sorted(limpiar(cfg, d.get('cambios')))) or 'sin cambios'}" for m, d in ok)
    razones = " | ".join(str(d.get("razon") or "")[:90] for _, d in ok)
    return {"ts": int(time.time() * 1000), "cambios": cambios, "razon": f"Consenso de {len(ok)} IAs ({detalle}). {razones}"[:300],
            "modelo": "consenso:" + "+".join(corto(m) for m, _ in ok)}
