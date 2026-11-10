"""Titulares de las últimas 24 h (RSS gratis) para que la IA decida con contexto.

Se leen en el runner (GitHub Actions, sin límite de CPU), no en el Worker gratis.
Si una fuente falla, se sigue con las demás: nunca tumba el ciclo.
"""
import re
import time
import urllib.request
from email.utils import parsedate_to_datetime
from html import unescape

FUENTES = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://news.google.com/rss/search?q=bitcoin+OR+crypto+OR+fed+OR+nasdaq+when:1d&hl=es-419&gl=US&ceid=US:es-419",
]
ITEM = re.compile(r"<item>(.*?)</item>", re.S)
TITULO = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
FECHA = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)


def leer(xml, ahora=None, horas=24):
    """[(ts, titular)] de un RSS, solo de las últimas `horas`."""
    ahora = ahora or time.time()
    out = []
    for item in ITEM.findall(xml):
        t, f = TITULO.search(item), FECHA.search(item)
        if not t:
            continue
        try:
            ts = parsedate_to_datetime(f.group(1).strip()).timestamp() if f else ahora
        except (TypeError, ValueError):
            ts = ahora
        if ahora - ts <= horas * 3600:
            out.append((ts, re.sub(r"\s+", " ", unescape(t.group(1))).strip()[:160]))
    return out


def titulares(maximo=12, bajar=None):
    """Los más nuevos de todas las fuentes, sin repetidos. `bajar(url)` → texto (pruebas)."""
    def pedir(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 kumo-bot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "ignore")
    bajar = bajar or pedir
    todos = []
    for url in FUENTES:
        try:
            todos += leer(bajar(url))
        except Exception:  # una fuente caída no importa
            continue
    vistos, out = set(), []
    for _, t in sorted(todos, reverse=True):
        clave = t.lower()[:60]
        if clave not in vistos:
            vistos.add(clave)
            out.append(t)
        if len(out) >= maximo:
            break
    return out
