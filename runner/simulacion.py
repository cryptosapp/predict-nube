"""Predicciones en papel: libro y precios REALES de Hyperliquid, dinero virtual.

No tiene claves ni firma: es imposible que envíe una orden real. Compra y vende contra
el mejor precio del libro en ese momento (solo el primer nivel, para no suponer más
liquidez de la que hay) y, cuando el mercado vence, cobra con el resultado OFICIAL
(`settledOutcome`). Sirve para medir si el agente acierta antes de arriesgar dinero.
"""
import math
import time

COMISION_VENTA = 0.003  # estimada (la misma que usa Kumo para no vender con pérdida); los creadores externos cobran el doble


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class SimPredicciones:
    papel = True
    id = "sim:hyperliquid"
    quote = "USDC"
    con_claves = False

    def __init__(self, info, estado=None, saldo_inicial=100.0):
        """`info(cuerpo)` = POST público a https://api.hyperliquid.xyz/info."""
        self._info_hl = info
        e = estado if isinstance(estado, dict) else {}
        self.usdc = _f(e.get("usdc", saldo_inicial))
        self.inicial = _f(e.get("inicial", saldo_inicial))
        self.predicciones = {k: _f(v) for k, v in (e.get("posiciones") or {}).items() if _f(v) > 0}
        self.entradas = {k: _f(v) for k, v in (e.get("entradas") or {}).items()}
        self.liquidadas = list(e.get("liquidadas") or [])[:60]
        self.nuevo = not e

    def estado(self):
        return {"usdc": round(self.usdc, 4), "inicial": self.inicial, "posiciones": self.predicciones,
                "entradas": {k: round(v, 5) for k, v in self.entradas.items() if k in self.predicciones}, "liquidadas": self.liquidadas[:60]}

    def saldos(self):
        return {"USDC": round(self.usdc, 4)}

    def mejor_precio_prediccion(self, cod, comprar):
        try:
            niveles = self._info_hl({"type": "l2Book", "coin": f"#{int(cod)}"}).get("levels") or [[], []]
            lado = niveles[1] if comprar else niveles[0]
            return {"precio": _f(lado[0]["px"]), "unidades": _f(lado[0]["sz"])} if lado else None
        except Exception:
            return None

    def orden_prediccion(self, cod, comprar, unidades, limite):
        """Como la orden IOC real: se llena ya contra el mejor precio si cumple tu límite."""
        mejor = self.mejor_precio_prediccion(cod, comprar)
        nada = {"cantidad": 0.0, "precio": 0.0, "total": 0.0, "id": None, "mejor": mejor}
        if not mejor or not mejor.get("precio"):
            return nada
        px = mejor["precio"]
        if (comprar and px > limite) or (not comprar and px < limite):
            return nada
        coin = f"+{int(cod)}"
        n = min(int(unidades), int(mejor.get("unidades") or 0))
        if comprar:
            n = min(n, int(self.usdc // px))
            if n < 1:
                return nada
            previo = self.predicciones.get(coin, 0.0)
            self.entradas[coin] = (self.entradas.get(coin, 0.0) * previo + px * n) / (previo + n)
            self.predicciones[coin] = previo + n
            self.usdc -= n * px
            total = n * px
        else:
            n = min(n, int(self.predicciones.get(coin, 0)))
            if n < 1:
                return nada
            total = n * px * (1 - COMISION_VENTA)
            self.predicciones[coin] -= n
            if self.predicciones[coin] < 1:
                self.predicciones.pop(coin, None)
                self.entradas.pop(coin, None)
            self.usdc += total
        return {"cantidad": float(n), "precio": px, "total": round(total, 4), "id": f"papel-{int(time.time() * 1000)}"}

    def liquidar(self, consultar, abiertos, ahora=None, hasta=None):
        """Cobra las posiciones de mercados que ya no están abiertos y Hyperliquid ya
        liquidó. `consultar(mercado)` → settleFraction o None; `abiertos` = ids de
        mercado que siguen en outcomeMeta (esos no se preguntan). Devuelve las operaciones."""
        ahora = ahora or int(time.time() * 1000)
        ops = []
        for coin, cant in list(self.predicciones.items()):
            cod = int(coin.lstrip("+"))
            mercado, lado = divmod(cod, 10)
            if mercado in abiertos:
                continue
            if hasta is not None and time.time() >= hasta:
                break  # sin tiempo en este ciclo: lo cobra en el próximo
            try:
                f = consultar(mercado)
            except Exception:
                continue
            if f is None:
                continue
            paga = _f(f) if lado == 0 else 1 - _f(f)
            cobro = round(cant * paga, 4)
            costo = self.entradas.get(coin)
            self.usdc += cobro
            self.predicciones.pop(coin, None)
            self.entradas.pop(coin, None)
            self.liquidadas.insert(0, {"ts": ahora, "coin": f"#{cod}", "cantidad": cant, "costo": costo, "pago": paga, "cobro": cobro,
                                       "pnl": round(cobro - cant * costo, 4) if costo else None})
            ops.append({"ts": int(ahora / 1000), "simbolo": f"#{cod}", "accion": "LIQUIDACIÓN", "cantidad": cant, "precio": paga, "total": cobro,
                        "motivo": f"Venció: {'acertó' if paga >= 0.5 else 'falló'} (resultado oficial)", "simulada": True, "pnl": None, "prediccion": f"#{cod}"})
        return ops

    def valor(self, precios):
        """USDC + posiciones al precio medio actual (`precios` = {"#N": precio})."""
        return round(self.usdc + sum(c * (precios.get("#" + k.lstrip("+")) or self.entradas.get(k) or 0) for k, c in self.predicciones.items()), 2)


def resultado_oficial(info):
    """consultar(mercado) para aprendizaje.resolver y SimPredicciones.liquidar."""
    def consultar(mercado):
        r = info({"type": "settledOutcome", "outcome": int(mercado)})
        if not r or r.get("settleFraction") is None:
            return None
        f = float(r["settleFraction"])
        return f if not math.isnan(f) else None
    return consultar
