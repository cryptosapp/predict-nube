"""Adaptadores de exchange para Kumo Bot.

Todos exponen la misma interfaz mínima:
    saldos() -> {ACTIVO: cantidad}
    precios(simbolos) -> {SIMBOLO: precio en la moneda base (quote)}
    velas(simbolo, marco, n) -> [cierres]
    velas_ts(simbolo, marco, n) -> [(ms, cierre)]
    mercados_top(n) -> [{simbolo, precio, cambio_24h, volumen}]
    comprar(simbolo, monto_quote) -> {cantidad, precio, total}
    vender(simbolo, cantidad) -> {cantidad, precio, total}

- CcxtExchange: cualquier exchange soportado por ccxt (Binance, Kraken, OKX…).
- CryptoComApp: la API de la App de Crypto.com (wapi.crypto.com), que ccxt no trae.
- Simulador: usa los precios reales del exchange y una cartera virtual.
"""
import base64
import hashlib
import hmac
import json
import re
import time
import urllib.request
from decimal import Decimal, ROUND_DOWN

import ccxt

from predicciones import separar

# Exchanges que bloquean los servidores de EE. UU. donde corre GitHub Actions.
BLOQUEAN_EEUU = {"binance", "bybit", "okx", "bitget", "kucoin"}
BILLETERA = {"hyperliquid"}  # se conectan con dirección + clave privada en vez de API key


class ErrorExchange(Exception):
    pass


def _f(x, defecto=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return defecto


def _cambio(t, precio):
    """% en 24 h; Hyperliquid no lo da en el ticker, pero sí el precio de ayer (prevDayPx)."""
    if t.get("percentage") is not None:
        return _f(t.get("percentage"))
    previo = _f(t.get("open")) or _f((t.get("info") or {}).get("prevDayPx"))
    return (precio / previo - 1) * 100 if previo > 0 else 0.0


class CcxtExchange:
    def __init__(self, exchange_id, quote="USDT", api_key="", secret="", password="", par_quote=None):
        if exchange_id not in ccxt.exchanges:
            raise ErrorExchange(f"Exchange «{exchange_id}» no existe en ccxt")
        opciones = {"enableRateLimit": True, "timeout": 20000}
        if exchange_id in BILLETERA:
            # DEX sin KYC: «API key» = dirección de la cuenta (0x…), «secret» = clave
            # privada de una API wallet (agente que opera pero no puede retirar).
            opciones["options"] = {"defaultType": "spot"}
            if api_key:
                opciones.update({"walletAddress": api_key.strip(), "privateKey": secret.strip()})
        elif api_key:
            opciones.update({"apiKey": api_key, "secret": secret})
        if password:
            opciones["password"] = password
        self.id = exchange_id
        self.quote = quote.upper()
        self.par_quote = (par_quote or quote).upper()  # moneda de los pares (p. ej. USD en Crypto.com)
        self.ex = getattr(ccxt, exchange_id)(opciones)
        self.con_claves = bool(api_key)
        self._mercados = None
        self._cuenta_lista = exchange_id not in BILLETERA
        self.aviso_cuenta = None  # explicación para el usuario si se corrigió la dirección
        self.predicciones = {}  # {"+N": cantidad} de mercados de predicción (Hyperliquid)
        self.entradas = {}  # {"+N" o "HYPE": precio promedio de compra según Hyperliquid}

    def _info_hl(self, cuerpo):
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "kumo-bot/1.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def cuenta(self):
        """Hyperliquid: los fondos viven en la cuenta principal, no en la API wallet que firma.
        Si el usuario pegó la dirección de la API wallet (o una dirección sin cuenta), se busca
        la principal con «userRole» y se usa esa para leer saldos."""
        if self._cuenta_lista or not self.con_claves:
            return
        self._cuenta_lista = True
        try:
            pegada = self.ex.walletAddress
            rol = self._info_hl({"type": "userRole", "user": pegada})
            if rol.get("role") in ("user", "vault", "subAccount"):
                return
            if rol.get("role") == "agent":
                principal = (rol.get("data") or {}).get("user")
                motivo = "la dirección que pegaste es la de la API wallet"
            else:  # «missing»: quizá la clave privada es de una API wallet de otra cuenta
                firmante = self.ex.eth_get_address_from_private_key(self.ex.privateKey)
                rol_f = self._info_hl({"type": "userRole", "user": firmante})
                if rol_f.get("role") == "agent":
                    principal = (rol_f.get("data") or {}).get("user")
                elif rol_f.get("role") == "user":
                    principal = firmante  # la clave es de la billetera principal (puede retirar: mejor una API wallet)
                else:
                    principal = None
                motivo = "la dirección que pegaste no tiene cuenta en Hyperliquid"
            if principal:
                self.ex.walletAddress = principal
                self.aviso_cuenta = f"Hyperliquid: {motivo}; uso tu cuenta principal {principal[:6]}…{principal[-4:]}. Corrígelo en «Cambiar claves» cuando puedas."
            else:
                self.aviso_cuenta = f"Hyperliquid: {motivo} y no encontré tu cuenta principal. Pega la dirección de tu billetera principal en «Cambiar claves»."
        except Exception as e:  # sin red o respuesta rara: se sigue con la dirección pegada
            self.aviso_cuenta = f"Hyperliquid: no pude comprobar tu dirección ({type(e).__name__})"

    def _par(self, simbolo):
        return f"{simbolo.upper()}/{self.par_quote}"

    def mercados(self):
        if self._mercados is None:
            try:
                self._mercados = self.ex.load_markets()
            except ccxt.BaseError as e:
                raise ErrorExchange(_explicar(self.id, e))
        return self._mercados

    def existe(self, simbolo):
        m = self.mercados().get(self._par(simbolo))
        return bool(m and m.get("spot", True) and m.get("active", True) is not False)

    def saldos(self):
        if not self.con_claves:
            raise ErrorExchange("Sin claves del exchange: solo modo simulación")
        self.cuenta()
        try:
            b = self.ex.fetch_balance()
        except ccxt.BaseError as e:
            raise ErrorExchange(_explicar(self.id, e))
        total = b.get("total") or {}
        saldos = {k.upper(): _f(v) for k, v in total.items() if _f(v) > 0}
        # Hyperliquid: los mercados de predicción («+N») no son monedas; van aparte.
        saldos, self.predicciones = separar(saldos)
        # Hyperliquid da lo que pagaste por cada token («entryNtl»): precio promedio de compra.
        # Vale para predicciones y monedas spot (así Kumo sabe tu costo real, no uno estimado).
        self.entradas = {}
        for x in ((b.get("info") or {}).get("balances") or []) if isinstance(b.get("info"), dict) else []:
            coin, tot, ntl = str(x.get("coin") or "").upper(), _f(x.get("total")), _f(x.get("entryNtl"))
            if (coin in self.predicciones or coin in saldos) and coin not in ESTABLES and tot > 0 and ntl > 0:
                self.entradas[coin] = ntl / tot
        return saldos

    def saldo_perps(self):
        """USDC en la cuenta de futuros (Perps). Hyperliquid la separa de Spot, que es donde opera Kumo."""
        if self.id != "hyperliquid" or not self.con_claves:
            return 0.0
        self.cuenta()
        try:
            b = self.ex.fetch_balance({"type": "swap"})
        except ccxt.BaseError:
            return 0.0
        return _f((b.get("total") or {}).get("USDC"))

    def mejor_precio_prediccion(self, cod, comprar):
        """Mejor precio contrario en el libro: quien vende (si compras) o quien compra (si vendes)."""
        try:
            niveles = self._info_hl({"type": "l2Book", "coin": f"#{int(cod)}"}).get("levels") or [[], []]
            lado = niveles[1] if comprar else niveles[0]
            return {"precio": _f(lado[0]["px"]), "unidades": _f(lado[0]["sz"])} if lado else None
        except Exception:
            return None

    def orden_prediccion(self, cod, comprar, unidades, limite):
        """Hyperliquid HIP-4: compra o vende `unidades` del lado «#cod» a `limite` o mejor.
        Es IOC (se llena ya lo que se pueda; lo demás se cancela): nunca queda una orden
        olvidada en el libro. ccxt no conoce estos mercados, así que la acción se arma y
        firma aquí con la API wallet (activo = 100 000 000 + cod)."""
        if self.id != "hyperliquid" or not self.con_claves:
            raise ErrorExchange("Las predicciones solo se operan en Hyperliquid con tus claves")
        if not (0.001 <= limite <= 0.999) or int(unidades) < 1:
            raise ErrorExchange("Precio límite entre 0.001 y 0.999 y al menos 1 unidad")
        px = f"{limite:.5g}"  # Hyperliquid acepta hasta 5 cifras significativas (p. ej. 0.81001)
        accion = {"type": "order", "orders": [{"a": 100_000_000 + int(cod), "b": bool(comprar), "p": px, "s": str(int(unidades)),
                                               "r": False, "t": {"limit": {"tif": "Ioc"}}}], "grouping": "na"}
        nonce = self.ex.milliseconds()
        try:
            firma = self.ex.sign_l1_action(accion, nonce)
            resp = self.ex.private_post_exchange({"action": accion, "nonce": nonce, "signature": firma})
        except ccxt.BaseError as e:
            # ccxt convierte los errores de Hyperliquid en excepción con el JSON dentro.
            m = re.search(r'"error":\s*"([^"]+)"', str(e)) or re.search(r'"response":\s*"([^"]+)"', str(e))
            texto = m.group(1) if m else str(e)
            if "could not immediately match" in texto:
                return {"cantidad": 0.0, "precio": 0.0, "total": 0.0, "id": None, "mejor": self.mejor_precio_prediccion(cod, comprar)}
            raise ErrorExchange(f"Hyperliquid: {texto[:200]}")
        if not isinstance(resp, dict) or resp.get("status") != "ok":
            raise ErrorExchange(f"Hyperliquid: {str((resp or {}).get('response') if isinstance(resp, dict) else resp)[:200]}")
        estado = (((resp.get("response") or {}).get("data") or {}).get("statuses") or [{}])[0]
        if isinstance(estado, dict) and estado.get("error"):
            if "could not immediately match" in estado["error"]:
                return {"cantidad": 0.0, "precio": 0.0, "total": 0.0, "id": None, "mejor": self.mejor_precio_prediccion(cod, comprar)}
            raise ErrorExchange(f"Hyperliquid: {estado['error'][:200]}")
        lleno = estado.get("filled") if isinstance(estado, dict) else None
        if not lleno:
            return {"cantidad": 0.0, "precio": 0.0, "total": 0.0, "id": None, "mejor": self.mejor_precio_prediccion(cod, comprar)}
        cant, precio = _f(lleno.get("totalSz")), _f(lleno.get("avgPx"))
        return {"cantidad": cant, "precio": precio, "total": round(cant * precio, 4), "id": lleno.get("oid")}

    def precios(self, simbolos):
        pares = [self._par(s) for s in simbolos if s.upper() != self.quote and self.existe(s)]
        out = {}
        if not pares:
            return out
        try:
            tickers = self.ex.fetch_tickers(pares) if self.ex.has.get("fetchTickers") else {p: self.ex.fetch_ticker(p) for p in pares}
        except ccxt.BaseError:
            tickers = {}
            for p in pares:
                try:
                    tickers[p] = self.ex.fetch_ticker(p)
                except ccxt.BaseError:
                    pass
        for par, t in tickers.items():
            precio = _f(t.get("last") or t.get("close"))
            if precio > 0:
                out[par.split("/")[0]] = {"precio": precio, "cambio_24h": _cambio(t, precio), "volumen": _f(t.get("quoteVolume"))}
        return out

    def velas(self, simbolo, marco="1h", n=100):
        return [c for _, c in self.velas_ts(simbolo, marco, n)]

    def velas_ts(self, simbolo, marco="1h", n=100):
        """[(ms_apertura, cierre)] de las últimas n velas."""
        try:
            datos = self.ex.fetch_ohlcv(self._par(simbolo), marco, limit=n)
        except ccxt.BaseError as e:
            raise ErrorExchange(_explicar(self.id, e))
        return [(int(v[0]), _f(v[4])) for v in datos if v and _f(v[4]) > 0]

    def mercados_top(self, n=20):
        try:
            tickers = self.ex.fetch_tickers()
        except ccxt.BaseError as e:
            raise ErrorExchange(_explicar(self.id, e))
        filas = []
        for par, t in tickers.items():
            if not par.endswith("/" + self.par_quote) or ":" in par:
                continue
            base = par.split("/")[0]
            if base in ESTABLES:
                continue
            vol = _f(t.get("quoteVolume"))
            precio = _f(t.get("last"))
            if vol <= 0 or precio <= 0:
                continue
            filas.append({"simbolo": base, "precio": precio, "cambio_24h": round(_cambio(t, precio), 2), "volumen": round(vol)})
        filas.sort(key=lambda f: -f["volumen"])
        return filas[:n]

    def _ajustar(self, par, cantidad):
        try:
            return float(self.ex.amount_to_precision(par, cantidad))
        except ccxt.BaseError:
            return cantidad

    def comprar(self, simbolo, monto_quote):
        par = self._par(simbolo)
        try:
            precio = _f(self.ex.fetch_ticker(par).get("last"))
            if precio <= 0:
                raise ErrorExchange(f"Sin precio para {par}")
            if self.id in BILLETERA:
                # Hyperliquid no tiene órdenes a mercado puras: necesita el precio para el deslizamiento máximo.
                o = self.ex.create_order(par, "market", "buy", self._ajustar(par, monto_quote / precio), precio)
            elif self.ex.has.get("createMarketBuyOrderWithCost"):
                o = self.ex.create_market_buy_order_with_cost(par, monto_quote)
            else:
                o = self.ex.create_market_buy_order(par, self._ajustar(par, monto_quote / precio))
        except ccxt.BaseError as e:
            raise ErrorExchange(_explicar(self.id, e))
        return _resultado(o, precio)

    def vender(self, simbolo, cantidad, precio_min=None):
        """Vende a mercado. Con `precio_min` nunca vende por debajo: se mira el mejor
        comprador (bid) y en Hyperliquid la orden es IOC con ese límite, así el
        deslizamiento (hasta 5 % en una orden «a mercado») no puede dejarte en pérdida."""
        par = self._par(simbolo)
        try:
            t = self.ex.fetch_ticker(par)
            precio = _f(t.get("last"))
            bid = _f(t.get("bid"))
            if precio_min and bid and bid < precio_min:
                raise ErrorExchange(f"No vendí {simbolo}: el mejor comprador paga {bid:.6g} y sin pérdida es desde {precio_min:.6g}")
            if self.id in BILLETERA and precio_min:
                o = self.ex.create_order(par, "limit", "sell", self._ajustar(par, cantidad), _arriba_5cifras(precio_min), {"timeInForce": "Ioc"})
            elif self.id in BILLETERA:
                o = self.ex.create_order(par, "market", "sell", self._ajustar(par, cantidad), precio)
            else:
                o = self.ex.create_market_sell_order(par, self._ajustar(par, cantidad))
        except ccxt.BaseError as e:
            raise ErrorExchange(_explicar(self.id, e))
        r = _resultado(o, precio)
        if precio_min and self.id in BILLETERA and not _f(o.get("filled")):
            raise ErrorExchange(f"No vendí {simbolo}: nadie compraba a {precio_min:.6g} o más (no vendo con pérdida)")
        return r


def _arriba_5cifras(x):
    """Redondea HACIA ARRIBA a 5 cifras significativas (Hyperliquid no acepta más):
    un límite de venta redondeado hacia abajo podría quedar por debajo del mínimo."""
    import math
    if x <= 0:
        return x
    paso = 10 ** (math.floor(math.log10(x)) - 4)
    return round(math.ceil(round(x / paso, 6)) * paso, 12)


ESTABLES = {"USDT", "USDC", "USD", "DAI", "FDUSD", "TUSD", "BUSD", "USDP", "EUR", "PYUSD", "USDE", "USD1"}


def _resultado(orden, precio_ref):
    cantidad = _f(orden.get("filled") or orden.get("amount"))
    precio = _f(orden.get("average") or orden.get("price") or precio_ref)
    total = _f(orden.get("cost")) or cantidad * precio
    return {"cantidad": cantidad, "precio": precio, "total": total, "id": orden.get("id")}


def _explicar(exchange_id, error):
    texto = str(error)
    if exchange_id in BLOQUEAN_EEUU and re.search(r"451|403|restricted|not available|CloudFront", texto, re.I):
        return (f"{exchange_id} bloquea los servidores de EE. UU. donde corre GitHub Actions. "
                "Usa otro exchange (Kraken, Coinbase, Crypto.com, MEXC, Hyperliquid, Bitstamp) o un runner propio.")
    if isinstance(error, ccxt.AuthenticationError):
        return f"{exchange_id}: claves rechazadas. Revisa API key, secret y permisos de trading."
    if isinstance(error, ccxt.InsufficientFunds):
        return f"{exchange_id}: saldo insuficiente."
    return f"{exchange_id}: {texto[:240]}"


class CryptoComApp:
    """API de la App de Crypto.com. Opera con cotización + confirmación."""

    BASE = "https://wapi.crypto.com"

    def __init__(self, api_key, secret, quote="USDC"):
        if not api_key or not secret:
            raise ErrorExchange("Crypto.com App necesita API key y secret")
        self.id = "cryptocom_app"
        self.key = api_key
        self.secret = secret
        self.quote = quote.upper()
        self.con_claves = True
        # Precios y velas públicos del Crypto.com Exchange (mismo ecosistema).
        self.publico = crear_publico("cryptocom_app", self.quote)

    def _pedir(self, metodo, ruta, cuerpo=None):
        ts = str(int(time.time() * 1000))
        texto = json.dumps(cuerpo, separators=(",", ":")) if cuerpo is not None else ""
        firma = base64.b64encode(hmac.new(self.secret.encode(), (ts + metodo + ruta.split("?")[0] + texto).encode(), hashlib.sha256).digest()).decode()
        req = urllib.request.Request(self.BASE + ruta, method=metodo, data=texto.encode() if cuerpo is not None else None, headers={
            "Cdc-Api-Key": self.key, "Cdc-Api-Timestamp": ts, "Cdc-Api-Signature": firma,
            "Content-Type": "application/json", "User-Agent": "kumo-bot/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                datos = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                datos = json.loads(e.read().decode() or "{}")
            except ValueError:
                datos = {}
            if e.code == 401:
                raise ErrorExchange("Crypto.com App: claves rechazadas (401). La firma usa el secret: si no lo tienes "
                                    "o no corresponde a esa API key, borra la clave en la App y crea una nueva "
                                    "(el secret solo se muestra una vez). Luego cámbiala en Config → Cambiar claves.")
            raise ErrorExchange(f"Crypto.com App HTTP {e.code}: {datos.get('error_message') or datos.get('error') or ''}")
        if not datos.get("ok", False):
            raise ErrorExchange(f"Crypto.com App: {datos.get('error_message') or datos.get('error') or 'error'}")
        return datos

    def saldos(self):
        wallets = self._pedir("GET", "/v1/crypto-account").get("account", {}).get("wallets", [])
        out = {}
        for w in wallets:
            cant = _f((w.get("balance") or {}).get("amount"))
            if cant > 0:
                out[str(w.get("currency", "")).upper()] = cant
        return out

    def existe(self, simbolo):
        return self.publico.existe(simbolo)

    def precios(self, simbolos):
        return self.publico.precios(simbolos)

    def velas(self, simbolo, marco="1h", n=100):
        return self.publico.velas(simbolo, marco, n)

    def velas_ts(self, simbolo, marco="1h", n=100):
        return self.publico.velas_ts(simbolo, marco, n)

    def mercados_top(self, n=20):
        return self.publico.mercados_top(n)

    def _intercambio(self, desde, hacia, monto):
        cuerpo = {"from": desde, "to": hacia, "from_amount": format(monto, "f"), "side": "buy"}
        try:
            q = self._pedir("POST", "/v1/crypto-exchange/quotations", cuerpo)
        except ErrorExchange as e:
            m = re.search(r"multiple of ([0-9.]+)", str(e))
            if not m:
                raise
            paso = Decimal(m.group(1))
            monto = (monto / paso).to_integral_value(rounding=ROUND_DOWN) * paso
            cuerpo["from_amount"] = format(monto, "f")
            q = self._pedir("POST", "/v1/crypto-exchange/quotations", cuerpo)
        cot = q.get("quotation") or {}
        tx = self._pedir("POST", "/v1/crypto-exchange/orders", {"quotation_id": cot.get("id"), "side": "buy"}).get("transaction") or {}
        recibido = _f((cot.get("to_amount") or {}).get("amount") if isinstance(cot.get("to_amount"), dict) else cot.get("to_amount"))
        return float(monto), recibido, tx.get("id") or cot.get("id")

    def comprar(self, simbolo, monto_quote):
        gastado, recibido, oid = self._intercambio(self.quote, simbolo.upper(), Decimal(str(round(monto_quote, 2))))
        precio = gastado / recibido if recibido else 0
        return {"cantidad": recibido, "precio": precio, "total": gastado, "id": oid}

    def vender(self, simbolo, cantidad, precio_min=None):
        if precio_min:
            p = (self.precios([simbolo]).get(simbolo.upper()) or {}).get("precio")
            if p and p < precio_min:
                raise ErrorExchange(f"No vendí {simbolo}: vale {p:.6g} y sin pérdida es desde {precio_min:.6g}")
        cant = Decimal(str(cantidad)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        vendido, recibido, oid = self._intercambio(simbolo.upper(), self.quote, cant)
        precio = recibido / vendido if vendido else 0
        return {"cantidad": vendido, "precio": precio, "total": recibido, "id": oid}


class Simulador:
    """Cartera virtual con precios reales. Nunca envía órdenes."""

    def __init__(self, base, cartera):
        self.base = base
        self.id = f"sim:{base.id}"
        self.quote = base.quote
        self.cartera = cartera  # dict mutable {ACTIVO: cantidad}
        self.con_claves = True

    def saldos(self):
        return {k: v for k, v in self.cartera.items() if v > 0}

    def existe(self, simbolo):
        return self.base.existe(simbolo)

    def precios(self, simbolos):
        return self.base.precios(simbolos)

    def velas(self, simbolo, marco="1h", n=100):
        return self.base.velas(simbolo, marco, n)

    def velas_ts(self, simbolo, marco="1h", n=100):
        return self.base.velas_ts(simbolo, marco, n)

    def mercados_top(self, n=20):
        return self.base.mercados_top(n)

    def comprar(self, simbolo, monto_quote):
        precio = self.precios([simbolo]).get(simbolo.upper(), {}).get("precio", 0)
        if precio <= 0:
            raise ErrorExchange(f"Sin precio para {simbolo}")
        if self.cartera.get(self.quote, 0) < monto_quote:
            raise ErrorExchange("Saldo virtual insuficiente")
        comision = monto_quote * 0.001
        cantidad = (monto_quote - comision) / precio
        self.cartera[self.quote] = self.cartera.get(self.quote, 0) - monto_quote
        self.cartera[simbolo.upper()] = self.cartera.get(simbolo.upper(), 0) + cantidad
        return {"cantidad": cantidad, "precio": precio, "total": monto_quote, "id": f"sim-{int(time.time())}"}

    def vender(self, simbolo, cantidad, precio_min=None):
        precio = self.precios([simbolo]).get(simbolo.upper(), {}).get("precio", 0)
        if precio <= 0:
            raise ErrorExchange(f"Sin precio para {simbolo}")
        if precio_min and precio < precio_min:
            raise ErrorExchange(f"No vendí {simbolo}: vale {precio:.6g} y sin pérdida es desde {precio_min:.6g}")
        cantidad = min(cantidad, self.cartera.get(simbolo.upper(), 0))
        total = cantidad * precio * 0.999
        self.cartera[simbolo.upper()] = self.cartera.get(simbolo.upper(), 0) - cantidad
        self.cartera[self.quote] = self.cartera.get(self.quote, 0) + total
        return {"cantidad": cantidad, "precio": precio, "total": total, "id": f"sim-{int(time.time())}"}


def crear_publico(exchange_id, quote):
    """Solo datos públicos (precios, velas). Crypto.com cotiza USDC contra pares en USD."""
    if exchange_id in ("cryptocom_app", "cryptocom") and quote.upper() in ("USD", "USDC"):
        return CcxtExchange("cryptocom", quote, par_quote="USD")
    return CcxtExchange(exchange_id, quote)


def crear_exchange(exchange_id, quote, api_key="", secret="", password=""):
    exchange_id = (exchange_id or "kraken").strip().lower()
    if exchange_id == "cryptocom_app":
        return CryptoComApp(api_key, secret, quote)
    return CcxtExchange(exchange_id, quote, api_key, secret, password)
