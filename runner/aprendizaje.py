"""Aprendizaje: cada decisión del agente queda anotada y, cuando el mercado vence, se
compara con el resultado OFICIAL de Hyperliquid (`settledOutcome`), no con una
estimación. Con esos resultados:

- mide qué tan bien acertaron el mercado, el modelo estadístico y la IA (Brier),
- calibra las probabilidades por tramos (si la IA dice 80 % y sale 55 %, lo corrige),
  encogiendo hacia lo que dijo mientras haya pocos casos,
- mide la ventaja REAL de lo que compró (resultado − precio pagado),
- arma lecciones cortas para la IA («en cripto de < 1 h, tus 70 % salen 50 %»),
- y endurece los requisitos donde viene perdiendo. Nunca los afloja: lo que pones tú
  es el mínimo.

No gasta IA: son cuentas. El registro viaja en el estado del agente que la nube ya
guarda en cada ciclo.
"""
import math
import time

HORA = 3_600_000
DIA = 24 * HORA
MAX_REGISTROS = 400   # cada ciclo la nube lee y guarda este registro: sin pasarse del CPU del plan gratis
DIAS_GUARDAR = 60
SIN_RESULTADO_TRAS = 3 * DIA   # si Hyperliquid no lo liquida en 3 días, se deja de preguntar
TRAMOS = (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001)
ENCOGER = 20            # casos «ficticios» que pesan lo que dijo el modelo: con pocos datos casi no corrige
MIN_TRAMO = 10          # casos para mostrar un tramo o sacar una lección
MIN_COMPRAS = 12        # compras resueltas para juzgar una categoría
MIN_COMPARAR = 30       # decisiones resueltas para comparar la IA con el mercado
EXTRA_PASO = 5          # puntos de ventaja extra por cada señal de que va mal…
EXTRA_MAX = 20          # …como mucho


def _clave(d):
    return f"{d['mercado']}:{d['lado']}"


def anotar(reg, decisiones, ahora=None):
    """Suma las decisiones de este ciclo. Un mercado+lado se anota una sola vez (la
    primera vez que el agente lo evaluó), para no contar diez veces el mismo resultado.
    Si después se compró, la decisión pasa a «comprar» con el precio que se pagó."""
    ahora = ahora or int(time.time() * 1000)
    reg = dict(reg) if isinstance(reg, dict) else {}
    lista = [dict(d) for d in reg.get("decisiones") or [] if ahora - (d.get("ts") or 0) < DIAS_GUARDAR * DIA]
    por_clave = {_clave(d): d for d in lista}
    for d in decisiones or []:
        if d.get("mercado") is None or d.get("lado") not in (0, 1):
            continue
        previa = por_clave.get(_clave(d))
        if previa is None:
            nueva = {"ts": ahora, "y": None, **d}
            lista.insert(0, nueva)
            por_clave[_clave(d)] = nueva
        elif d.get("accion") == "comprar" and previa.get("accion") != "comprar" and previa.get("y") is None:
            previa.update({k: d[k] for k in ("accion", "precio", "p_ia", "p_modelo", "conf", "senales", "modelos") if k in d})
            previa["ts_compra"] = ahora
    return {**reg, "decisiones": lista[:MAX_REGISTROS], "ts": ahora}


def resolver(reg, consultar, ahora=None, max_consultas=20, hasta=None):
    """Pregunta a Hyperliquid el resultado de los mercados ya vencidos.
    `consultar(mercado)` → settleFraction (0..1, paga al lado 0 «Sí») o None si aún
    no está liquidado. `hasta` (epoch s): no empieza consultas nuevas después de esa
    hora, para dejarle tiempo a la IA en el ciclo. Primero los que vencieron hace
    menos (los importados de Kumo, con vencimiento estimado, al final).
    Devuelve (registro, cuántos se resolvieron)."""
    ahora = ahora or int(time.time() * 1000)
    hechos, consultas = 0, 0
    cache = {}
    lista = [d for d in (reg or {}).get("decisiones") or []
             if d.get("y") is None and not d.get("sin_resultado") and d.get("vence") and d["vence"] <= ahora - 2 * 60_000]
    lista.sort(key=lambda d: (bool(d.get("vence_estimado")), -d["vence"]))
    for d in lista:
        if d["mercado"] not in cache and (consultas >= max_consultas or (hasta is not None and time.time() >= hasta)):
            continue
        if d["mercado"] not in cache:
            consultas += 1
            try:
                cache[d["mercado"]] = consultar(d["mercado"])
            except Exception:  # sin red: se intenta en el próximo ciclo
                cache[d["mercado"]] = None
        f = cache[d["mercado"]]
        if f is None:
            if ahora - d["vence"] > (30 * DIA if d.get("vence_estimado") else SIN_RESULTADO_TRAS):
                d["sin_resultado"] = True
            continue
        f = min(1.0, max(0.0, float(f)))
        d["y"] = f if d["lado"] == 0 else 1 - f
        d["resuelto"] = ahora
        hechos += 1
    return reg, hechos


def _resueltas(reg, desde=None):
    return [d for d in (reg or {}).get("decisiones") or [] if d.get("y") is not None and (desde is None or d.get("ts", 0) >= desde)]


def _prob(d):
    """La probabilidad con la que decidió el agente: la de la IA si opinó; si no, la del modelo."""
    p = d.get("p_ia") if d.get("p_ia") is not None else d.get("p_modelo")
    return None if p is None else min(0.999, max(0.001, float(p)))


def _brier(pares):
    return round(sum((p - y) ** 2 for p, y in pares) / len(pares), 4) if pares else None


def tabla_calibracion(reg, categoria=None, campo=None):
    """[(desde, hasta, n, dijo, salió)] por tramos de probabilidad. `campo` = "p_ia" o
    "p_modelo" para calibrar cada fuente por separado; sin campo, la que usó el agente."""
    filas = []
    prob = (lambda d: None if d.get(campo) is None else min(0.999, max(0.001, float(d[campo])))) if campo else _prob
    ds = [d for d in _resueltas(reg) if prob(d) is not None and (categoria is None or d.get("categoria") == categoria)]
    for a, b in zip(TRAMOS, TRAMOS[1:]):
        t = [d for d in ds if a <= prob(d) < b]
        if t:
            filas.append({"desde": a, "hasta": min(b, 1.0), "n": len(t), "dijo": round(sum(prob(d) for d in t) / len(t), 3),
                          "salio": round(sum(d["y"] for d in t) / len(t), 3)})
    return filas


def calibrar(p, reg, categoria=None, campo=None):
    """Corrige una probabilidad con lo que pasó de verdad en su tramo. Con n casos el
    ajuste pesa n / (n + ENCOGER): con 10 casos corrige un tercio, con 60 tres cuartos.
    Usa la tabla de la categoría si tiene casos en ese tramo; si no, la general."""
    if p is None:
        return None
    for tabla in ([tabla_calibracion(reg, categoria, campo)] if categoria else []) + [tabla_calibracion(reg, None, campo)]:
        for t in tabla:
            if t["desde"] <= p < t["hasta"] + (1e-9 if t["hasta"] >= 1 else 0) and t["n"] >= 3:
                peso = t["n"] / (t["n"] + ENCOGER)
                return round(min(0.99, max(0.01, p + peso * (t["salio"] - t["dijo"]))), 4)
    return p


def puntaje(reg, ahora=None, dias=30):
    """Cómo le va: Brier de mercado / modelo / IA y ventaja real de lo que compró."""
    ahora = ahora or int(time.time() * 1000)
    ds = _resueltas(reg, ahora - dias * DIA)
    def br(campo):
        return _brier([(min(0.999, max(0.001, float(d[campo]))), d["y"]) for d in ds if d.get(campo) is not None])
    compras = [d for d in ds if d.get("accion") == "comprar" and d.get("precio") is not None]
    por_cat = {}
    for d in compras:
        c = por_cat.setdefault(d.get("categoria") or "otro", {"n": 0, "ganadas": 0, "ventaja": 0.0})
        c["n"] += 1
        c["ganadas"] += 1 if d["y"] >= 0.5 else 0
        c["ventaja"] += d["y"] - float(d["precio"])
    for c in por_cat.values():
        c["ventaja"] = round(c["ventaja"] / c["n"] * 100, 1)  # puntos por compra (≈ % de lo pagado por unidad)
    return {"resueltas": len(ds), "brier_mercado": br("p_mercado"), "brier_modelo": br("p_modelo"), "brier_ia": br("p_ia"),
            "compras": len(compras), "ganadas": sum(1 for d in compras if d["y"] >= 0.5),
            "ventaja_real": round(sum(d["y"] - float(d["precio"]) for d in compras) / len(compras) * 100, 1) if compras else None,
            "por_categoria": por_cat,
            "pendientes": sum(1 for d in (reg or {}).get("decisiones") or [] if d.get("y") is None and not d.get("sin_resultado"))}


def ajustes(reg, ahora=None):
    """Solo hacia la prudencia: puntos de ventaja EXTRA que el agente exige.
    - Categoría con ≥ MIN_COMPRAS compras resueltas y ventaja real negativa: +5 (+10 si
      pierde más de 10 puntos por compra), hasta +20.
    - Si con ≥ MIN_COMPARAR decisiones la IA acierta peor que el propio precio del
      mercado (Brier mayor): +5 en todo, porque su «ventaja» no es real."""
    p = puntaje(reg, ahora)
    extra, razones = {"*": 0}, []
    for cat, c in p["por_categoria"].items():
        if c["n"] >= MIN_COMPRAS and c["ventaja"] < 0:
            pts = min(EXTRA_MAX, EXTRA_PASO * (2 if c["ventaja"] < -10 else 1))
            extra[cat] = pts
            razones.append(f"En {cat} las compras pierden {abs(c['ventaja']):g} puntos en promedio ({c['n']} resueltas): pido +{pts} de ventaja")
    if p["resueltas"] >= MIN_COMPARAR and p["brier_ia"] is not None and p["brier_mercado"] is not None and p["brier_ia"] > p["brier_mercado"]:
        extra["*"] = EXTRA_PASO
        razones.append(f"La IA acierta peor que el precio del mercado (Brier {p['brier_ia']} vs {p['brier_mercado']}): pido +{EXTRA_PASO} en todo")
    return {"extra": extra, "razones": razones}


def extra_para(aj, categoria):
    """Puntos extra de ventaja para una categoría (el mayor entre el general y el suyo)."""
    e = (aj or {}).get("extra") or {}
    return min(EXTRA_MAX, max(e.get("*", 0), e.get(categoria or "otro", 0)))


def lecciones(reg, maximo=5):
    """Frases cortas para la IA, solo con muestra suficiente y desvíos grandes."""
    out = []
    p = puntaje(reg)
    if p["resueltas"] >= MIN_COMPARAR and p["brier_ia"] is not None and p["brier_mercado"] is not None:
        mejor = "mejor" if p["brier_ia"] < p["brier_mercado"] else "peor"
        out.append(f"En {p['resueltas']} decisiones resueltas acertaste {mejor} que el precio del mercado (Brier {p['brier_ia']} vs {p['brier_mercado']}).")
    cats = sorted({d.get("categoria") or "otro" for d in _resueltas(reg)})
    for cat in [None] + cats:
        for t in tabla_calibracion(reg, cat):
            if t["n"] >= MIN_TRAMO and abs(t["salio"] - t["dijo"]) >= 0.12:
                donde = f"en {cat}" if cat else "en general"
                out.append(f"{donde.capitalize()}, cuando dijiste ~{round(t['dijo'] * 100)} % salió {round(t['salio'] * 100)} % ({t['n']} casos): "
                           + ("sé más escéptico." if t["salio"] < t["dijo"] else "fuiste demasiado prudente."))
    for cat, c in p["por_categoria"].items():
        if c["n"] >= MIN_COMPRAS:
            out.append(f"Compras en {cat}: {c['ganadas']}/{c['n']} ganadas, ventaja real {c['ventaja']:+g} puntos por compra.")
    return out[:maximo]


def desde_kumo(ordenes):
    """Convierte órdenes de predicción de Kumo (las que importas desde tu nube de Kumo)
    en decisiones para aprender: solo compras ejecutadas o propuestas con su precio."""
    out = []
    for o in ordenes or []:
        if o.get("tipo") != "prediccion" or o.get("accion") != "COMPRAR":
            continue
        try:
            cod = int(str(o.get("coin")).lstrip("#+"))
        except (TypeError, ValueError):
            continue
        ia = o.get("ia") or {}
        p_ia = ia.get("prob")  # Kumo la guarda en 0-100
        precio = (o.get("resultado") or {}).get("precio") or o.get("limite")
        ts = o.get("ts") or None
        horas = o.get("horas")
        out.append({"mercado": cod // 10, "lado": cod % 10, "coin": f"#{cod}", "etiqueta": str(o.get("etiqueta") or "")[:90],
                    "categoria": o.get("categoria") or "otro", "precio": float(precio) if precio else None,
                    "p_mercado": float(precio) if precio else None, "p_ia": float(p_ia) / 100 if p_ia is not None else None,
                    "conf": o.get("confianza"), "accion": "comprar" if o.get("estado") == "ejecutada" else "propuesta",
                    "origen": "kumo", "ts": ts,
                    # Kumo no guarda cuándo vence el mercado: se estima con las horas que faltaban al decidir.
                    "vence": int(ts + float(horas) * HORA) if ts and horas is not None else ts, "vence_estimado": True})
    return out


def importar(reg, decisiones, ahora=None):
    """Suma decisiones importadas conservando su fecha original."""
    ahora = ahora or int(time.time() * 1000)
    reg = dict(reg) if isinstance(reg, dict) else {}
    lista = list(reg.get("decisiones") or [])
    vistas = {_clave(d) for d in lista}
    n = 0
    for d in decisiones or []:
        if d.get("mercado") is None or d.get("lado") not in (0, 1) or _clave(d) in vistas:
            continue
        lista.append({"y": None, **d, "ts": d.get("ts") or ahora})
        vistas.add(_clave(d))
        n += 1
    lista.sort(key=lambda d: -(d.get("ts") or 0))
    return {**reg, "decisiones": lista[:MAX_REGISTROS], "ts": ahora}, n


def logit(p):
    p = min(0.999, max(0.001, p))
    return math.log(p / (1 - p))
