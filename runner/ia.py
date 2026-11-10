"""IA de Kumo: varios proveedores gratuitos, respaldo en cadena y decisión por consenso.

Todos hablan el formato de OpenAI (`/chat/completions`). La lista de proveedores, en
orden de prioridad y con sus claves, la manda la nube en cada ciclo (`configurar`):
primero las claves que pegaste, después la de la instalación y al final los gratuitos
sin clave (Kilo, LLM7, OVH) como respaldo, para que la IA no quede caída si uno falla.
Las nubes antiguas no mandan lista: entonces se usan los secretos IA_URL, IA_MODELOS e
IA_CLAVE (o GROQ_API_KEY) como antes.

- `chat_json(mensajes)`: cadena de respaldo. Prueba cada proveedor y modelo en orden;
  un 429, un timeout o un JSON inválido pasan al siguiente.
- `opinar` / `opinar_predicciones`: si el consenso está activo, preguntan a varias IAs
  en paralelo (un modelo de cada proveedor primero) y votan. Si no, usan la cadena.
"""
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_MODELOS = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"]

# Catálogo (igual que en la nube). `json`: qué formato de respuesta pedir.
CATALOGO = {
    "groq": {"url": GROQ_URL, "json": "schema"},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/openai", "max_tokens": 2048},
    "openrouter": {"url": "https://openrouter.ai/api/v1"},
    "cerebras": {"url": "https://api.cerebras.ai/v1", "json": "objeto"},
    "mistral": {"url": "https://api.mistral.ai/v1", "json": "objeto"},
    "kilo": {"url": "https://api.kilo.ai/api/gateway", "sin_clave": True, "max_tokens": 4096},
    "llm7": {"url": "https://api.llm7.io/v1", "sin_clave": True},
    "ovh": {"url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "sin_clave": True},
}
MAX_TOKENS = 2048
TIMEOUT = 45
# Tope de tiempo para la IA en todo el ciclo (epoch s; None = sin tope). GitHub cobra
# cada ciclo por minuto entero: si el ciclo pasa de 60 s cuesta el doble y los 2.000
# minutos gratis del mes no alcanzan. Lo fija el runner al empezar el ciclo.
LIMITE_TS = None


class SinTiempo(ValueError):
    pass


def _timeout():
    """Segundos que le quedan a esta llamada: lo que falte para el tope, nunca más de TIMEOUT."""
    if LIMITE_TS is None:
        return TIMEOUT
    quedan = LIMITE_TS - time.time()
    if quedan < 4:
        raise SinTiempo("sin tiempo en este ciclo (para no gastar minutos de GitHub)")
    return min(TIMEOUT, quedan)

# Modelo que respondió en la última prueba de la app (Config → semáforo): se prueba
# primero dentro de su proveedor.
PREFERIDO = None
# Lista de proveedores que manda la nube: [{id, url, key, modelos, reserva}] (None = nube vieja).
# `modelos` = los que marcaste para trabajar; `reserva` = los demás, solo si uno marcado falla.
PROVEEDORES = None
# Consenso: {"enabled": bool, "size": 2-5, o 0 = todos los modelos activos (hasta 5)}.
CONSENSO = {"enabled": False, "size": 3}


def configurar(lista=None, consenso=None):
    """La nube manda en cada ciclo la lista de proveedores (ya descifrada) y el consenso."""
    global PROVEEDORES, CONSENSO
    PROVEEDORES = [p for p in (lista or []) if p.get("url") and (p.get("modelos") or p.get("reserva"))] or None
    c = consenso or {}
    size = c.get("size")
    # size 0 = «todas»: votan todos tus modelos activos (hasta 5).
    CONSENSO = {"enabled": bool(c.get("enabled")), "size": 0 if size == 0 else max(2, min(5, int(size or 3)))}


def _id_de_url(url):
    for k, v in CATALOGO.items():
        if url.rstrip("/").startswith(v["url"]):
            return k
    return re.sub(r"^https?://(api\.)?", "", url).split("/")[0]


def _ordenar(modelos):
    if PREFERIDO and PREFERIDO in modelos:
        return [PREFERIDO] + [m for m in modelos if m != PREFERIDO]
    return modelos


def proveedor():
    """El proveedor de los secretos de GitHub: (url, clave, [modelos], nombre) o None."""
    url = os.getenv("IA_URL", "").strip().rstrip("/")
    if url:
        modelos = [m.strip() for m in os.getenv("IA_MODELOS", "").split(",") if m.strip()]
        nombre = re.sub(r"^https?://(api\.)?", "", url).split("/")[0]
        return url, os.getenv("IA_CLAVE", "").strip(), _ordenar(modelos), nombre
    clave = os.getenv("GROQ_API_KEY", "").strip()
    if clave:
        return GROQ_URL, clave, _ordenar([m for m in [os.getenv("GROQ_MODEL")] + GROQ_MODELOS if m]), "groq.com"
    return None


def proveedores():
    """[{id, url, key, modelos, nombre}] en orden de prioridad."""
    if PROVEEDORES:
        return [{**p, "url": p["url"].rstrip("/"), "modelos": _ordenar(list(p.get("modelos") or [])),
                 "reserva": _ordenar(list(p.get("reserva") or [])), "nombre": p.get("id") or _id_de_url(p["url"])}
                for p in PROVEEDORES]
    prov = proveedor()
    if not prov:
        return []
    url, clave, modelos, nombre = prov
    return [{"id": _id_de_url(url), "url": url, "key": clave, "modelos": modelos, "nombre": nombre}]


def extraer_json(texto):
    """Algunos modelos envuelven el JSON en ```json```, en <think>…</think> o en texto."""
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S).strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    try:
        return json.loads(texto)
    except ValueError:
        # El primer objeto JSON completo (hay modelos que lo repiten o agregan texto o emojis).
        a = texto.find("{")
        while a >= 0:
            try:
                return json.JSONDecoder().raw_decode(texto[a:])[0]
            except ValueError:
                a = texto.find("{", a + 1)
        raise ValueError("JSON inválido")


# Esquema estricto para Groq (gpt-oss y qwen): un objeto con cualquier lista de opiniones.
ESQUEMA = {"name": "respuesta", "strict": False, "schema": {"type": "object"}}


def _cuerpo(url, modelo, mensajes, formato_json):
    """Cada proveedor con lo suyo: Groq con json_schema y razonamiento corto (menos llama),
    Mistral y Cerebras con json_object, los demás solo max_tokens."""
    cat = CATALOGO.get(_id_de_url(url), {})
    datos = {"model": modelo, "messages": mensajes, "temperature": 0.2, "max_tokens": cat.get("max_tokens", MAX_TOKENS)}
    if formato_json:
        modo = cat.get("json")
        if modo == "schema" and "llama" not in modelo.lower():
            datos["response_format"] = {"type": "json_schema", "json_schema": ESQUEMA}
            datos["reasoning_effort"] = "low"
        elif modo:
            datos["response_format"] = {"type": "json_object"}
    return datos


def _json_completo(texto):
    try:
        return extraer_json(texto or "") is not None
    except ValueError:
        return False


def _llamar(url, clave, modelo, mensajes, formato_json=True):
    datos = _cuerpo(url, modelo, mensajes, formato_json)
    # Groq bloquea (error 1010) los user-agent que no parecen de una herramienta conocida.
    cab = {"Content-Type": "application/json", "User-Agent": "curl/8.14.1" if url.startswith(GROQ_URL) else "kumo-bot/1.0"}
    if clave:  # los gratuitos sin clave no llevan Authorization
        cab["Authorization"] = f"Bearer {clave}"
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(datos).encode(), method="POST", headers=cab)
    with urllib.request.urlopen(req, timeout=_timeout()) as r:
        resp = json.loads(r.read().decode())
    eleccion = (resp.get("choices") or [{}])[0]
    contenido = (eleccion.get("message") or {}).get("content")
    # Los modelos que razonan pueden gastar todos los tokens antes de escribir la respuesta.
    if eleccion.get("finish_reason") == "length" and not _json_completo(contenido):
        raise ValueError("se cortó (límite de tokens)")
    if not contenido or not str(contenido).strip():
        raise ValueError("respuesta vacía")
    return contenido


# Bitácora de llamadas a la IA de este ciclo (se manda en el reporte para que la
# app muestre si la IA está respondiendo, con qué modelo y cuánto tarda).
LOG = []


def _anotar(proveedor_, modelo, ok, t0, error=None):
    LOG.append({"ts": int(time.time() * 1000), "proveedor": proveedor_, "modelo": modelo, "ok": ok,
                "ms": int((time.time() - t0) * 1000), "error": error})


def _explicar(e):
    if isinstance(e, urllib.error.HTTPError):
        return "sin cuota (429)" if e.code == 429 else f"HTTP {e.code}"
    if isinstance(e, TimeoutError) or "timed out" in str(e):
        return "timeout"
    if isinstance(e, ValueError):
        return next((m for m in ("respuesta vacía", "se cortó (límite de tokens)") if m in str(e)), "JSON inválido")
    return type(e).__name__


def preguntar(prov, modelo, mensajes):
    """Una IA: (datos|None, error|None). Reintenta sin formato JSON si el modelo no lo acepta."""
    for formato_json in (True, False):
        t0 = time.time()
        try:
            datos = extraer_json(_llamar(prov["url"], prov.get("key") or "", modelo, mensajes, formato_json))
            _anotar(prov["nombre"], modelo, True, t0)
            return datos, None
        except urllib.error.HTTPError as e:
            _anotar(prov["nombre"], modelo, False, t0, _explicar(e))
            if e.code == 400 and formato_json:
                continue  # no acepta response_format: reintenta sin él
            return None, (e.code, _explicar(e))
        except Exception as e:  # red, timeout, JSON inválido, respuesta vacía
            _anotar(prov["nombre"], modelo, False, t0, _explicar(e))
            return None, (0, _explicar(e))
    return None, (0, "error")


def _candidatos():
    """[(prov, modelo)] en orden de prioridad: primero los modelos marcados y al final
    los de reserva."""
    provs = proveedores()
    return _marcados(provs) + [(p, m) for p in provs for m in p.get("reserva") or []]


def _marcados(provs=None):
    return [(p, m) for p in (proveedores() if provs is None else provs) for m in p["modelos"]]


def chat_json(mensajes):
    """Cadena de respaldo: el primer proveedor y modelo que responda JSON.
    Devuelve (dict, modelo, error); `ULTIMO_RESPALDO` guarda de qué falló antes."""
    global ULTIMO_RESPALDO
    ULTIMO_RESPALDO = []
    cands = _candidatos()
    if not cands:
        return None, None, "Sin IA configurada"
    ultimo, caidos = None, set()
    for prov, modelo in cands:
        if prov["nombre"] in caidos:
            continue
        datos, err = preguntar(prov, modelo, mensajes)
        if datos is not None:
            return datos, modelo, None
        codigo, texto = err
        ultimo = f"{prov['nombre']} HTTP {codigo} con {modelo}" if codigo else f"{prov['nombre']}: {texto} con {modelo}"
        ULTIMO_RESPALDO.append({"proveedor": prov["nombre"], "modelo": modelo, "error": texto})
        if codigo in (401, 403) and prov.get("key"):
            caidos.add(prov["nombre"])  # clave rechazada: sus otros modelos tampoco van a responder
    return None, None, ultimo


ULTIMO_RESPALDO = []


def elegir_votantes(n):
    """Un modelo de cada proveedor distinto primero (votos independientes y cuotas
    separadas) y luego el resto, todo en orden de prioridad."""
    provs = proveedores()
    primero, resto, vistos = [], [], set()
    for prov, modelo in _marcados(provs):
        (resto if prov["nombre"] in vistos else primero).append((prov, modelo))
        vistos.add(prov["nombre"])
    return primero + resto + [(p, m) for p in provs for m in p.get("reserva") or []]


def tamano_consenso():
    """Cuántas IAs votan: las que elegiste (2–5) o, con «todas» (size 0), todos tus
    modelos activos hasta 5."""
    total = len(_marcados()) or len(_candidatos())  # votan los marcados; la reserva solo cubre fallos
    return min(5, total) if not CONSENSO["size"] else min(CONSENSO["size"], total)


def consultar_varias(mensajes, n, valido=lambda d: True):
    """Pregunta a `n` IAs en paralelo y, si alguna falla, llama a las siguientes de tu
    lista hasta juntar `n` votos o quedarse sin modelos (como mucho n + 6 llamadas).
    Una respuesta que no trae ningún voto válido (`valido`) cuenta como fallida:
    «formato inválido». Devuelve ([(etiqueta, datos)], [errores])."""
    cola = elegir_votantes(n)[:n + 6]
    con_clave = {p["nombre"] for p, _ in cola if p.get("key")}
    ok, errores, caidos = [], [], set()

    def una(pm):
        prov, modelo = pm
        return pm, preguntar(prov, modelo, mensajes)

    while cola and len(ok) < n:
        if LIMITE_TS is not None and LIMITE_TS - time.time() < 4:
            errores.append({"proveedor": "-", "modelo": "-", "error": "sin tiempo en este ciclo (para no gastar minutos de GitHub)"})
            break
        ronda, cola = cola[:n - len(ok)], cola[n - len(ok):]
        ronda = [pm for pm in ronda if pm[0]["nombre"] not in caidos]
        if not ronda:
            continue
        with ThreadPoolExecutor(max_workers=len(ronda)) as ex:
            for (prov, modelo), (datos, err) in ex.map(una, ronda):
                if datos is not None and valido(datos):
                    ok.append((modelo, datos))
                    continue
                error = err[1] if err else "formato inválido"
                errores.append({"proveedor": prov["nombre"], "modelo": modelo, "error": error})
                # Una clave rechazada salta los demás modelos de ese proveedor.
                if error in ("HTTP 401", "HTTP 403") and prov["nombre"] in con_clave:
                    caidos.add(prov["nombre"])
    return ok, errores


def corto(modelo):
    """«nvidia/nemotron-3-super-120b-a12b:free» → «nemotron-3-super-120b-a12b»."""
    return str(modelo).split("/")[-1].replace(":free", "")


def votar(votos, espera):
    """votos = [(modelo, {accion, confianza, razon, prob?})] de UN activo.
    Cada IA es un voto: gana la acción con más votos y, si hay empate, `espera`
    (ESPERAR / NO ENTRAR / MANTENER). Confianza final = media de quienes votaron la
    ganadora × (0.5 + 0.5 × acuerdo): con 3/3 se conserva, con 2/3 baja un 17 %."""
    if not votos:
        return None
    if len(votos) == 1:
        return dict(votos[0][1])
    cuenta = {}
    for _, v in votos:
        cuenta[v["accion"]] = cuenta.get(v["accion"], 0) + 1
    mejor = max(cuenta.values())
    ganadoras = [a for a, k in cuenta.items() if k == mejor]
    accion = ganadoras[0] if len(ganadoras) == 1 else espera
    a_favor = [v for _, v in votos if v["accion"] == accion]
    acuerdo = len(a_favor) / len(votos)
    media = statistics.mean(float(v.get("confianza") or 0) for v in a_favor) if a_favor else 0.0
    conf = int(round(media * (0.5 + 0.5 * acuerdo)))
    lider = max(a_favor, key=lambda v: float(v.get("confianza") or 0)) if a_favor else max((v for _, v in votos), key=lambda v: float(v.get("confianza") or 0))
    detalle = ", ".join(f"{corto(m)} {v['accion']} {int(v.get('confianza') or 0)}" for m, v in votos)
    out = {"accion": accion, "confianza": conf, "razon": f"Consenso {len(a_favor)}/{len(votos)} ({detalle}): {lider.get('razon', '')}"[:300],
           "votos": [{"modelo": corto(m), "accion": v["accion"], "confianza": int(v.get("confianza") or 0)} for m, v in votos]}
    if any("prob" in v for _, v in votos):
        # La probabilidad final es la mediana, solo si al menos la mitad dio una; para
        # comprar, solo de quienes no votaron vender.
        probs = [v["prob"] for _, v in votos if v.get("prob") is not None and not (accion.startswith("COMPRAR") and v["accion"] == "VENDER")]
        out["prob"] = statistics.median(probs) if probs and len(probs) * 2 >= len(votos) else None
    return out


def decidir(mensajes, leer, espera):
    """Consenso si está activo y hay al menos 2 IAs; si no, cadena de respaldo.
    `leer(datos)` → {clave: opinión}; `espera(clave)` = acción si hay empate.
    Devuelve ({clave: opinión}, modelo, error)."""
    n = tamano_consenso()
    if not CONSENSO["enabled"] or tamano_consenso() < 2:
        datos, modelo, error = chat_json(mensajes)
        return (leer(datos), modelo, None) if datos is not None else ({}, None, error)
    ok, errores = consultar_varias(mensajes, n, lambda d: bool(leer(d)))
    ia_errores = [e for e in errores if e["error"] == "formato inválido"]
    for e in ia_errores:
        LOG.append({"ts": int(time.time() * 1000), "proveedor": e["proveedor"], "modelo": e["modelo"], "ok": False, "ms": 0, "error": "formato inválido (no se contó su voto)"})
    if not ok:
        return {}, None, "Ninguna IA respondió: " + "; ".join(f"{e['proveedor']} {corto(e['modelo'])}: {e['error']}" for e in errores[:4])
    if len(ok) == 1:
        return leer(ok[0][1]), ok[0][0], None
    porclave = {}
    for modelo, datos in ok:
        for k, v in leer(datos).items():
            porclave.setdefault(k, []).append((modelo, v))
    out = {k: votar(vs, espera(k)) for k, vs in porclave.items()}
    return out, "consenso:" + "+".join(corto(m) for m, _ in ok), None


def varias_json(mensajes, valido=lambda d: True):
    """Como `decidir`, pero devuelve las respuestas crudas para que quien llama las combine:
    ([(modelo, datos)], error). Con consenso apagado o una sola IA, una respuesta (cadena)."""
    if not CONSENSO["enabled"] or tamano_consenso() < 2:
        datos, modelo, error = chat_json(mensajes)
        if datos is None:
            return [], error
        return ([(modelo, datos)], None) if valido(datos) else ([], f"{corto(modelo)}: formato inválido")
    ok, errores = consultar_varias(mensajes, tamano_consenso(), valido)
    for e in errores:
        if e["error"] == "formato inválido":
            LOG.append({"ts": int(time.time() * 1000), "proveedor": e["proveedor"], "modelo": e["modelo"], "ok": False, "ms": 0, "error": "formato inválido (no se contó su voto)"})
    if not ok:
        return [], "Ninguna IA respondió: " + "; ".join(f"{e['proveedor']} {corto(e['modelo'])}: {e['error']}" for e in errores[:4])
    return ok, None


def _filas(datos, clave):
    """Las opiniones vengan como vengan: {"opiniones":[…]}, otra lista de objetos o un
    diccionario por activo ({"BTC": {"senal": …}}). Cada fila lleva `clave` y «accion»."""
    if not isinstance(datos, dict):
        return []
    filas = datos.get("opiniones")
    if not isinstance(filas, list):
        filas = next((v for v in datos.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
    if filas is None:
        filas = [{clave: k, **v} for k, v in datos.items() if isinstance(v, dict)]
    out = []
    for f in filas:
        if isinstance(f, dict):
            acc = next((f[k] for k in ("accion", "acción", "senal", "señal", "action", "decision") if f.get(k)), None)
            out.append({**f, "accion": acc})
    return out


def _leer_opiniones(datos):
    out = {}
    for o in _filas(datos, "simbolo"):
        try:
            sym = str(o.get("simbolo", "")).upper()
            if sym:
                acc = str(o.get("accion") or "ESPERAR").upper().strip()
                acc = {"BUY": "COMPRAR", "SELL": "VENDER", "HOLD": "ESPERAR", "MANTENER": "ESPERAR"}.get(acc, acc)
                if acc not in ("COMPRAR", "VENDER", "ESPERAR"):
                    continue
                out[sym] = {"accion": acc, "confianza": max(0, min(100, int(float(o.get("confianza", 0) or 0)))), "razon": str(o.get("razon", ""))[:200]}
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def opinar(propuestas, contexto, vigilar=None):
    """Devuelve ({SIMBOLO: {accion, confianza, razon}}, modelo|None, error|None).
    Si no hay propuestas, da su señal sobre las monedas de `vigilar` para que el
    usuario vea en cada ciclo qué piensa la IA del mercado."""
    if not propuestas and not vigilar:
        return {}, None, None
    if propuestas:
        filas = [{k: p.get(k) for k in ("simbolo", "accion", "precio", "rsi", "tendencia", "peso", "objetivo", "pnl", "motivo")} for p in propuestas]
        pedido = ("La estrategia automática propone estas operaciones spot. Para cada símbolo opina "
                  "COMPRAR, VENDER o ESPERAR, con confianza 0-100 y una razón breve (máx. 20 palabras). "
                  "Sé escéptico: si los datos no lo justifican, ESPERAR.\n"
                  f"Propuestas: {json.dumps(filas, ensure_ascii=False)}\n")
        extra = [s for s in (vigilar or []) if s not in {p["simbolo"] for p in propuestas}]
        if extra:
            pedido += f"Da también tu señal para: {', '.join(extra)}.\n"
    else:
        pedido = ("No hay operaciones propuestas en este ciclo. Da tu señal spot para cada una de estas "
                  f"monedas: {', '.join(vigilar)}. Para cada una: COMPRAR, VENDER o ESPERAR, confianza 0-100 "
                  "y una razón breve (máx. 20 palabras). Sé escéptico: si los datos no lo justifican, ESPERAR.\n")
    mensajes = [
        {"role": "system", "content": "Eres un analista de riesgo cripto prudente. Respondes SOLO JSON válido en español."},
        {"role": "user", "content": (
            pedido + f"Contexto: {json.dumps(contexto, ensure_ascii=False)}\n"
            'Formato: {"opiniones":[{"simbolo":"BTC","accion":"ESPERAR","confianza":60,"razon":"..."}]}'
        )},
    ]
    return decidir(mensajes, _leer_opiniones, lambda s: "ESPERAR")


VALIDAS_PRED = {"COMPRAR", "NO ENTRAR", "MANTENER", "VENDER", "COMPRAR MÁS"}


def _leer_pred(datos):
    out = {}
    for o in _filas(datos, "coin"):
        try:
            coin, acc = str(o.get("coin", "")).strip(), str(o.get("accion") or "").upper().strip().replace("COMPRAR MAS", "COMPRAR MÁS")
            if not re.match(r"^#\d+$", coin) or acc not in VALIDAS_PRED:
                continue
            prob = o.get("prob")
            out[coin] = {"accion": acc, "confianza": max(0, min(100, int(float(o.get("confianza", 0) or 0)))),
                         "prob": max(0.0, min(100.0, float(prob))) if prob not in (None, "") else None, "razon": str(o.get("razon", ""))[:200]}
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def opinar_predicciones(candidatos, posiciones, glob=None, noticias=None, feedback=None):
    """La IA revisa mercados de predicción con ventaja según el modelo y tus
    posiciones, con el mercado global y las noticias de 24 h como contexto.
    Devuelve ({"#N": {accion, confianza, prob, razon}}, modelo, error)."""
    if not candidatos and not posiciones:
        return {}, None, None
    pedido = ("Mercados de predicción de Hyperliquid: cada unidad paga 1 USDC si acierta y 0 si no; el precio es la "
              "probabilidad que da el mercado. «prob_modelo_pct» sale de un modelo log-normal con la volatilidad de 1 h, 24 h "
              "y 48 h por tramos (no es certeza); «mov_tipico_1h_24h_48h_pct» es cuánto se mueve el subyacente normalmente y "
              "«volatilidad» = turbulento si la última hora se mueve mucho más que lo normal. Sé escéptico: el modelo ignora "
              "noticias y el mercado puede saber algo más. REGLA: cuando el mercado da menos de 5 % a un lado, casi siempre ya está "
              "prácticamente decidido; no propongas COMPRAR ni COMPRAR MÁS de ese lado salvo que el precio actual, las horas que quedan y "
              "el movimiento típico lo justifiquen con números (una tendencia de 24 h no basta).\n")
    if glob and glob.get("resumen"):
        pedido += (f"Mercado global ahora: {glob['resumen']} Úsalo en tu decisión: con aversión al riesgo o turbulencia sé más "
                   "exigente con apuestas a que el precio suba o se mantenga quieto.\n")
    if noticias:
        pedido += f"Titulares de las últimas 24 h: {json.dumps(noticias[:12], ensure_ascii=False)}\n"
    if feedback:
        pedido += feedback + "\n"
    if any(c.get("senales") for c in candidatos or []):
        pedido += ("«senales» del subyacente (velas de 15 min): tendencia (medias de 3 h vs 12 h), MACD y RSI de 1 h. Son contexto, "
                   "no certeza: en una prueba con ~1 400 mercados ya resueltos NO mejoraron al modelo estadístico por sí solas. "
                   "Úsalas solo si refuerzan lo que dicen los números (distancia al objetivo, horas, movimiento típico).\n")
    if any(c.get("prob_por_ventana_pct") for c in candidatos or []):
        pedido += ("«prob_por_ventana_pct» es la probabilidad del mismo modelo midiendo la volatilidad en 1 h, 3 h, 6 h, 12 h, 24 h y 48 h. "
                   "Si casi todas superan el precio, la ventaja es sólida; si se contradicen, sé prudente.\n")
    if any(c.get("prob_modelo_corregida_pct") is not None for c in candidatos or []):
        pedido += ("«prob_modelo_corregida_pct» es la del modelo ajustada con los resultados reales de este agente: confía más en ella que en la original.\n")
    if candidatos:
        pedido += ("Candidatos para ENTRAR (el modelo les ve ventaja). Para cada uno: COMPRAR o NO ENTRAR.\n"
                   f"{json.dumps(candidatos, ensure_ascii=False)}\n")
    if posiciones:
        pedido += ("Posiciones que YA tiene el usuario. Para cada una: MANTENER (hasta el vencimiento), VENDER (cerrar ahora) "
                   "o COMPRAR MÁS (solo si la señal es fuerte y el precio sigue barato). REGLA DEL USUARIO: nunca vender con pérdida, "
                   "por mínima que sea; VENDER solo si «puede_vender_sin_perdida» es true (precio ≥ «vender_solo_desde»). Si no, MANTENER.\n"
                   f"{json.dumps(posiciones, ensure_ascii=False)}\n")
    mensajes = [
        {"role": "system", "content": "Eres un analista prudente de mercados de predicción. Respondes SOLO JSON válido en español."},
        {"role": "user", "content": pedido + 'Para cada «coin» da confianza 0-100, tu probabilidad de que ESE lado acierte (prob 0-100) y una razón breve (máx. 20 palabras).\n'
                                             'Formato: {"opiniones":[{"coin":"#90170","accion":"NO ENTRAR","confianza":60,"prob":55,"razon":"..."}]}'},
    ]
    en_cartera = {p.get("coin") for p in posiciones or []}
    return decidir(mensajes, _leer_pred, lambda c: "MANTENER" if c in en_cartera else "NO ENTRAR")
