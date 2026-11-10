
# Las pruebas no salen a internet a buscar titulares (el ciclo los pide si hay IA).
import noticias as _noticias  # noqa: E402

_noticias.titulares = lambda *a, **k: []
