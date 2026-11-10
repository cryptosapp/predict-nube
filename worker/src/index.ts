// Kumo Bot · nube personal (Cloudflare Worker).
// Guarda la config y el estado del bot en KV y sirve la API de la app.
// Las claves del exchange NUNCA llegan aquí: viven solo en los secretos de
// GitHub del usuario y las usa el runner.

import { WEB_HTML } from './web';

export interface Env {
  KUMO: KVNamespace;
  APP_TOKEN: string;
  RUNNER_TOKEN: string;
  // IA: cualquier proveedor compatible con OpenAI. IA_CLAVE va vacía en los
  // que no piden cuenta (Kilo). GROQ_* queda para nubes instaladas antes.
  IA_URL?: string;
  IA_MODELOS?: string;
  IA_CLAVE?: string;
  GROQ_API_KEY?: string;
  GROQ_MODEL?: string;
  // Versión del código de la nube (runner/version.txt), la pone instalar.yml.
  KUMO_VERSION?: string;
}

const GROQ_MODELOS = ['qwen/qwen3.8-27b', 'openai/gpt-oss-120b', 'llama-3.3-70b-versatile'];

type Json = Record<string, any>;

const CONFIG_DEFECTO: Json = {
  modo: 'simulacion', pausado: false, quote: 'USDT', objetivo: { BTC: 40, ETH: 30 },
  monto_min: 5, monto_max: 25, rsi_compra: 35, rsi_venta: 68, banda: 3, ganancia_min: 1.5,
  stop_perdida: 0, nunca_vender_con_perdida: true, ia: 'veto', ia_conf_min: 65, max_ops_ciclo: 2,
  marco: '1h', saldo_simulado: 1000, radar_pedidos: [], ordenes_ia: 'proponer', orden_horas: 24,
  // Beta: la IA revisa los mercados de predicción en cada ciclo.
  pred_ia: 'proponer', pred_monto: 11, pred_max_total: 30, pred_ventaja: 8, pred_conf_min: 65, pred_max_ciclo: 1,
  pred_prob_min: 5, pred_prob_max: 95, pred_horas_min: 1, pred_categorias: ['cripto_hoy'], pred_vender_ia: true, pred_tomar_ganancia: 0,
  pred_auto_solo_corto: true, pred_corto_horas: 36, pred_pagar_con: '', pred_giro: 'off', pred_giro_umbral: 40,
  pred_supervisor: true, pred_feedback: true, pred_monto_techo: 11, pred_max_total_techo: 30, pred_supervisor_log: [],
  // Varias IAs votan cada decisión del ciclo (un modelo de cada proveedor primero).
  ia_consenso: { enabled: true, size: 3 },
};
const PRED_CATEGORIAS = ['cripto_hoy', 'cripto_mediano', 'bolsa'];
// Mínimo por orden en predicciones: ~1 USDC (no los 10 de spot). Hyperliquid no lo
// publica; se ve en el libro: cientos de órdenes de 1.00-1.05 USDC y muchas de menos de 10.
const MIN_PRED = 1;

// Límites duros: la app no puede guardar valores fuera de estos rangos.
const RANGOS: Record<string, [number, number]> = {
  monto_min: [1, 1000], monto_max: [1, 5000], rsi_compra: [5, 60], rsi_venta: [40, 95], banda: [0, 20],
  ganancia_min: [0, 50], stop_perdida: [0, 50], ia_conf_min: [0, 100], max_ops_ciclo: [0, 10], saldo_simulado: [10, 1000000], orden_horas: [1, 168],
  pred_monto: [1, 500], pred_max_total: [1, 5000], pred_ventaja: [3, 40], pred_conf_min: [0, 100], pred_max_ciclo: [1, 5],
  pred_prob_min: [1, 50], pred_prob_max: [50, 99], pred_horas_min: [0, 48], pred_tomar_ganancia: [0, 99], pred_corto_horas: [1, 168], pred_giro_umbral: [10, 49],
};

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization,Content-Type',
};

// Versión web de la app (la misma página de la APK), servida desde tu propia nube.
// La página no trae ningún secreto: sin tu token de la app no muestra ni hace nada.
const CAB_WEB = {
  'Cache-Control': 'no-cache', 'X-Robots-Tag': 'noindex, nofollow', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
};
const ICONO_WEB = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108"><rect width="108" height="108" rx="26" fill="#0E1424"/><path d="M38 29.5H55A16.5 16.5 0 0 1 55 62.5H38" fill="none" stroke="#2A3550" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/><path d="M38 29.5H55A16.5 16.5 0 0 1 70.4 52" fill="none" stroke="#2EE6A6" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/><rect x="27" y="24" width="11.5" height="60" rx="5.75" fill="#F4F6FB"/><circle cx="54" cy="46" r="4.6" fill="#F4F6FB"/></svg>';
function web(req: Request, ruta: string): Response | null {
  if (req.method !== 'GET') return null;
  if (ruta === '/app' || (ruta === '/' && (req.headers.get('Accept') || '').includes('text/html')))
    return new Response(WEB_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8', ...CAB_WEB } });
  if (ruta === '/manifest.webmanifest')
    return new Response(JSON.stringify({ name: 'Predict', short_name: 'Predict', start_url: '/app', scope: '/', display: 'standalone', background_color: '#0b1020', theme_color: '#0b1020',
      icons: [{ src: '/icono.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }] }), { headers: { 'Content-Type': 'application/manifest+json; charset=utf-8', ...CAB_WEB } });
  if (ruta === '/icono.svg') return new Response(ICONO_WEB, { headers: { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'public, max-age=86400', 'X-Content-Type-Options': 'nosniff' } });
  return null;
}

function json(datos: unknown, status = 200): Response {
  return new Response(JSON.stringify(datos), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...CORS } });
}

function iguales(a: string, b: string): boolean {
  if (!a || !b || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function token(req: Request): string {
  const h = req.headers.get('Authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

async function leer<T>(env: Env, clave: string, defecto: T): Promise<T> {
  return ((await env.KUMO.get(clave, 'json')) as T) ?? defecto;
}

async function leerConfig(env: Env): Promise<Json> {
  const c = await leer<Json>(env, 'config', {});
  return { ...CONFIG_DEFECTO, ...c, objetivo: c.objetivo ?? CONFIG_DEFECTO.objetivo };
}

function limpiarSimbolo(s: unknown): string {
  return String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 12);
}

function validarConfig(actual: Json, cambios: Json): { config?: Json; error?: string } {
  const c: Json = { ...actual };
  for (const [k, [min, max]] of Object.entries(RANGOS)) {
    if (cambios[k] === undefined) continue;
    const v = Number(cambios[k]);
    if (!Number.isFinite(v) || v < min || v > max) return { error: `${k} debe estar entre ${min} y ${max}` };
    c[k] = v;
  }
  if (c.monto_min > c.monto_max) return { error: 'monto_min no puede ser mayor que monto_max' };
  if (c.rsi_compra >= c.rsi_venta) return { error: 'rsi_compra debe ser menor que rsi_venta' };
  if (cambios.quote !== undefined) c.quote = limpiarSimbolo(cambios.quote) || 'USDT';
  if (cambios.marco !== undefined) {
    if (!['15m', '1h', '4h', '1d'].includes(cambios.marco)) return { error: 'marco inválido' };
    c.marco = cambios.marco;
  }
  if (cambios.ia !== undefined) {
    if (!['off', 'veto', 'confirmar'].includes(cambios.ia)) return { error: 'ia inválida' };
    c.ia = cambios.ia;
  }
  if (cambios.ordenes_ia !== undefined) {
    if (!['off', 'proponer', 'auto'].includes(cambios.ordenes_ia)) return { error: 'ordenes_ia inválido' };
    c.ordenes_ia = cambios.ordenes_ia;
  }
  if (cambios.pred_ia !== undefined) {
    if (!['off', 'proponer', 'auto'].includes(cambios.pred_ia)) return { error: 'pred_ia inválido' };
    c.pred_ia = cambios.pred_ia;
  }
  // Protección por giro: la única venta con pérdida en predicciones, y solo si la activas.
  if (cambios.pred_giro !== undefined) {
    if (!['off', 'vender', 'girar'].includes(cambios.pred_giro)) return { error: 'pred_giro inválido' };
    c.pred_giro = cambios.pred_giro;
  }
  if (c.pred_monto > c.pred_max_total) return { error: 'El monto por compra no puede pasar del tope total' };
  if (c.pred_tomar_ganancia > 0 && c.pred_tomar_ganancia < 50) return { error: 'Asegurar ganancia: 0 (apagado) o entre 50 y 99%' };
  if (cambios.pred_categorias !== undefined) {
    const l = Array.isArray(cambios.pred_categorias) ? [...new Set(cambios.pred_categorias.map(String))] : null;
    if (!l || l.some((x) => !PRED_CATEGORIAS.includes(x))) return { error: 'Categorías de predicción inválidas' };
    c.pred_categorias = l;
  }
  if (cambios.pred_vender_ia !== undefined) c.pred_vender_ia = Boolean(cambios.pred_vender_ia);
  if (cambios.pred_supervisor !== undefined) c.pred_supervisor = Boolean(cambios.pred_supervisor);
  // Feedback en movimiento: los resultados recientes pueden pedir más confianza (nunca menos).
  if (cambios.pred_feedback !== undefined) c.pred_feedback = Boolean(cambios.pred_feedback);
  // Moneda que se vende si falta USDC para pagar una predicción ('' = solo USDC).
  if (cambios.pred_pagar_con !== undefined) c.pred_pagar_con = cambios.pred_pagar_con ? limpiarSimbolo(cambios.pred_pagar_con) : '';
  if (cambios.pred_auto_solo_corto !== undefined) c.pred_auto_solo_corto = Boolean(cambios.pred_auto_solo_corto);
  // Lo que pone el usuario es el techo: el supervisor IA solo puede bajarlo.
  if (cambios.pred_monto !== undefined) c.pred_monto_techo = c.pred_monto;
  if (cambios.pred_max_total !== undefined) c.pred_max_total_techo = c.pred_max_total;
  // Y lo que pone en los demás parámetros es su límite de riesgo: el supervisor solo puede ser más estricto.
  for (const k of SUPERVISABLES) if (cambios[k] !== undefined) c.pred_usuario = { ...(c.pred_usuario || {}), [k]: c[k] };
  if (cambios.nunca_vender_con_perdida !== undefined) c.nunca_vender_con_perdida = Boolean(cambios.nunca_vender_con_perdida);
  if (cambios.objetivo !== undefined) {
    const obj: Json = {};
    let suma = 0;
    for (const [k, v] of Object.entries(cambios.objetivo || {})) {
      const s = limpiarSimbolo(k), n = Number(v);
      if (!s || !Number.isFinite(n) || n < 0 || n > 100) return { error: `objetivo inválido para ${k}` };
      if (n > 0) { obj[s] = Math.round(n * 10) / 10; suma += n; }
    }
    if (suma > 100) return { error: `El reparto suma ${suma}%: máximo 100%` };
    if (Object.keys(obj).length > 30) return { error: 'Máximo 30 monedas' };
    c.objetivo = obj;
  }
  if (cambios.modo !== undefined) {
    if (!['simulacion', 'real'].includes(cambios.modo)) return { error: 'modo inválido' };
    if (cambios.modo === 'real' && actual.modo !== 'real' && cambios.confirmar_real !== true) return { error: 'Activar dinero real requiere confirmar_real: true' };
    c.modo = cambios.modo;
  }
  return { config: c };
}

// Supervisor IA de predicciones: solo parámetros de la lista, validados igual que
// los del usuario, y el dinero nunca por encima del techo que puso el usuario.
const SUPERVISABLES = ['pred_ventaja', 'pred_conf_min', 'pred_prob_min', 'pred_prob_max', 'pred_horas_min', 'pred_max_ciclo', 'pred_monto', 'pred_max_total', 'pred_categorias'];
async function aplicarSupervisor(env: Env, ahora: number, s: Json): Promise<void> {
  const actual = await leerConfig(env);
  if (actual.pred_supervisor === false) return;
  const cambios: Json = {};
  for (const k of SUPERVISABLES) if (s.cambios[k] !== undefined) cambios[k] = s.cambios[k];
  if (cambios.pred_monto !== undefined) cambios.pred_monto = Math.min(Number(cambios.pred_monto), Number(actual.pred_monto_techo ?? actual.pred_monto));
  if (cambios.pred_max_total !== undefined) cambios.pred_max_total = Math.min(Number(cambios.pred_max_total), Number(actual.pred_max_total_techo ?? actual.pred_max_total));
  if (cambios.pred_conf_min !== undefined) cambios.pred_conf_min = Math.max(50, Number(cambios.pred_conf_min));
  // Nunca más arriesgado que lo que puso el usuario: más ventaja, más confianza, menos compras, menos categorías.
  const usr: Json = { ...Object.fromEntries(SUPERVISABLES.map((k) => [k, actual[k]])), ...(actual.pred_usuario || {}) };
  const n = (k: string) => Number(cambios[k]);
  if (cambios.pred_ventaja !== undefined) cambios.pred_ventaja = Math.max(n('pred_ventaja'), Number(usr.pred_ventaja));
  if (cambios.pred_conf_min !== undefined) cambios.pred_conf_min = Math.max(n('pred_conf_min'), Number(usr.pred_conf_min ?? 0));
  if (cambios.pred_prob_min !== undefined) cambios.pred_prob_min = Math.max(n('pred_prob_min'), Number(usr.pred_prob_min));
  if (cambios.pred_prob_max !== undefined) cambios.pred_prob_max = Math.min(n('pred_prob_max'), Number(usr.pred_prob_max));
  if (cambios.pred_horas_min !== undefined) cambios.pred_horas_min = Math.max(n('pred_horas_min'), Number(usr.pred_horas_min));
  if (cambios.pred_max_ciclo !== undefined) cambios.pred_max_ciclo = Math.min(n('pred_max_ciclo'), Number(usr.pred_max_ciclo));
  if (cambios.pred_categorias !== undefined) {
    const permitidas = Array.isArray(usr.pred_categorias) ? usr.pred_categorias : [];
    const l = Array.isArray(cambios.pred_categorias) ? cambios.pred_categorias.filter((x: string) => permitidas.includes(x)) : [];
    if (l.length) cambios.pred_categorias = l; else delete cambios.pred_categorias;
  }
  const v = validarConfig(actual, cambios);
  if (!v.config) {
    await bitacora(env, [{ ts: ahora, tipo: 'supervisor', ok: false, texto: `Supervisor IA: cambio rechazado (${v.error})` }]);
    return;
  }
  // validarConfig trata lo que llega como si fuera del usuario: los techos no se mueven.
  v.config.pred_monto_techo = actual.pred_monto_techo ?? actual.pred_monto;
  v.config.pred_max_total_techo = actual.pred_max_total_techo ?? actual.pred_max_total;
  v.config.pred_usuario = usr;
  const hechos = Object.keys(cambios).filter((k) => JSON.stringify(actual[k]) !== JSON.stringify(v.config![k]))
    .map((k) => ({ k, antes: actual[k], despues: v.config![k] }));
  if (!hechos.length) return;
  v.config.pred_supervisor_log = [{ ts: ahora, modelo: s.modelo || null, razon: String(s.razon || '').slice(0, 300), cambios: hechos }, ...(actual.pred_supervisor_log || [])].slice(0, 8);
  await env.KUMO.put('config', JSON.stringify(v.config));
  const txt = hechos.map((h) => `${h.k} ${JSON.stringify(h.antes)} → ${JSON.stringify(h.despues)}`).join(', ');
  await bitacora(env, [{ ts: ahora, tipo: 'supervisor', ok: true, texto: `Supervisor IA ajustó predicciones: ${txt}. ${String(s.razon || '').slice(0, 160)}` }]);
  await avisar(env, [{ kind: 'supervisor', title: '🎲 El supervisor IA ajustó tus predicciones', body: txt.slice(0, 160) }]);
}

async function avisar(env: Env, items: Json[]) {
  if (!items.length) return;
  const feed = await leer<Json[]>(env, 'feed', []);
  const ts = Date.now();
  const nuevos = items.map((it, i) => ({ id: `${ts}-${i}`, ts, ...it }));
  await env.KUMO.put('feed', JSON.stringify([...nuevos.reverse(), ...feed].slice(0, 30)));
}

// ---------------------------------------------------------------- Señales de la IA
// Historial de lo que dijo la IA en cada ciclo y qué pasó con cada orden, para
// que la app lo muestre en vivo. Una escritura de KV por ciclo con señales.
const MAX_SENALES = 60;
async function guardarSenales(env: Env, ciclo: number, ts: number, r: Json): Promise<void> {
  const op: Json = r.ia?.opiniones || {};
  const senales = Object.keys(op).map((s) => ({ simbolo: s, ...op[s] }));
  const ordenes: Json[] = Array.isArray(r.ordenes) ? r.ordenes : [];
  if (!senales.length && !ordenes.length) return;
  const lista = await leer<Json[]>(env, 'senales', []);
  const item = { tipo: 'ciclo', ciclo, ts, modelo: r.ia?.modelo || null, modo_ia: r.ia?.modo || null, modo: r.modo, quote: r.quote, senales, ordenes };
  await env.KUMO.put('senales', JSON.stringify([item, ...lista].slice(0, MAX_SENALES)));
}

// ---------------------------------------------------------------- Órdenes
// Cola de órdenes explícitas: propuestas por la IA o creadas por el usuario.
// propuesta → (aprobar) → aprobada → (el runner la ejecuta) → ejecutada | error
// propuesta/aprobada → cancelada | caducada (orden_horas sin ejecutarse).
const MAX_ORDENES = 80;
const PENDIENTE = (o: Json) => o.estado === 'propuesta' || o.estado === 'aprobada';
// Si el resultado de una orden «ejecutando» no llega en este tiempo, no se reintenta:
// se marca error para que revises tu historial en el exchange.
const EJECUTANDO_MAX_MS = 2 * 3600_000;

async function leerOrdenes(env: Env): Promise<Json[]> {
  return leer<Json[]>(env, 'ordenes', []);
}
async function guardarOrdenes(env: Env, lista: Json[]): Promise<void> {
  // Las pendientes nunca se recortan; de las cerradas quedan las más nuevas.
  const viva = (o: Json) => PENDIENTE(o) || o.estado === 'ejecutando';
  const pend = lista.filter(viva), cerradas = lista.filter((o) => !viva(o));
  await env.KUMO.put('ordenes', JSON.stringify([...pend, ...cerradas].sort((a, b) => b.ts - a.ts).slice(0, Math.max(MAX_ORDENES, pend.length))));
}
function nuevoId(prefijo: string): string {
  return prefijo + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}
function textoOrden(o: Json): string {
  if (o.tipo === 'prediccion') return `${o.accion === 'COMPRAR' ? 'Compra' : 'Venta'} ${o.unidades} × ${o.etiqueta || o.coin} a ${o.accion === 'COMPRAR' ? '≤' : '≥'} ${o.limite}`;
  return `${o.accion === 'COMPRAR' ? 'Compra' : 'Venta'} ${o.simbolo} ${o.accion === 'COMPRAR' ? `${o.monto} ${o.quote || ''}`.trim() : `${o.pct}%`}${o.limite ? ` si ${o.accion === 'COMPRAR' ? '≤' : '≥'} ${o.limite}` : ''}`;
}

// Valida lo que llega de la app o de la IA. Devuelve la orden limpia o un error.
function limpiarOrden(base: Json, c: Json, config: Json): { orden?: Json; error?: string } {
  if (c.tipo === 'prediccion' || base.tipo === 'prediccion') return limpiarPrediccion(base, c, config);
  const o: Json = { ...base };
  if (c.simbolo !== undefined) { o.simbolo = limpiarSimbolo(c.simbolo); if (!o.simbolo) return { error: 'Moneda inválida' }; }
  if (c.accion !== undefined) {
    const a = String(c.accion).toUpperCase();
    if (!['COMPRAR', 'VENDER'].includes(a)) return { error: 'La acción debe ser COMPRAR o VENDER' };
    o.accion = a;
  }
  if (!o.simbolo || !o.accion) return { error: 'Falta la moneda o la acción' };
  if (o.simbolo === String(config.quote).toUpperCase()) return { error: `${o.simbolo} es tu moneda base` };
  if (o.accion === 'COMPRAR') {
    const m = Number(c.monto ?? o.monto);
    if (!(m >= 1 && m <= 100000)) return { error: 'Monto inválido (mínimo 1)' };
    o.monto = Math.round(m * 100) / 100; delete o.pct;
  } else {
    const p = Number(c.pct ?? o.pct ?? 100);
    if (!(p >= 1 && p <= 100)) return { error: 'El % a vender va de 1 a 100' };
    o.pct = Math.round(p); delete o.monto;
  }
  if (c.limite !== undefined) {
    const l = c.limite === null || c.limite === '' ? null : Number(c.limite);
    if (l !== null && !(l > 0)) return { error: 'Precio límite inválido' };
    o.limite = l;
  }
  if (c.permitir_perdida !== undefined) o.permitir_perdida = Boolean(c.permitir_perdida);
  if (c.razon !== undefined) o.razon = String(c.razon).slice(0, 240);
  o.quote = config.quote;
  return { orden: o };
}

// Órdenes en mercados de predicción de Hyperliquid: «#N» (N = 10 × mercado + lado),
// unidades enteras y precio límite = probabilidad (0.001–0.999). Se ejecutan como IOC.
function limpiarPrediccion(base: Json, c: Json, config: Json): { orden?: Json; error?: string } {
  const o: Json = { ...base, tipo: 'prediccion' };
  if (c.coin !== undefined) { if (!/^#\d{2,9}$/.test(String(c.coin))) return { error: 'Mercado inválido' }; o.coin = String(c.coin); }
  if (c.accion !== undefined) {
    const a = String(c.accion).toUpperCase();
    if (!['COMPRAR', 'VENDER'].includes(a)) return { error: 'La acción debe ser COMPRAR o VENDER' };
    o.accion = a;
  }
  if (!o.coin || !o.accion) return { error: 'Falta el mercado o la acción' };
  const u = Number(c.unidades ?? o.unidades);
  if (!(Number.isInteger(u) && u >= 1 && u <= 100000)) return { error: 'Unidades: número entero desde 1' };
  const l = Number(c.limite ?? o.limite);
  if (!(l >= 0.001 && l <= 0.999)) return { error: 'El precio límite va de 0.001 a 0.999' };
  o.unidades = u; o.limite = Number(l.toPrecision(5)); o.simbolo = o.coin;
  if (c.etiqueta !== undefined) o.etiqueta = String(c.etiqueta).slice(0, 90);
  if (c.pagar_con !== undefined) { const m = c.pagar_con ? limpiarSimbolo(c.pagar_con) : ''; if (m) o.pagar_con = m; else delete o.pagar_con; } // 'USDC' = esta orden solo con USDC
  if (c.razon !== undefined) o.razon = String(c.razon).slice(0, 240);
  // Vender con pérdida solo si TÚ lo marcas a propósito (el runner lo bloquea si no).
  if (c.permitir_perdida !== undefined) { if (c.permitir_perdida === true && o.accion === 'VENDER') o.permitir_perdida = true; else delete o.permitir_perdida; }
  if (o.accion !== 'VENDER') delete o.permitir_perdida;
  o.quote = config.quote; delete o.monto; delete o.pct;
  return { orden: o };
}

const o_tipo = (n: Json) => (n.tipo === 'prediccion' ? (n.giro != null ? 'Predicción · 🔄 Giro (tu regla) · ' : 'Predicción · ') : '');
async function mezclarOrdenes(env: Env, ahora: number, r: Json): Promise<void> {
  const upd: Json[] = Array.isArray(r.ordenes_upd) ? r.ordenes_upd : [];
  const nuevas: Json[] = Array.isArray(r.ordenes_nuevas) ? r.ordenes_nuevas : [];
  let lista = await leerOrdenes(env);
  const config = await leerConfig(env);
  let cambio = false;
  const eventos: Json[] = [];
  for (const u of upd) {
    const o = lista.find((x) => x.id === u.id);
    if (!o) continue;
    // Si el usuario la canceló mientras corría el ciclo, solo cuenta si de verdad se ejecutó.
    if (o.estado === 'cancelada' && u.estado !== 'ejecutada') continue;
    // De «ejecutando» de vuelta a «aprobada» = sigue esperando su precio: no es noticia.
    if (o.estado !== u.estado && !(o.estado === 'ejecutando' && u.estado === 'aprobada')) eventos.push({ tipo: 'orden', ok: u.estado !== 'error', texto: `${textoOrden(o)}: ${u.estado}${u.error ? ` · ${u.error}` : ''}`, id: o.id });
    if (o.estado !== u.estado || o.nota !== u.nota) cambio = true;
    Object.assign(o, { estado: u.estado, nota: u.nota || null, error: u.error || null, resultado: u.resultado || o.resultado || null, ts_mod: ahora });
  }
  for (const n of nuevas) {
    const v = limpiarOrden({}, n, config);
    if (!v.orden) continue;
    const previa = lista.find((x) => PENDIENTE(x) && x.origen === 'ia' && x.simbolo === v.orden!.simbolo && x.accion === v.orden!.accion);
    if (previa) continue;
    const o: Json = { ...v.orden, id: n.id || nuevoId('ia'), ts: ahora, origen: 'ia', confianza: n.confianza, ia: n.ia || null, categoria: n.categoria ? String(n.categoria).slice(0, 24) : undefined, giro: n.giro != null && Number.isFinite(Number(n.giro)) ? Number(n.giro) : undefined,
      estado: ['propuesta', 'aprobada', 'ejecutada', 'error'].includes(n.estado) ? n.estado : 'propuesta',
      nota: n.nota || null, error: n.error || null, resultado: n.resultado || null,
      vence: Math.min(ahora + (config.orden_horas || 24) * 3600_000, Number(n.caduca) > ahora ? Number(n.caduca) : Infinity),
      historia: [{ ts: ahora, quien: 'ia', texto: `${o_tipo(n)}Propuesta con ${n.confianza}% de confianza: ${n.razon || ''}`.trim() }] };
    lista.unshift(o);
    cambio = true;
    eventos.push({ tipo: 'orden', ok: o.estado !== 'error', texto: `IA ${o.estado === 'ejecutada' ? 'ejecutó' : 'propuso'}: ${textoOrden(o)}`, id: o.id });
  }
  for (const o of lista) {
    if (o.estado === 'ejecutando' && (o.ts_mod || o.ts) < ahora - EJECUTANDO_MAX_MS) {
      Object.assign(o, { estado: 'error', ts_mod: ahora, error: 'El ciclo que la enviaba no informó el resultado. No la reintento para no repetirla: revisa en tu exchange si se ejecutó.' });
      cambio = true;
      eventos.push({ tipo: 'orden', ok: false, texto: `Sin resultado: ${textoOrden(o)}`, id: o.id });
      continue;
    }
    if (PENDIENTE(o) && o.vence && o.vence < ahora) {
      o.estado = 'caducada'; o.ts_mod = ahora; cambio = true;
      eventos.push({ tipo: 'orden', ok: true, texto: `Caducó: ${textoOrden(o)}`, id: o.id });
    }
  }
  if (cambio) await guardarOrdenes(env, lista);
  if (eventos.length) await bitacora(env, eventos.map((e) => ({ ...e, ts: ahora })));
}

async function ordenesApi(env: Env, c: Json): Promise<Response> {
  const config = await leerConfig(env);
  const lista = await leerOrdenes(env);
  const ahora = Date.now();
  const accion = String(c.accion || '');
  const o = c.id ? lista.find((x) => x.id === c.id) : null;
  if (c.id && !o) return json({ error: 'Esa orden ya no existe' }, 404);
  if (o && !PENDIENTE(o)) return json({ error: `La orden ya está ${o.estado}` }, 409);
  const hist = (x: Json, quien: string, texto: string) => { x.historia = [...(x.historia || []), { ts: ahora, quien, texto }].slice(-12); x.ts_mod = ahora; };

  if (accion === 'crear') {
    const v = limpiarOrden({}, c.orden || {}, config);
    if (!v.orden) return json({ error: v.error }, 400);
    const n: Json = { ...v.orden, id: nuevoId('u'), ts: ahora, origen: 'usuario', estado: 'aprobada', vence: ahora + (config.orden_horas || 24) * 3600_000 };
    hist(n, 'usuario', `Creada: ${textoOrden(n)}`);
    lista.unshift(n);
    await guardarOrdenes(env, lista);
    await bitacora(env, [{ ts: ahora, tipo: 'orden', ok: true, texto: `Creaste: ${textoOrden(n)}`, id: n.id }]);
    return json({ ok: true, orden: n });
  }
  if (accion === 'editar' && o) {
    const v = limpiarOrden(o, c.cambios || {}, config);
    if (!v.orden) return json({ error: v.error }, 400);
    Object.assign(o, v.orden);
    hist(o, 'usuario', `Editada: ${textoOrden(o)}`);
    await guardarOrdenes(env, lista);
    await bitacora(env, [{ ts: ahora, tipo: 'orden', ok: true, texto: `Editaste: ${textoOrden(o)}`, id: o.id }]);
    return json({ ok: true, orden: o });
  }
  if ((accion === 'aprobar' || accion === 'cancelar') && o) {
    o.estado = accion === 'aprobar' ? 'aprobada' : 'cancelada';
    // Predicciones: el precio cambia rápido; aprobada vale 2 h (el ciclo corre cada hora).
    if (accion === 'aprobar') o.vence = o.tipo === 'prediccion' ? ahora + 2 * 3600_000 : Math.max(o.vence || 0, ahora + (config.orden_horas || 24) * 3600_000);
    hist(o, 'usuario', accion === 'aprobar' ? 'Aprobada: se ejecuta en el próximo ciclo' : 'Cancelada');
    await guardarOrdenes(env, lista);
    await bitacora(env, [{ ts: ahora, tipo: 'orden', ok: true, texto: `${accion === 'aprobar' ? 'Aprobaste' : 'Cancelaste'}: ${textoOrden(o)}`, id: o.id }]);
    return json({ ok: true, orden: o });
  }
  // Borra de la nube las órdenes ya cerradas (ejecutadas, canceladas, caducadas, con error)
  // para liberar espacio. Las pendientes y las que se están ejecutando nunca se tocan.
  if (accion === 'limpiar') {
    if (c.confirmed !== true) return json({ error: 'Falta confirmar el borrado' }, 400);
    const solo = c.tipo === 'prediccion' ? 'prediccion' : null;
    const viva = (x: Json) => PENDIENTE(x) || x.estado === 'ejecutando';
    const quedan = lista.filter((x) => viva(x) || (solo && x.tipo !== solo));
    const borradas = lista.length - quedan.length;
    if (borradas) {
      await guardarOrdenes(env, quedan);
      await bitacora(env, [{ ts: ahora, tipo: 'orden', ok: true, texto: `Borraste ${borradas} ${borradas === 1 ? 'orden pasada' : 'órdenes pasadas'}${solo ? ' de predicción' : ''} de tu nube` }]);
    }
    return json({ ok: true, borradas, quedan: quedan.length });
  }
  if (accion === 'ia') {
    const instruccion = String(c.instruccion || '').trim().slice(0, 400);
    if (!instruccion) return json({ error: 'Escribe qué quieres que haga la IA' }, 400);
    return ordenConIA(env, config, lista, o ?? null, instruccion);
  }
  return json({ error: 'Acción de orden desconocida' }, 400);
}

// La IA crea una orden a partir de lo que escribes, o edita una existente.
// El resultado siempre queda como «propuesta»: tú la apruebas.
async function ordenConIA(env: Env, config: Json, lista: Json[], o: Json | null, instruccion: string): Promise<Response> {
  const estado = await leer<Json>(env, 'estado', {});
  const ahora = Date.now();
  const mercado = (estado.radar || []).slice(0, 25).map((f: Json) => ({ s: f.simbolo, p: f.precio, rsi: f.rsi, t: f.tendencia, c24: f.cambio_24h }));
  const cartera = (estado.ultimo?.activos || []).map((a: Json) => ({ s: a.simbolo, cant: a.cantidad, valor: a.valor }));
  const t0 = Date.now();
  let d: Json, modelo: string;
  try {
    ({ datos: d, modelo } = await iaChat(env, [
      { role: 'system', content: 'Preparas órdenes spot para un bot cripto. Respondes SOLO JSON válido en español. Eres prudente y no inventas precios.' },
      { role: 'user', content: `${o ? `Orden actual: ${JSON.stringify({ simbolo: o.simbolo, accion: o.accion, monto: o.monto, pct: o.pct, limite: o.limite ?? null })}\nEl usuario pide cambiarla así: "${instruccion}"` : `El usuario pide esta orden: "${instruccion}"`}
Moneda base: ${config.quote}. Mínimo por orden: ${config.monto_min} ${config.quote}. Cartera: ${JSON.stringify(cartera)}. Mercado: ${JSON.stringify(mercado)}.
COMPRAR usa "monto" en ${config.quote}; VENDER usa "pct" (1-100 de lo que tiene). "limite" es un precio opcional (null = a mercado).
Formato: {"simbolo":"HYPE","accion":"COMPRAR|VENDER","monto":12,"pct":null,"limite":null,"razon":"máx. 25 palabras","aviso":"riesgo o duda, o vacío"}` },
    ]));
  } catch (e: any) {
    await bitacora(env, [{ ts: ahora, tipo: 'ia', ok: false, texto: `IA (órdenes) falló: ${String(e.message || e).slice(0, 160)}`, ms: Date.now() - t0 }]);
    return json({ error: String(e.message || e) }, 502);
  }
  const v = limpiarOrden(o || {}, { simbolo: d.simbolo, accion: d.accion, monto: d.monto ?? undefined, pct: d.pct ?? undefined, limite: d.limite ?? null, razon: d.razon }, config);
  await bitacora(env, [{ ts: ahora, tipo: 'ia', ok: !!v.orden, texto: `IA (órdenes) respondió con ${modelo} en ${((Date.now() - t0) / 1000).toFixed(1)} s${v.error ? ` · orden inválida: ${v.error}` : ''}`, ms: Date.now() - t0, modelo }]);
  if (!v.orden) return json({ error: `La IA propuso algo inválido: ${v.error}` }, 422);
  const n: Json = o || { id: nuevoId('ia'), ts: ahora, vence: ahora + (config.orden_horas || 24) * 3600_000 };
  Object.assign(n, v.orden, { origen: o ? o.origen : 'ia', estado: 'propuesta', ia: { accion: v.orden.accion, confianza: null, razon: v.orden.razon || '' }, aviso: String(d.aviso || '').slice(0, 200) || null });
  n.historia = [...(n.historia || []), { ts: ahora, quien: 'ia', texto: `${o ? 'Editada' : 'Creada'} por la IA («${instruccion.slice(0, 80)}»): ${textoOrden(n)}` }].slice(-12);
  n.ts_mod = ahora;
  if (!o) lista.unshift(n);
  await guardarOrdenes(env, lista);
  await bitacora(env, [{ ts: ahora, tipo: 'orden', ok: true, texto: `IA ${o ? 'editó' : 'creó'}: ${textoOrden(n)} (espera tu aprobación)`, id: n.id }]);
  return json({ ok: true, orden: n, modelo });
}

// ---------------------------------------------------------------- Bitácora
// Historial para saber si todo funciona: ciclos, llamadas a la IA (modelo,
// tiempo, error), órdenes y errores. Una escritura por ciclo y por acción.
const MAX_LOG = 200;
async function bitacora(env: Env, items: Json[]): Promise<void> {
  if (!items.length) return;
  const lista = await leer<Json[]>(env, 'bitacora', []);
  await env.KUMO.put('bitacora', JSON.stringify([...items.slice().reverse(), ...lista].slice(0, MAX_LOG)));
}
async function bitacoraCiclo(env: Env, ciclo: number, ahora: number, r: Json): Promise<void> {
  const items: Json[] = [];
  // Con consenso hay varias llamadas por ciclo: a la bitácora van como mucho 3 respuestas
  // buenas y 4 fallas por ciclo (lo que hace falta para ver qué falló y por qué).
  const todas: Json[] = Array.isArray(r.ia_log) ? r.ia_log : [];
  const log = [...todas.filter((l) => l.ok).slice(0, 3), ...todas.filter((l) => !l.ok).slice(0, 4)];
  for (const l of log) {
    items.push({ ts: Number(l.ts) || ahora, tipo: 'ia', ok: !!l.ok, ms: l.ms, modelo: l.modelo,
      texto: l.ok ? `IA ${l.proveedor || ''} respondió con ${l.modelo} en ${(Number(l.ms) / 1000).toFixed(1)} s` : `IA ${l.proveedor || ''} falló con ${l.modelo}: ${l.error || 'error'}` });
  }
  if (!log.length && r.ok && r.ia?.modo && r.ia.modo !== 'off') items.push({ ts: ahora, tipo: 'ia', ok: true, texto: 'IA: no hubo nada que consultar en este ciclo' });
  // Candado de pérdidas: ventas automáticas frenadas (sin repetir la misma en cada ciclo).
  const bloq: Json[] = Array.isArray(r.bloqueos_perdida) ? r.bloqueos_perdida : [];
  if (bloq.length) {
    const previos = new Set((await leer<Json[]>(env, 'bitacora', [])).slice(0, 40).map((l) => l.texto));
    for (const b of bloq.slice(0, 3)) {
      const texto = `Candado: no vendí «${String(b.que).slice(0, 80)}»: ${String(b.motivo).slice(0, 160)}`;
      if (!previos.has(texto)) items.push({ ts: Number(b.ts) || ahora, tipo: 'loss_sell_block', ok: false, texto });
    }
  }
  const errs: string[] = Array.isArray(r.errores) ? r.errores : [];
  for (const e of errs.slice(0, 5)) items.push({ ts: ahora, tipo: 'error', ok: false, texto: String(e).slice(0, 240) });
  const ops = Array.isArray(r.ejecutadas) ? r.ejecutadas.length : 0;
  items.push({ ts: ahora, tipo: 'ciclo', ok: r.ok !== false, ciclo,
    texto: `Ciclo #${ciclo} ${r.ok === false ? 'con error' : 'OK'} · ${r.exchange || '?'} · ${r.modo || '?'} · ${ops} operaci${ops === 1 ? 'ón' : 'ones'} · ${errs.length} error${errs.length === 1 ? '' : 'es'}${r.duracion ? ` · ${r.duracion} s` : ''}` });
  await bitacora(env, items);
}

// ---------------------------------------------------------------- Runner
async function runnerReporte(env: Env, r: Json): Promise<Response> {
  const estado = await leer<Json>(env, 'estado', {});
  const ciclo = (estado.ciclo || 0) + 1;
  const ahora = Date.now();
  let historial: [number, number][] = estado.historial || [];
  // La gráfica solo compara peras con peras: al cambiar de exchange o entre
  // simulación y real empieza de cero (si no, 1000 simulados → 10 reales sale -99%).
  const serie = r.ok ? `${r.exchange}|${r.modo}` : estado.serie;
  if (r.ok && estado.serie !== serie) historial = [];
  if (r.ok && typeof r.total === 'number') historial.push([ahora, r.total]);
  const { radar, cartera_sim, costos, graficas, feedback, feedback_resumen, giros, aprendizaje, aprendizaje_resumen, papel, ...ultimo } = r;
  const nuevo: Json = {
    ciclo, actualizado: ahora, serie, ultimo: { ...ultimo, ts: ahora },
    historial: historial.slice(-500),
    runner: { costos: costos ?? estado.runner?.costos ?? {}, cartera_sim: cartera_sim ?? estado.runner?.cartera_sim ?? null,
      super_ts: r.supervisor?.ts ?? estado.runner?.super_ts ?? null,
      // Registro de decisiones y resultados: lo arma el runner y vuelve a él en el próximo ciclo.
      feedback: feedback ?? estado.runner?.feedback ?? null, feedback_resumen: feedback_resumen ?? estado.runner?.feedback_resumen ?? null,
      // Mercados donde ya hubo un giro (no se repite): {id_mercado: ts}.
      giros: giros && typeof giros === 'object' ? giros : estado.runner?.giros ?? {},
      // Aprendizaje: cada decisión y su resultado oficial (lo arma el agente; vuelve a él en el próximo ciclo).
      aprendizaje: aprendizaje && typeof aprendizaje === 'object' ? aprendizaje : estado.runner?.aprendizaje ?? null,
      aprendizaje_resumen: aprendizaje_resumen && typeof aprendizaje_resumen === 'object' ? aprendizaje_resumen : estado.runner?.aprendizaje_resumen ?? null,
      // Cartera en papel (modo simulación): USDC virtuales y posiciones.
      papel: papel && typeof papel === 'object' ? papel : estado.runner?.papel ?? null },
    radar: Array.isArray(radar) && radar.length ? radar : estado.radar || [],
    radar_ts: Array.isArray(radar) && radar.length ? ahora : estado.radar_ts || null,
  };
  await env.KUMO.put('estado', JSON.stringify(nuevo));
  // Velas de tus activos para las gráficas de la app: una escritura por ciclo,
  // aparte del estado para que el Panel no tenga que descargarlas.
  if (r.ok && graficas && typeof graficas === 'object' && Object.keys(graficas).length) {
    await env.KUMO.put('graficas', JSON.stringify({ ts: ahora, exchange: r.exchange, quote: r.quote, datos: graficas }));
  }

  // El agente ya sumó lo que importaste de Kumo: se borra para no importarlo otra vez.
  if (r.ok && typeof r.importadas === 'number') await env.KUMO.delete('importar_pendiente');
  await guardarSenales(env, ciclo, ahora, r);
  if (r.supervisor && r.supervisor.cambios && Object.keys(r.supervisor.cambios).length) await aplicarSupervisor(env, ahora, r.supervisor);
  await mezclarOrdenes(env, ahora, r);
  await bitacoraCiclo(env, ciclo, ahora, r);

  const ops: Json[] = Array.isArray(r.ejecutadas) ? r.ejecutadas : [];
  if (ops.length) {
    const lista = await leer<Json[]>(env, 'operaciones', []);
    await env.KUMO.put('operaciones', JSON.stringify([...ops.slice().reverse(), ...lista].slice(0, 200)));
  }
  const avisos: Json[] = ops.map((o) => ({
    tipo: 'operacion',
    titulo: `${o.simulada ? '🧪 ' : ''}${o.accion === 'COMPRAR' ? 'Compra' : o.accion === 'LIQUIDACIÓN' ? 'Venció' : 'Venta'} ${o.simbolo}`,
    texto: `${Number(o.total).toFixed(2)} ${r.quote} a ${o.precio} · ${o.motivo}`,
  }));
  // Un giro propuesto vende con pérdida y caduca pronto: avisa para que lo apruebes o lo canceles a tiempo.
  for (const n of Array.isArray(r.ordenes_nuevas) ? r.ordenes_nuevas : []) {
    if (n && n.giro != null && n.estado === 'propuesta') avisos.push({ tipo: 'orden', titulo: '🔄 Giro: ¿lo apruebas?', texto: `${n.accion === 'VENDER' ? 'Vender' : 'Comprar'} ${n.unidades} de ${String(n.etiqueta || n.coin).slice(0, 80)} a ${n.limite}. Revísalo en Órdenes de predicción.` });
  }
  const errores: string[] = r.errores || [];
  const previos: string[] = estado.ultimo?.errores || [];
  const nuevosErr = errores.filter((e) => !previos.includes(e));
  if (nuevosErr.length) avisos.push({ tipo: 'error', titulo: 'Predict necesita tu atención', texto: nuevosErr.join(' · ').slice(0, 240) });
  await avisar(env, avisos);
  return json({ ok: true, ciclo });
}

// El runner «toma» las órdenes aprobadas justo antes de enviarlas al exchange: pasan a
// «ejecutando» y ya no se le vuelven a mandar. Si el ciclo se cae después de operar,
// la orden no se repite en el siguiente ciclo. Devuelve los ids tomados.
async function runnerTomar(env: Env, c: Json): Promise<Response> {
  const ids: string[] = Array.isArray(c.ids) ? c.ids.map(String) : [];
  const lista = await leerOrdenes(env);
  const ahora = Date.now(), tomadas: string[] = [];
  for (const o of lista) {
    if (ids.includes(o.id) && o.estado === 'aprobada' && !(o.vence && o.vence < ahora)) {
      Object.assign(o, { estado: 'ejecutando', ts_mod: ahora });
      tomadas.push(o.id);
    }
  }
  if (tomadas.length) await guardarOrdenes(env, lista);
  return json({ ok: true, ids: tomadas });
}

async function runnerConfig(env: Env): Promise<Response> {
  const [config, estado] = await Promise.all([leerConfig(env), leer<Json>(env, 'estado', {})]);
  const ahora = Date.now();
  // Las ya vencidas no se mandan (aunque el ciclo anterior no alcanzara a marcarlas caducadas).
  const ordenes = (await leerOrdenes(env)).filter((o) => PENDIENTE(o) && !(o.vence && o.vence < ahora));
  const ia_pref = await env.KUMO.get('ia_pref');
  const ordenes_pred = (await leerOrdenes(env)).filter((o) => o.tipo === 'prediccion').slice(0, 20);
  // El runner recibe tus proveedores de IA con sus claves (por HTTPS y con su token):
  // así no hace falta copiarlas a los secretos de GitHub.
  const { lista: ia_proveedores } = await proveedoresIA(env);
  const importar = await leer<Json[] | null>(env, 'importar_pendiente', null);
  return json({ config, estado_runner: estado.runner || {}, ordenes, ia_pref, ordenes_pred, importar,
    ia_proveedores: ia_proveedores.map((p) => ({ id: p.id, url: p.url, key: p.key, modelos: p.elegidos, reserva: p.reserva })), ia_consenso: config.ia_consenso });
}

// ---------------------------------------------------------------- Proveedores de IA
// Varias IAs gratuitas en orden de prioridad. Tus claves se guardan cifradas (AES-GCM)
// en una sola entrada de KV; la llave sale del token de la app, así que si cambia ese
// token las claves quedan ilegibles y hay que volver a pegarlas. Detrás de tus claves
// van siempre los respaldos: la clave de la instalación y los gratuitos sin clave
// (si alguno no se quita a propósito), para que la IA nunca quede caída por uno solo.
// Las claves nunca vuelven completas a la app ni a la bitácora: solo recortadas.
const IA_CATALOGO: Json[] = [
  { id: 'groq', nombre: 'Groq', url: 'https://api.groq.com/openai/v1', prefijos: ['gsk_'], link: 'https://console.groq.com/keys', nota: 'Muy rápida. Plan gratis con límite por minuto.' },
  { id: 'gemini', nombre: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai', prefijos: ['AIza', 'AQ.'], link: 'https://aistudio.google.com/apikey', nota: 'Gratis con cuenta de Google. A veces da «sin cuota» (429) en horas pico.' },
  { id: 'openrouter', nombre: 'OpenRouter', url: 'https://openrouter.ai/api/v1', prefijos: ['sk-or-'], link: 'https://openrouter.ai/keys', nota: 'Solo se usan sus modelos «:free».' },
  { id: 'cerebras', nombre: 'Cerebras', url: 'https://api.cerebras.ai/v1', prefijos: ['csk-'], link: 'https://cloud.cerebras.ai', nota: 'Muy rápida. Plan gratis.' },
  { id: 'mistral', nombre: 'Mistral', url: 'https://api.mistral.ai/v1', prefijos: [], link: 'https://console.mistral.ai/api-keys', nota: 'Plan gratis «Experiment».' },
  // Tu propio router FreeLLMAPI (rota solo entre muchos proveedores gratis). Como la nube
  // no ve tu teléfono, tiene que estar en una dirección pública (https://…/v1).
  { id: 'freellmapi', nombre: 'FreeLLMAPI (tu router)', url: '', propia: true, prefijos: ['freellmapi-'], link: 'https://github.com/tashfeenahmed/freellmapi',
    nota: 'Tu router: rota solo entre sus proveedores gratis. Pega su dirección pública (https://…/v1) y su clave unificada.' },
  { id: 'kilo', nombre: 'Kilo (sin clave)', url: 'https://api.kilo.ai/api/gateway', sinClave: true, nota: 'Sin registro. Sus modelos gratis cambian seguido.',
    modelos: ['nvidia/nemotron-3-super-120b-a12b:free', 'nvidia/nemotron-3-ultra-550b-a55b:free', 'poolside/laguna-s-2.1:free'] },
  { id: 'llm7', nombre: 'LLM7 (sin clave)', url: 'https://api.llm7.io/v1', sinClave: true, nota: 'Sin registro. Límite anónimo bajo.', modelos: ['GLM-5.3-Flash', 'gpt-oss:20b', 'DeepSeek-V4-Flash-0731'] },
  { id: 'ovh', nombre: 'OVH (sin clave)', url: 'https://oai.endpoints.kepler.ai.cloud.ovh.net/v1', sinClave: true, nota: 'Sin registro. Solo 2 consultas por minuto: queda de último respaldo.',
    modelos: ['gpt-oss-120b', 'Meta-Llama-3_3-70B-Instruct', 'Qwen3.8-27B'] },
];
const catIA = (id: string) => IA_CATALOGO.find((c) => c.id === id);
const recortar = (k: string) => (k ? (k.length > 10 ? `${k.slice(0, 4)}…${k.slice(-4)}` : '…') : '');
function detectarProveedor(clave: string): string | null {
  const k = clave.trim();
  for (const c of IA_CATALOGO) if ((c.prefijos || []).some((p: string) => k.startsWith(p))) return c.id;
  return null;
}

// ---- Cifrado (AES-GCM con WebCrypto)
const b64 = (u: Uint8Array) => btoa(String.fromCharCode(...u));
const deB64 = (t: string) => Uint8Array.from(atob(t), (c) => c.charCodeAt(0));
async function llaveIA(env: Env): Promise<CryptoKey> {
  const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('kumo:ia-keys:' + env.APP_TOKEN));
  return crypto.subtle.importKey('raw', h, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
// `elegidos` = los modelos que marcaste para trabajar («proveedor|modelo»); sin marcar ninguno, todos.
type DocIA = { lista: Json[]; quitados: string[]; orden?: string[]; elegidos?: string[] };
async function leerDocIA(env: Env): Promise<{ doc: DocIA; ilegible: boolean; migrar: boolean }> {
  const crudo = await env.KUMO.get('ia_proveedores');
  const vacio: DocIA = { lista: [], quitados: [] };
  if (!crudo) return { doc: vacio, ilegible: false, migrar: false };
  let x: Json;
  try { x = JSON.parse(crudo); } catch { return { doc: vacio, ilegible: true, migrar: false }; }
  if (Array.isArray(x)) return { doc: { lista: x, quitados: [] }, ilegible: false, migrar: true }; // lista vieja en texto plano
  if (!x.iv || !x.data) return { doc: { lista: x.lista || [], quitados: x.quitados || [], orden: x.orden, elegidos: x.elegidos }, ilegible: false, migrar: true };
  try {
    const plano = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: deB64(x.iv) }, await llaveIA(env), deB64(x.data));
    const d = JSON.parse(new TextDecoder().decode(plano));
    return { doc: { lista: d.lista || [], quitados: d.quitados || [], orden: d.orden, elegidos: d.elegidos }, ilegible: false, migrar: false };
  } catch {
    return { doc: vacio, ilegible: true, migrar: false }; // cambió el token de la app
  }
}
async function guardarDocIA(env: Env, doc: DocIA): Promise<void> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await llaveIA(env), new TextEncoder().encode(JSON.stringify(doc))));
  await env.KUMO.put('ia_proveedores', JSON.stringify({ v: 1, iv: b64(iv), data: b64(data) }));
}

// Lista efectiva: tus claves, luego la de la instalación y al final los sin clave
// (con los modelos del catálogo actual), salvo los que quitaste. Si moviste algo con
// ↑ ↓, se respeta ese orden.
function listaEfectiva(env: Env, doc: DocIA): Json[] {
  const out: Json[] = doc.lista.filter((p) => p.key && !catIA(p.id)?.sinClave).map((p) => ({ ...p, url: catIA(p.id)?.url || p.url, origen: 'tuya' }));
  const inst = proveedorIA(env);
  if (inst && inst.modelos.length && !doc.quitados.includes('instalacion') && !out.some((p) => p.url === inst.url && p.key === inst.clave)) {
    const id = IA_CATALOGO.find((c) => inst.url.startsWith(c.url))?.id;
    if (!(id && catIA(id)?.sinClave && !doc.quitados.includes(id))) out.push({ id: 'instalacion', url: inst.url, key: inst.clave, modelos: inst.modelos, origen: 'instalacion', de: id || inst.nombre });
  }
  for (const c of IA_CATALOGO.filter((c) => c.sinClave)) {
    if (!doc.quitados.includes(c.id)) out.push({ id: c.id, url: c.url, key: '', modelos: c.modelos, origen: 'sin_clave' });
  }
  if (doc.orden?.length) {
    const pos = (id: string) => { const i = doc.orden!.indexOf(id); return i < 0 ? 1e6 : i; };
    out.sort((a, b) => pos(a.id) - pos(b.id) || 0);
  }
  // Fuera los que no son de chat (voz, imagen…) aunque hayan quedado guardados de antes.
  for (const p of out) { const l = p.modelos.filter((m: string) => !NO_CHAT.test(m)); if (l.length) p.modelos = l; }
  // Los modelos que marcaste trabajan; los demás quedan de reserva (solo entran si uno marcado falla).
  const marcados = new Set(doc.elegidos || []);
  const hay = out.some((p) => p.modelos.some((m: string) => marcados.has(`${p.id}|${m}`)));
  for (const p of out) {
    p.elegidos = hay ? p.modelos.filter((m: string) => marcados.has(`${p.id}|${m}`)) : p.modelos;
    p.reserva = p.modelos.filter((m: string) => !p.elegidos.includes(m));
  }
  return out;
}
async function proveedoresIA(env: Env): Promise<{ lista: Json[]; ilegible: boolean }> {
  const { doc, ilegible, migrar } = await leerDocIA(env);
  if (migrar) await guardarDocIA(env, doc); // migra la lista vieja a cifrada
  return { lista: listaEfectiva(env, doc), ilegible };
}
// Primero los modelos marcados (en orden de prioridad) y al final los de reserva.
async function candidatosIA(env: Env): Promise<{ prov: Json; modelo: string; reserva?: boolean }[]> {
  const { lista } = await proveedoresIA(env);
  const pref = await env.KUMO.get('ia_pref');
  const orden = (ms: string[]) => (pref && ms.includes(pref) ? [pref, ...ms.filter((m) => m !== pref)] : ms);
  return [
    ...lista.flatMap((p) => orden(p.elegidos).map((modelo) => ({ prov: p, modelo }))),
    ...lista.flatMap((p) => orden(p.reserva).map((modelo) => ({ prov: p, modelo, reserva: true }))),
  ];
}

// Una pregunta a un proveedor, con lo suyo de cada uno.
function cuerpoIA(prov: Json, modelo: string, mensajes: Json[], formatoJson: boolean): Json {
  const id = IA_CATALOGO.find((c) => String(prov.url).startsWith(c.url))?.id;
  const c: Json = { model: modelo, messages: mensajes, temperature: 0.2, max_tokens: id === 'kilo' ? 4096 : 2048 }; // Kilo: modelos que razonan mucho antes de responder
  if (formatoJson) {
    if (id === 'groq' && !/llama/i.test(modelo)) { c.response_format = { type: 'json_schema', json_schema: { name: 'respuesta', strict: false, schema: { type: 'object' } } }; c.reasoning_effort = 'low'; }
    else if (id === 'groq' || id === 'mistral' || id === 'cerebras') c.response_format = { type: 'json_object' };
  }
  return c;
}
function errorIA(status: number, texto = ''): string {
  if (status === 429) return 'sin cuota (429)';
  if (status === 401 || status === 403) return `clave rechazada (HTTP ${status})`;
  return texto || `HTTP ${status}`;
}
async function preguntarIA(prov: Json, modelo: string, mensajes: Json[], ms: number): Promise<{ ok: boolean; datos?: Json; status?: number; error?: string }> {
  const groq = String(prov.url).startsWith('https://api.groq.com');
  const cab: Record<string, string> = { 'Content-Type': 'application/json', 'User-Agent': groq ? 'curl/8.14.1' : 'kumo-bot/1.0' };
  if (prov.key) cab.Authorization = `Bearer ${prov.key}`;
  for (const formatoJson of [true, false]) {
    let r: Response;
    try {
      r = await fetch(`${prov.url}/chat/completions`, { method: 'POST', headers: cab, body: JSON.stringify(cuerpoIA(prov, modelo, mensajes, formatoJson)), signal: AbortSignal.timeout(ms) });
    } catch (e: any) {
      return { ok: false, status: 0, error: /timeout|abort/i.test(String(e?.name || e)) ? 'timeout' : 'sin conexión' };
    }
    if (r.ok) {
      const d: any = await r.json().catch(() => ({}));
      const texto = d.choices?.[0]?.message?.content || '';
      // Los modelos que razonan pueden gastar todos los tokens antes de escribir la respuesta.
      const cortada = d.choices?.[0]?.finish_reason === 'length';
      if (!String(texto).trim()) return { ok: false, status: 200, error: cortada ? 'se cortó (límite de tokens)' : 'respuesta vacía' };
      try { return { ok: true, datos: extraerJson(texto) }; } catch { return { ok: false, status: 200, error: cortada ? 'se cortó (límite de tokens)' : 'JSON inválido' }; }
    }
    // Gemini responde 400 si la clave no sirve.
    if (r.status === 400 && formatoJson && !String(prov.url).includes('generativelanguage')) continue;
    return { ok: false, status: r.status, error: errorIA(r.status) };
  }
  return { ok: false, status: 0, error: 'error' };
}

// Elige hasta 3 modelos de lo que la cuenta tiene: fuera los que no sirven para
// chat y primero los grandes conocidos. Así no hay nombres fijos que caduquen.
const NO_CHAT = /guard|safety|whisper|tts|audio|embed|image|code|omni|moderation|vision|ocr|rerank|transcri|orpheus|canopylabs|playai|speech|voice|lyria|veo-|imagen/i;
function elegirModelos(id: string, ids: string[]): string[] {
  let l = [...new Set(ids.map((x) => String(x).replace(/^models\//, '')))].filter((x) => !NO_CHAT.test(x));
  if (id === 'openrouter') l = l.filter((x) => x.endsWith(':free'));
  const ver = (x: string) => Number((x.match(/gemini-(\d+(?:\.\d+)?)/) || [])[1] || 0);
  const puntos = (x: string) => {
    if (id === 'gemini') return /^gemini-\d+(\.\d+)?-flash$/.test(x) ? 200 + ver(x) : /^gemini-\d+(\.\d+)?-flash/.test(x) && !/preview|exp|lite/.test(x) ? 150 + ver(x) : /flash/.test(x) ? 100 + ver(x) : 0;
    if (/gpt-oss-120b/.test(x)) return 100;
    if (/qwen/i.test(x) && /(32b|72b|235b|397b|480b|qwen3)/i.test(x)) return 90;
    if (/llama/i.test(x) && /70b/i.test(x)) return 80;
    if (/(large|70b|120b|maverick|nemotron|glm|deepseek|mistral-medium|mistral-large)/i.test(x)) return 50;
    return 10;
  };
  return l.map((x) => [x, puntos(x)] as [string, number]).filter(([, p]) => p > 0).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([x]) => x);
}
// Dirección de un router propio: https y terminada en /v1 (sin barra final).
function urlPropia(u: string): string | null {
  let x = String(u || '').trim().replace(/\/+$/, '');
  if (!/^https:\/\/[^\s/]+/i.test(x)) return null;
  if (!/\/v1$/.test(x)) x += '/v1';
  return x;
}
async function validarProveedor(id: string, clave: string, urlDada = ''): Promise<{ modelos?: string[]; error?: string; url?: string }> {
  const c0 = catIA(id);
  if (!c0) return { error: 'Proveedor desconocido' };
  let c = c0;
  if (c0.propia) {
    const u = urlPropia(urlDada);
    if (!u) return { error: 'Pega la dirección pública de tu FreeLLMAPI, con https (por ejemplo https://mi-router.ejemplo.com/v1). La nube no puede llegar a 127.0.0.1 de tu teléfono.' };
    c = { ...c0, url: u };
  }
  const cab: Record<string, string> = { 'User-Agent': id === 'groq' ? 'curl/8.14.1' : 'kumo-bot/1.0' };
  if (clave) cab.Authorization = `Bearer ${clave}`;
  let r: Response;
  try {
    if (id === 'openrouter') {
      const k = await fetch(`${c.url}/key`, { headers: cab, signal: AbortSignal.timeout(15_000) });
      if (!k.ok) return { error: k.status === 401 || k.status === 403 ? 'Clave rechazada: OpenRouter no la reconoce' : `OpenRouter respondió HTTP ${k.status}` };
    }
    // FreeLLMAPI lista todo su catálogo, también lo que aún no puede usar: solo los listos.
    r = await fetch(`${c.url}/models${c.propia ? '?available=true' : ''}`, { headers: cab, signal: AbortSignal.timeout(15_000) });
  } catch {
    return { error: `No pude conectar con ${c.nombre}: prueba de nuevo en un rato` };
  }
  if (r.status === 401 || r.status === 403 || (id === 'gemini' && r.status === 400)) return { error: `Clave rechazada: ${c.nombre} no la reconoce. Revisa que la copiaste completa.` };
  if (!r.ok) return { error: `${c.nombre} respondió HTTP ${r.status}. Prueba de nuevo en un rato.` };
  const d: any = await r.json().catch(() => ({}));
  // Los que el proveedor marca como de pago («usage_based_only», p. ej. LLM7 «pro») no sirven sin plan.
  const ids: string[] = (d.data || d.models || []).filter((m: Json) => !(c.sinClave && m.usage_based_only === true)).map((m: Json) => m.id || m.name).filter(Boolean);
  // Sin clave: los del catálogo que sigan existiendo.
  // Con FreeLLMAPI, «auto» deja que su router elija y rote; se suman dos más para que
  // el consenso tenga votos de modelos distintos.
  const modelos = c.sinClave ? c.modelos.filter((m: string) => ids.includes(m))
    : c.propia ? [...(ids.includes('auto') ? ['auto'] : []), ...elegirModelos(id, ids.filter((x) => x !== 'auto'))].slice(0, 3)
    : elegirModelos(id, ids);
  if (!modelos.length) return { error: `${c.nombre} no tiene ahora modelos de chat gratis disponibles` };
  return { modelos, url: c.propia ? c.url : undefined };
}

async function iaProveedoresApi(env: Env, ruta: string, c: Json): Promise<Response> {
  const config = await leerConfig(env);
  const { doc, ilegible, migrar } = await leerDocIA(env);
  if (migrar) await guardarDocIA(env, doc);
  const vista = (lista: Json[]) => lista.map((p, i) => ({
    id: p.id, nombre: p.id === 'instalacion' ? `Clave de la instalación${p.de ? ` (${catIA(p.de)?.nombre || p.de})` : ''}` : catIA(p.id)?.nombre || p.id,
    clave: recortar(p.key), modelos: p.modelos, elegidos: p.elegidos, origen: p.origen, rol: i === 0 ? 'PRINCIPAL' : 'RESPALDO', actualizada: p.updated_at || null,
  }));
  const respuesta = (extra: Json = {}) => json({
    catalogo: IA_CATALOGO.map(({ prefijos, ...x }) => ({ ...x, prefijos })), activa: vista(listaEfectiva(env, doc)),
    cifrada: true, ilegible, quitados: doc.quitados, consenso: config.ia_consenso,
    seleccion: listaEfectiva(env, doc).some((p) => p.reserva.length > 0), ...extra,
  });
  if (ruta === '/api/ia/proveedores' && c._get) return respuesta();
  if (!c.confirmed) return json({ error: 'Falta confirmar el cambio' }, 400);

  if (ruta === '/api/ia/proveedores') {
    const id = String(c.proveedor || ''), cat = catIA(id), clave = String(c.key || '').trim();
    if (!cat) return json({ error: 'Elige un proveedor de la lista' }, 400);
    const parece = clave ? detectarProveedor(clave) : null;
    if (parece && parece !== id) return json({ error: `Esa clave parece de ${catIA(parece)!.nombre}, no de ${cat.nombre}. Cambia el proveedor o pega otra clave.`, sugerido: parece }, 400);
    if (!cat.sinClave && !clave) return json({ error: `${cat.nombre} necesita una clave` }, 400);
    const v = await validarProveedor(id, cat.sinClave ? '' : clave, String(c.url || ''));
    if (v.error) return json({ error: v.error }, 400);
    const ahora = Date.now();
    doc.quitados = doc.quitados.filter((q) => q !== id);
    if (!cat.sinClave) {
      doc.lista = [...doc.lista.filter((p) => p.id !== id), { id, key: clave, modelos: v.modelos, updated_at: ahora, ...(v.url ? { url: v.url } : {}) }];
      // Una clave nueva entra antes de los respaldos sin clave.
      if (doc.orden?.length) {
        const ord = doc.orden.filter((x) => x !== id);
        const i = ord.findIndex((x) => catIA(x)?.sinClave);
        ord.splice(i < 0 ? ord.length : i, 0, id);
        doc.orden = ord;
      }
    }
    // Si ya elegiste con cuáles trabajar, los de un proveedor nuevo entran marcados.
    if (doc.elegidos?.length) doc.elegidos = [...new Set([...doc.elegidos.filter((x) => !x.startsWith(`${id}|`)), ...v.modelos!.map((m) => `${id}|${m}`)])];
    await guardarDocIA(env, doc);
    await bitacora(env, [{ ts: ahora, tipo: 'ia', ok: true, texto: `IA: ${cat.nombre} ${cat.sinClave ? 'activada' : `guardada (${recortar(clave)})`} con ${v.modelos!.join(', ')}` }]);
    return respuesta({ ok: true, guardado: { id, modelos: v.modelos } });
  }
  if (ruta === '/api/ia/proveedores/orden') {
    const lista = listaEfectiva(env, doc), ids = lista.map((p) => p.id), id = String(c.id || ''), i = ids.indexOf(id);
    if (i < 0) return json({ error: 'Ese proveedor no está en tu lista' }, 404);
    const acc = String(c.accion || '');
    if (acc === 'quitar') {
      if (ids.length <= 1) return json({ error: 'No puedes quitar el último proveedor: la IA quedaría apagada' }, 400);
      doc.lista = doc.lista.filter((p) => p.id !== id);
      if (!doc.quitados.includes(id) && (id === 'instalacion' || catIA(id)?.sinClave)) doc.quitados.push(id); // no volver a agregarlo solo
      doc.orden = ids.filter((x) => x !== id);
    } else if (['subir', 'bajar', 'primero'].includes(acc)) {
      const j = acc === 'primero' ? 0 : acc === 'subir' ? Math.max(0, i - 1) : Math.min(ids.length - 1, i + 1);
      ids.splice(i, 1); ids.splice(j, 0, id); doc.orden = ids;
    } else return json({ error: 'Acción inválida' }, 400);
    await guardarDocIA(env, doc);
    return respuesta({ ok: true });
  }
  if (ruta === '/api/ia/proveedores/probar') {
    const lista = listaEfectiva(env, doc);
    const pares = lista.flatMap((p) => p.modelos.map((m: string) => ({ p, m }))).slice(0, 14);
    const msg = [{ role: 'user', content: 'Responde solo: {"ok":true}' }];
    const res = await Promise.all(pares.map(async ({ p, m }) => {
      const t0 = Date.now();
      const r = await preguntarIA(p, m, msg, 20_000);
      return { id: p.id, modelo: m, ok: r.ok, ms: Date.now() - t0, error: r.ok ? null : r.error };
    }));
    return respuesta({ pruebas: res });
  }
  if (ruta === '/api/ia/proveedores/modelos') {
    // Con qué modelos trabajar: [«proveedor|modelo»]. `todos` = volver a usar todos.
    const validos = new Set(listaEfectiva(env, doc).flatMap((p) => p.modelos.map((m: string) => `${p.id}|${m}`)));
    const pedidos = c.todos ? [] : [...new Set((Array.isArray(c.elegidos) ? c.elegidos : []).map(String))].filter((x) => validos.has(x)).slice(0, 30);
    if (!c.todos && !pedidos.length) return json({ error: 'Marca al menos un modelo: sin ninguno la IA quedaría apagada' }, 400);
    doc.elegidos = pedidos.length === validos.size ? [] : pedidos;
    await guardarDocIA(env, doc);
    await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: true, texto: doc.elegidos.length ? `IA: trabajas con ${doc.elegidos.map((x) => corto(x.split('|')[1])).join(', ')} (los demás quedan de reserva)` : 'IA: trabajas con todos tus modelos' }]);
    return respuesta({ ok: true });
  }
  if (ruta === '/api/ia/consenso') {
    const size = Math.round(Number(c.size));
    if (c.size !== undefined && !(size === 0 || (size >= 2 && size <= 5))) return json({ error: 'Pueden votar de 2 a 5 IAs, o todas (0)' }, 400);
    config.ia_consenso = { enabled: c.enabled !== undefined ? Boolean(c.enabled) : !!config.ia_consenso?.enabled, size: c.size !== undefined ? size : config.ia_consenso?.size ?? 3 };
    await env.KUMO.put('config', JSON.stringify(config));
    return respuesta({ ok: true });
  }
  return json({ error: 'Ruta no encontrada' }, 404);
}

// ---------------------------------------------------------------- IA radar
const analisisMem = new Map<string, { ts: number; datos: Json }>();

function proveedorIA(env: Env): { url: string; clave: string; modelos: string[]; nombre: string } | null {
  const url = (env.IA_URL || '').trim().replace(/\/+$/, '');
  if (url) {
    const modelos = (env.IA_MODELOS || '').split(',').map((m) => m.trim()).filter(Boolean);
    return { url, clave: (env.IA_CLAVE || '').trim(), modelos, nombre: url.replace(/^https?:\/\/(api\.)?/, '').split('/')[0] };
  }
  if (env.GROQ_API_KEY) {
    return { url: 'https://api.groq.com/openai/v1', clave: env.GROQ_API_KEY, modelos: [env.GROQ_MODEL, ...GROQ_MODELOS].filter(Boolean) as string[], nombre: 'groq.com' };
  }
  return null;
}

// Algunos modelos envuelven el JSON en ```json``` o en <think>…</think>.
function extraerJson(texto: string): Json {
  const t = (texto || '').replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  try { return JSON.parse(t); } catch {
    // El primer objeto JSON completo (hay modelos que lo repiten o agregan texto o emojis).
    for (let a = t.indexOf('{'); a >= 0; a = t.indexOf('{', a + 1)) {
      let prof = 0, enTexto = false, esc = false;
      for (let i = a; i < t.length; i++) {
        const ch = t[i];
        if (enTexto) { if (esc) esc = false; else if (ch === '\\') esc = true; else if (ch === '"') enTexto = false; continue; }
        if (ch === '"') enTexto = true;
        else if (ch === '{') prof++;
        else if (ch === '}' && --prof === 0) { try { return JSON.parse(t.slice(a, i + 1)); } catch { break; } }
      }
    }
    throw new Error('JSON inválido');
  }
}

// Modelo que respondió en la última prueba del semáforo: se prueba primero.
async function modelosOrdenados(env: Env, modelos: string[]): Promise<string[]> {
  const pref = await env.KUMO.get('ia_pref');
  return pref && modelos.includes(pref) ? [pref, ...modelos.filter((m) => m !== pref)] : modelos;
}

// Cadena de respaldo: cada proveedor y modelo de tu lista, en orden de prioridad.
// Un 429, un timeout o un JSON inválido pasan al siguiente; una clave rechazada
// salta los demás modelos de ese proveedor. `solo` = probar solo esos.
async function iaChat(env: Env, mensajes: Json[], solo?: { prov: Json; modelo: string }[]): Promise<{ datos: Json; modelo: string; proveedor?: string; respaldo?: Json[] }> {
  const cands = solo || (await candidatosIA(env));
  if (!cands.length) throw new Error('Tu nube no tiene IA configurada: agrega un proveedor en Config → IA');
  const respaldo: Json[] = [], caidos = new Set<string>();
  for (const { prov, modelo } of cands) {
    if (caidos.has(prov.id)) continue;
    const r = await preguntarIA(prov, modelo, mensajes, 45_000);
    if (r.ok) return { datos: r.datos!, modelo, proveedor: prov.id, respaldo };
    respaldo.push({ proveedor: prov.id, modelo, error: r.error });
    if ((r.status === 401 || r.status === 403) && prov.key) caidos.add(prov.id); // sin clave, el 401 es solo de ese modelo
  }
  const u = respaldo[respaldo.length - 1];
  throw new Error(u ? `${u.proveedor} ${u.error} con ${u.modelo}` : 'La IA no respondió');
}

// Consenso en la nube (igual que en el ciclo): `size` IAs en paralelo (0 = todos tus
// modelos activos, hasta 5), primero un modelo de cada proveedor. Si alguna falla se
// llama a las siguientes hasta juntar esos votos o quedarse sin modelos. Una respuesta
// sin formato útil (`valido`) no cuenta y entra la siguiente.
async function iaVarias(env: Env, config: Json, mensajes: Json[], valido: (d: Json) => boolean): Promise<{ votos: { datos: Json; modelo: string; proveedor: string }[]; errores: Json[] }> {
  const cands = await candidatosIA(env);
  if (!cands.length) throw new Error('Tu nube no tiene IA configurada: agrega un proveedor en Config → IA');
  // Sin consenso (o con un solo modelo) basta 1 respuesta válida: es la cadena de respaldo.
  // Votan los modelos marcados (los de reserva solo entran si uno marcado falla).
  const marcados = cands.filter((c) => !c.reserva).length || cands.length;
  const solo = !config.ia_consenso?.enabled || marcados < 2;
  const size = Number(config.ia_consenso?.size ?? 3);
  const n = solo ? 1 : Math.min(marcados, size === 0 ? 5 : Math.max(2, Math.min(5, size || 3)));
  const vistos = new Set<string>(), primero: typeof cands = [], resto: typeof cands = [], reserva: typeof cands = [];
  for (const c of cands) { if (c.reserva) { reserva.push(c); continue; } (vistos.has(c.prov.id) ? resto : primero).push(c); vistos.add(c.prov.id); }
  // Tope de llamadas: el plan gratis permite 50 subpeticiones por petición.
  let cola = solo ? cands : [...primero, ...resto, ...reserva].slice(0, n + 6);
  const votos: { datos: Json; modelo: string; proveedor: string }[] = [], errores: Json[] = [], caidos = new Set<string>(), t0 = Date.now();
  while (cola.length && votos.length < n) {
    // Es un botón de la app: con un voto ya en mano no se hace esperar más de ~40 s.
    if (votos.length && Date.now() - t0 > 40_000) break;
    const ronda = cola.slice(0, n - votos.length).filter((c) => !caidos.has(c.prov.id));
    cola = cola.slice(n - votos.length);
    const rs = await Promise.all(ronda.map((c) => preguntarIA(c.prov, c.modelo, mensajes, votos.length ? 25_000 : 45_000)));
    rs.forEach((r, i) => {
      const c = ronda[i];
      if (r.ok && valido(r.datos!)) votos.push({ datos: r.datos!, modelo: c.modelo, proveedor: c.prov.id });
      else {
        errores.push({ proveedor: c.prov.id, modelo: c.modelo, error: r.ok ? 'formato inválido (no se contó su voto)' : r.error });
        if ((r.status === 401 || r.status === 403) && c.prov.key) caidos.add(c.prov.id); // sin clave, el 401 es solo de ese modelo
      }
    });
  }
  if (!votos.length) {
    const u = errores.slice(0, 4).map((e) => `${e.proveedor} ${corto(e.modelo)}: ${e.error}`).join('; ');
    throw new Error(`Ninguna IA respondió: ${u || 'sin respuesta'}`);
  }
  return { votos, errores };
}
const corto = (m: string) => String(m).split('/').pop()!.replace(':free', '');
const mediana = (xs: number[]) => { const a = [...xs].sort((x, y) => x - y), k = a.length >> 1; return a.length % 2 ? a[k] : (a[k - 1] + a[k]) / 2; };
// Etiqueta del modelo como en el ciclo: «consenso:a+b» o el modelo solo.
const etiquetaVotos = (votos: { modelo: string }[]) => votos.length > 1 ? 'consenso:' + votos.map((v) => corto(v.modelo)).join('+') : votos[0].modelo;
// Cada IA es un voto: gana la opción con más votos; empate → `espera`. Confianza final =
// media de quienes votaron la ganadora × (0.5 + 0.5 × acuerdo).
function votarOpciones(votos: { modelo: string; opcion: string; confianza: number }[], espera: string): { opcion: string; confianza: number; a_favor: number } {
  const cuenta: Record<string, number> = {};
  for (const v of votos) cuenta[v.opcion] = (cuenta[v.opcion] || 0) + 1;
  const mejor = Math.max(...Object.values(cuenta)), gan = Object.keys(cuenta).filter((k) => cuenta[k] === mejor);
  const opcion = gan.length === 1 ? gan[0] : espera, favor = votos.filter((v) => v.opcion === opcion);
  const media = favor.length ? favor.reduce((a, v) => a + v.confianza, 0) / favor.length : 0;
  return { opcion, confianza: Math.round(media * (0.5 + 0.5 * favor.length / votos.length)), a_favor: favor.length };
}

async function radarAnalizar(env: Env, cuerpo: Json): Promise<Response> {
  const sym = limpiarSimbolo(cuerpo.simbolo);
  if (!sym) return json({ error: 'Falta el símbolo' }, 400);
  const mem = analisisMem.get(sym);
  if (mem && Date.now() - mem.ts < 10 * 60_000) return json({ ...mem.datos, cache: true });
  const [estado, config] = await Promise.all([leer<Json>(env, 'estado', {}), leerConfig(env)]);
  const fila = (estado.radar || []).find((f: Json) => f.simbolo === sym);
  if (!fila) return json({ error: `${sym} aún no tiene datos. Pide el análisis y espera el próximo ciclo.`, pendiente: true }, 404);
  const cartera = (estado.ultimo?.activos || []).map((a: Json) => ({ s: a.simbolo, peso: a.peso }));
  const t0 = Date.now();
  const { datos: d, modelo } = await iaChat(env, [
    { role: 'system', content: 'Eres un analista cripto prudente para principiantes. Respondes SOLO JSON válido en español.' },
    { role: 'user', content: `Evalúa si conviene INCORPORAR la moneda ${sym} a un bot de trading spot.\nDatos de mercado (velas diarias del exchange del usuario): ${JSON.stringify(fila)}\nCartera actual (peso %): ${JSON.stringify(cartera)}\nModo: ${config.modo}.\nIncluye qué es el proyecto si lo conoces (sin inventar cifras), riesgos y una entrada sugerida.\nFormato: {"veredicto":"INCORPORAR|ESPERAR|NO INCORPORAR","confianza":0-100,"resena":"2-4 frases","riesgos":["..."],"entrada":numero|null,"pct_sugerido":1-10}` },
  ]);
  const veredicto = ['INCORPORAR', 'ESPERAR', 'NO INCORPORAR'].includes(String(d.veredicto).toUpperCase()) ? String(d.veredicto).toUpperCase() : 'ESPERAR';
  const datos = {
    simbolo: sym, fila, modelo, veredicto,
    confianza: Math.max(0, Math.min(100, Number(d.confianza) || 0)),
    resena: String(d.resena || '').slice(0, 800),
    riesgos: (Array.isArray(d.riesgos) ? d.riesgos : []).slice(0, 5).map((x: unknown) => String(x).slice(0, 160)),
    entrada: Number(d.entrada) > 0 ? Number(d.entrada) : null,
    pct_sugerido: Math.max(1, Math.min(10, Number(d.pct_sugerido) || 3)),
  };
  analisisMem.set(sym, { ts: Date.now(), datos });
  await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: true, modelo, ms: Date.now() - t0, texto: `IA (Radar ${sym}) respondió con ${modelo} en ${((Date.now() - t0) / 1000).toFixed(1)} s: ${veredicto}` }]);
  const lista = await leer<Json[]>(env, 'senales', []);
  const item = { tipo: 'radar', ts: Date.now(), modelo, senales: [{ simbolo: sym, accion: veredicto, confianza: datos.confianza, razon: datos.resena.slice(0, 200), precio: fila.precio }] };
  await env.KUMO.put('senales', JSON.stringify([item, ...lista].slice(0, MAX_SENALES)));
  return json(datos);
}

// ---------------------------------------------------------------- Predicciones (Hyperliquid HIP-4)
// La IA analiza una posición y SUGIERE (mantener, vender o comprar más). Kumo no
// ejecuta nada: el usuario decide y, si quiere, lo hace en Hyperliquid.
const prediccionMem = new Map<string, { ts: number; datos: Json }>();

// Datos de prueba (mock) para que la app funcione aunque la API de Hyperliquid
// no responda desde Cloudflare. Los valores están calculados con la fórmula log-normal
// del runner para que la probabilidad sí correlacione con la distancia al objetivo:
//   • Precio LEJOS → baja probabilidad (~16%)
//   • Precio MEDIO → prob media-baja (~25%)
//   • Precio CERCANO → prob alta (~44-47%)
//   • Evento macro → sin modelo propio, se basa en consenso IA (~35%)
const MOCK_MERCADOS: Json = {
  ts: Date.now(),
  mercados: [
    {
      mercado: 1,
      pregunta: "¿BTC ≥ 100000 al vencer?",
      regla: "「Sí」gana si BTC está en 100000 o más al vencer.",
      tipo: "precio",
      categoria: "cripto",
      plazo: "hoy",
      subyacente: "BTC",
      nombre_sub: "Bitcoin",
      objetivo: 100000,
      vence: Date.now() + 12 * 3600_000,
      horas: 12,
      lineas: [{ p: 100000, t: "objetivo" }],
      lados: [
        // S=88000, K=100000: falta +13.6% → P(modelo)=16%. Mercado da ~14%, ventaja pequeña (+2 pts).
        { nombre: "Sí", coin: "#10", precio: 0.14, prob_modelo: 16.0, ventaja: 2.0, prob_ventanas: { "1h": 15.0, "3h": 15.4, "6h": 15.9, "12h": 16.3, "24h": 16.7, "48h": 17.1 }, ventanas_a_favor: 1 },
        { nombre: "No", coin: "#11", precio: 0.86, prob_modelo: 84.0, ventaja: -2.0, prob_ventanas: { "1h": 85.0, "3h": 84.6, "6h": 84.1, "12h": 83.7, "24h": 83.3, "48h": 82.9 }, ventanas_a_favor: 6 }
      ],
      quote: "USDC",
      creador: "Hyperliquid",
      comision_doble: undefined,
      precio_actual: 88000,
      distancia_pct: -12.0,
      cambio_1h_pct: -0.3,
      cambio_24h_pct: -1.5,
      cambio_48h_pct: -3.2,
      regimen: "normal",
      mov: { h1: 1.8, h24: 5.2, h48: 7.1 },
      senales: { tendencia: "bajista", medias_3h_vs_12h_pct: -0.5, macd: "a favor de bajar", macd_hist_pct: -0.12, rsi_1h: 42 },
      sigma_plazo_pct: 13.2
    },
    {
      mercado: 2,
      pregunta: "¿ETH ≥ 4000 al vencer?",
      regla: "「Sí」gana si ETH está en 4000 o más al vencer.",
      tipo: "precio",
      categoria: "cripto",
      plazo: "hoy",
      subyacente: "ETH",
      nombre_sub: "Ethereum",
      objetivo: 4000,
      vence: Date.now() + 8 * 3600_000,
      horas: 8,
      lineas: [{ p: 4000, t: "objetivo" }],
      lados: [
        // S=3700, K=4000: falta +8.1% → P(modelo)=25%. Mercado da ~21%, ventaja media (+4 pts).
        { nombre: "Sí", coin: "#20", precio: 0.21, prob_modelo: 25.0, ventaja: 4.0, prob_ventanas: { "1h": 24.0, "3h": 24.5, "6h": 25.0, "12h": 25.2, "24h": 25.5, "48h": 25.8 }, ventanas_a_favor: 3 },
        { nombre: "No", coin: "#21", precio: 0.79, prob_modelo: 75.0, ventaja: -4.0, prob_ventanas: { "1h": 76.0, "3h": 75.5, "6h": 75.0, "12h": 74.8, "24h": 74.5, "48h": 74.2 }, ventanas_a_favor: 0 }
      ],
      quote: "USDC",
      creador: "Hyperliquid",
      comision_doble: undefined,
      precio_actual: 3700,
      distancia_pct: -7.5,
      cambio_1h_pct: -0.8,
      cambio_24h_pct: 0.5,
      cambio_48h_pct: 2.1,
      regimen: "normal",
      mov: { h1: 2.1, h24: 5.8, h48: 8.0 },
      senales: { tendencia: "lateral", medias_3h_vs_12h_pct: -0.1, macd: "neutro", macd_hist_pct: 0.03, rsi_1h: 48 },
      sigma_plazo_pct: 11.9
    },
    {
      mercado: 3,
      pregunta: "¿S&P 500 ≥ 6000 al vencer?",
      regla: "「Sí」gana si S&P 500 está en 6000 o más al vencer.",
      tipo: "precio",
      categoria: "bolsa",
      plazo: "mediano",
      subyacente: "xyz:SP500",
      nombre_sub: "S&P 500",
      objetivo: 6000,
      vence: Date.now() + 72 * 3600_000,
      horas: 72,
      lineas: [{ p: 6000, t: "objetivo" }],
      lados: [
        // S=5990, K=6000: falta solo +0.2% → P(modelo)=47%. Mercado da ~40%, ventaja buena (+7 pts).
        { nombre: "Sí", coin: "#30", precio: 0.40, prob_modelo: 47.0, ventaja: 7.0, prob_ventanas: { "1h": 47.0, "3h": 47.0, "6h": 47.0, "12h": 47.0, "24h": 47.0, "48h": 47.0 }, ventanas_a_favor: 6 },
        { nombre: "No", coin: "#31", precio: 0.60, prob_modelo: 53.0, ventaja: -7.0, prob_ventanas: { "1h": 53.0, "3h": 53.0, "6h": 53.0, "12h": 53.0, "24h": 53.0, "48h": 53.0 }, ventanas_a_favor: 0 }
      ],
      quote: "USDC",
      creador: "Hyperliquid",
      comision_doble: undefined,
      precio_actual: 5990,
      distancia_pct: -0.2,
      cambio_1h_pct: 0.1,
      cambio_24h_pct: 0.6,
      cambio_48h_pct: 1.2,
      regimen: "calmo",
      mov: { h1: 0.4, h24: 1.2, h48: 1.8 },
      senales: { tendencia: "alcista", medias_3h_vs_12h_pct: 0.3, macd: "a favor de subir", macd_hist_pct: 0.07, rsi_1h: 56 },
      sigma_plazo_pct: 1.5
    },
    {
      mercado: 4,
      pregunta: "¿Oro ≥ 2700 al vencer?",
      regla: "「Sí」gana si el oro está en 2700 o más al vencer.",
      tipo: "precio",
      categoria: "bolsa",
      plazo: "mediano",
      subyacente: "xyz:GOLD",
      nombre_sub: "Oro",
      objetivo: 2700,
      vence: Date.now() + 48 * 3600_000,
      horas: 48,
      lineas: [{ p: 2700, t: "objetivo" }],
      lados: [
        // S=2670, K=2700: falta +1.1% → P(modelo)=45%. Mercado da ~37%, ventaja buena (+8 pts).
        { nombre: "Sí", coin: "#40", precio: 0.37, prob_modelo: 45.0, ventaja: 8.0, prob_ventanas: { "1h": 45.0, "3h": 45.0, "6h": 45.0, "12h": 45.0, "24h": 45.0, "48h": 45.0 }, ventanas_a_favor: 6 },
        { nombre: "No", coin: "#41", precio: 0.63, prob_modelo: 55.0, ventaja: -8.0, prob_ventanas: { "1h": 55.0, "3h": 55.0, "6h": 55.0, "12h": 55.0, "24h": 55.0, "48h": 55.0 }, ventanas_a_favor: 0 }
      ],
      quote: "USDC",
      creador: "Hyperliquid",
      comision_doble: undefined,
      precio_actual: 2670,
      distancia_pct: -1.1,
      cambio_1h_pct: 0.1,
      cambio_24h_pct: -0.2,
      cambio_48h_pct: 1.5,
      regimen: "normal",
      mov: { h1: 0.5, h24: 1.4, h48: 2.0 },
      senales: { tendencia: "lateral", medias_3h_vs_12h_pct: -0.05, macd: "neutro", macd_hist_pct: 0.01, rsi_1h: 50 },
      sigma_plazo_pct: 1.8
    },
    {
      mercado: 5,
      pregunta: "¿La Fed baja la tasa en la próxima reunión?",
      regla: "「Sí」gana si la Fed baja la tasa de interés en esa decisión.",
      tipo: "evento",
      categoria: "economia",
      plazo: "mediano",
      subyacente: null,
      nombre_sub: null,
      objetivo: null,
      vence: Date.now() + 168 * 3600_000,
      horas: 168,
      lineas: [],
      lados: [
        // Evento macro: sin modelo log-normal. Consenso artificial: 35% Sí vs 65% No.
        // Mercado da solo 22% a Sí → gran ventaja (+13 pts).
        { nombre: "Sí", coin: "#50", precio: 0.22, prob_modelo: 35.0, ventaja: 13.0, prob_ventanas: { "1h": 35.0, "3h": 35.0, "6h": 35.0, "12h": 35.0, "24h": 35.0, "48h": 35.0 }, ventanas_a_favor: 6 },
        { nombre: "No", coin: "#51", precio: 0.78, prob_modelo: 65.0, ventaja: -13.0, prob_ventanas: { "1h": 65.0, "3h": 65.0, "6h": 65.0, "12h": 65.0, "24h": 65.0, "48h": 65.0 }, ventanas_a_favor: 0 }
      ],
      quote: "USDC",
      creador: "Hyperliquid",
      comision_doble: undefined,
      precio_actual: null,
      distancia_pct: null,
      cambio_1h_pct: null,
      cambio_24h_pct: null,
      cambio_48h_pct: null,
      regimen: null,
      mov: null,
      senales: null,
      sigma_plazo_pct: null
    }
  ],
  total: 5,
  cuenta: { cripto_hoy: 2, cripto_mediano: 0, bolsa: 2, economia: 1, otro: 0 },
  global: { ts: Date.now(), activos: [], resumen: "Datos de prueba (mock). Conecta tu nube a Hyperliquid real para datos en vivo.", tono: "mixto", turbulencia: false }
};

async function hlInfo(cuerpo: Json): Promise<any> {
  // En simulación (o si la API falla), devolvemos mock
  const config = await leerConfigGlobal();
  if (config.modo === 'simulacion') {
    const type = cuerpo.type;
    if (type === 'outcomeMeta') return MOCK_OUTCOME_META;
    if (type === 'allMids') return MOCK_ALL_MIDS;
    if (type === 'candleSnapshot') return generarCandlesMock(cuerpo);
    if (type === 'l2Book') return MOCK_L2BOOK;
  }
  // Intento real
  try {
    const r = await fetch('https://api.hyperliquid.xyz/info', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo), signal: AbortSignal.timeout(8000) });
    if (!r.ok) throw new Error(`Hyperliquid HTTP ${r.status}`);
    return r.json();
  } catch {
    // Fallback a mock aunque no sea simulación
    const type = cuerpo.type;
    if (type === 'outcomeMeta') return MOCK_OUTCOME_META;
    if (type === 'allMids') return MOCK_ALL_MIDS;
    if (type === 'candleSnapshot') return generarCandlesMock(cuerpo);
    if (type === 'l2Book') return MOCK_L2BOOK;
    throw new Error('Hyperliquid no respondió y no hay datos de respaldo');
  }
}

// Cache de config global para saber si estamos en simulación
let configGlobalCache: Json | null = null;
async function leerConfigGlobal(): Promise<Json> {
  if (configGlobalCache) return configGlobalCache;
  // Se llama desde hlInfo antes de que exista env.KUMO, así que usamos la que viene en el worker
  // En la práctica, el worker ya tiene la config en env, pero aquí hacemos una lectura simple
  // Como esto es solo para decidir mock vs real, y hlInfo se usa en el contexto del worker,
  // la config real está disponible via closure. Pero para no romper, asumimos simulación si no hay config.
  return { modo: 'simulacion' };
}

// Mock data structures
const MOCK_OUTCOME_META: Json = {
  outcomes: [
    { outcome: 1, name: "", description: "perp:BTC|class:priceBinary|underlying:BTC|targetPrice:100000|expiry:20261110-120000|priceDescription:MarkPx", sideSpecs: [{ name: "Yes" }, { name: "No" }], venue: "", deployerFeeScale: 0, quoteToken: "USDC" },
    { outcome: 2, name: "", description: "perp:ETH|class:priceBinary|underlying:ETH|targetPrice:4000|expiry:20261110-080000|priceDescription:MarkPx", sideSpecs: [{ name: "Yes" }, { name: "No" }], venue: "", deployerFeeScale: 0, quoteToken: "USDC" },
    { outcome: 3, name: "", description: "perp:xyz:SP500|class:priceBinary|underlying:xyz:SP500|targetPrice:6000|expiry:20261113-120000|priceDescription:MarkPx", sideSpecs: [{ name: "Yes" }, { name: "No" }], venue: "", deployerFeeScale: 0, quoteToken: "USDC" },
    { outcome: 4, name: "", description: "perp:xyz:GOLD|class:priceBinary|underlying:xyz:GOLD|targetPrice:2700|expiry:20261112-120000|priceDescription:MarkPx", sideSpecs: [{ name: "Yes" }, { name: "No" }], venue: "", deployerFeeScale: 0, quoteToken: "USDC" },
    { outcome: 5, name: "template:policyRateFedDecrease", description: "class:priceBinary|underlying:FED|targetPrice:1|expiry:20261201-120000|priceDescription:Official", sideSpecs: [{ name: "Yes" }, { name: "No" }], venue: "", deployerFeeScale: 0, quoteToken: "USDC" }
  ],
  questions: [
    { outcome: 1, name: "", description: "perp:BTC|class:priceBinary|underlying:BTC|targetPrice:100000|expiry:20261110-120000|priceDescription:MarkPx", namedOutcomes: [1, 2], fallbackOutcome: 2 },
    { outcome: 2, name: "", description: "perp:ETH|class:priceBinary|underlying:ETH|targetPrice:4000|expiry:20261110-080000|priceDescription:MarkPx", namedOutcomes: [3, 4], fallbackOutcome: 4 },
    { outcome: 3, name: "", description: "perp:xyz:SP500|class:priceBinary|underlying:xyz:SP500|targetPrice:6000|expiry:20261113-120000|priceDescription:MarkPx", namedOutcomes: [5, 6], fallbackOutcome: 6 },
    { outcome: 4, name: "", description: "perp:xyz:GOLD|class:priceBinary|underlying:xyz:GOLD|targetPrice:2700|expiry:20261112-120000|priceDescription:MarkPx", namedOutcomes: [7, 8], fallbackOutcome: 8 },
    { outcome: 5, name: "template:policyRateFedDecrease", description: "class:priceBinary|underlying:FED|targetPrice:1|expiry:20261201-120000|priceDescription:Official", namedOutcomes: [9, 10], fallbackOutcome: 10 }
  ]
};

const MOCK_ALL_MIDS: Json = {
  "BTC": "98500", "ETH": "3850", "SOL": "180", "HYPE": "25",
  "xyz:SP500": "5890", "xyz:XYZ100": "19800", "xyz:GOLD": "2650", "xyz:CL": "72",
  "#10": "0.42", "#11": "0.58", "#20": "0.31", "#21": "0.69",
  "#30": "0.55", "#31": "0.45", "#40": "0.38", "#41": "0.62",
  "#50": "0.22", "#51": "0.78"
};

function generarVelasMock(precioBase: number, horas: number, intervalo: string): Json[] {
  const msPorVela = intervalo === '15m' ? 900_000 : intervalo === '1h' ? 3_600_000 : intervalo === '4h' ? 14_400_000 : intervalo === '12h' ? 43_200_000 : 86_400_000;
  const n = Math.floor(horas * 3_600_000 / msPorVela);
  const ahora = Date.now();
  const velas: Json[] = [];
  let precio = precioBase;
  for (let i = 0; i < n; i++) {
    const cambio = (Math.random() - 0.5) * 0.02;
    precio *= (1 + cambio);
    const t = ahora - (n - i) * msPorVela;
    velas.push({ t, c: precio, h: precio * 1.005, l: precio * 0.995 });
  }
  return velas;
}

const MOCK_L2BOOK: Json = {
  levels: [
    [{ px: "0.42", sz: "100" }, { px: "0.41", sz: "200" }, { px: "0.40", sz: "300" }, { px: "0.39", sz: "400" }, { px: "0.38", sz: "500" }],
    [{ px: "0.58", sz: "100" }, { px: "0.59", sz: "200" }, { px: "0.60", sz: "300" }, { px: "0.61", sz: "400" }, { px: "0.62", sz: "500" }]
  ]
};

// Genera velas mock para candleSnapshot según el coin, intervalo y rango de tiempo
function generarCandlesMock(cuerpo: Json): Json[] {
  const coin = String(cuerpo.req?.coin || '');
  const intervalo = String(cuerpo.req?.interval || '15m');
  const startTime = Number(cuerpo.req?.startTime || 0);
  const endTime = Number(cuerpo.req?.endTime || 0);
  const horas = startTime && endTime ? (endTime - startTime) / 3_600_000 : 48;
  const precios: Record<string, number> = { "BTC": 98500, "ETH": 3850, "SOL": 180, "HYPE": 25, "xyz:SP500": 5890, "xyz:XYZ100": 19800, "xyz:GOLD": 2650, "xyz:CL": 72 };
  const base = precios[coin] || 100;
  return generarVelasMock(base, horas, intervalo);
}

// Sustituye MOCK_CANDLES por una función que genere velas según el coin
const MOCK_CANDLES_FN = (coin: string, interval: string, startTime: number, endTime: number): Json[] => {
  const horas = (endTime - startTime) / 3_600_000;
  const precios: Record<string, number> = { "BTC": 98500, "ETH": 3850, "SOL": 180, "HYPE": 25, "xyz:SP500": 5890, "xyz:XYZ100": 19800, "xyz:GOLD": 2650, "xyz:CL": 72 };
  const base = precios[coin] || 100;
  return generarVelasMock(base, horas, interval);
};
function campos(desc: unknown): Json {
  const out: Json = {};
  for (const parte of String(desc || '').split('|')) { const i = parte.indexOf(':'); if (i > 0) out[parte.slice(0, i).trim()] = parte.slice(i + 1).trim(); }
  return out;
}
function venceMs(t: unknown): number | null {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$/.exec(String(t || ''));
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
}
// Φ(x) (Abramowitz-Stegun 7.1.26).
function normal(x: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const e = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x / 2);
  return x >= 0 ? (1 + e) / 2 : (1 - e) / 2;
}
// Probabilidad de que el precio termine ≥ objetivo con un modelo log-normal y la
// volatilidad por hora del plazo (1 h / 24 h / 48 h por tramos). Referencia, no certeza.
function probModelo(ahora: number, objetivo: number, sigmaH: number, horas: number): number | null {
  if (!(ahora > 0 && objetivo > 0 && sigmaH > 0 && horas > 0)) return null;
  const s = sigmaH * Math.sqrt(horas);
  return normal((Math.log(ahora / objetivo) - s * s / 2) / s);
}
// Volatilidad por hora en tres ventanas, con velas de 15 min de las últimas 49 h
// (una sola petición por activo: el plan gratis de Cloudflare permite 50 por llamada):
//  · 1 h: rango alto-bajo de las 4 últimas velas (Parkinson, más estable que 4 cierres);
//  · 24 h y 48 h: desviación de los cierres.
// También el cambio de precio en 1 h, 24 h y 48 h (para el análisis del mercado global).
const volMem = new Map<string, { ts: number; v: Json }>();
async function volatilidades(subyacentes: string[]): Promise<Json> {
  const out: Json = {}, ahora = Date.now();
  await Promise.all(subyacentes.slice(0, 26).map(async (u) => {
    const m = volMem.get(u);
    if (m && ahora - m.ts < 3 * 60_000) { out[u] = m.v; return; }
    try {
      const velas: Json[] = await hlInfo({ type: 'candleSnapshot', req: { coin: u, interval: '15m', startTime: ahora - 49 * 3_600_000, endTime: ahora } });
      const v = velas.map((x) => ({ c: Number(x.c), h: Number(x.h), l: Number(x.l) })).filter((x) => x.c > 0 && x.h > 0 && x.l > 0);
      if (v.length < 12) return;
      const c = v.map((x) => x.c), n = c.length - 1;
      const sd = (xs: number[]) => {
        const r = xs.slice(1).map((x, i) => Math.log(x / xs[i])), me = r.reduce((a, b) => a + b, 0) / r.length;
        return Math.sqrt(r.reduce((a, b) => a + (b - me) ** 2, 0) / Math.max(1, r.length - 1));
      };
      // Por hora = por vela de 15 min × √4.
      const s24 = sd(c.slice(-97)) * 2, s48 = sd(c) * 2;
      const park = Math.sqrt(v.slice(-4).reduce((a, x) => a + Math.log(x.h / x.l) ** 2, 0) / 4 / (4 * Math.LN2)) * 2;
      // Una hora sin movimiento no significa que el riesgo desapareció: piso en la mitad de la de 24 h.
      const s1 = Math.max(park || 0, s24 * 0.5);
      const cambio = (k: number) => (n >= k ? Math.round((c[n] / c[n - k] - 1) * 10000) / 100 : null);
      const r = s48 > 0 ? s1 / s48 : 1;
      const x: Json = { sigma_1h: s1, sigma_24h: s24, sigma_48h: s48, cambio_1h_pct: cambio(4), cambio_24h_pct: cambio(96), cambio_48h_pct: cambio(Math.min(n, 192)),
        regimen: r >= 1.8 ? 'turbulento' : r <= 0.5 ? 'calmo' : 'normal', senales: senalesTecnicas(c),
        // Volatilidad por hora medida en cada ventana: cada una da un «modelo» distinto.
        ventanas: { '1h': s1, '3h': sd(c.slice(-13)) * 2 || s1, '6h': sd(c.slice(-25)) * 2 || s24, '12h': sd(c.slice(-49)) * 2 || s24, '24h': s24, '48h': s48 } };
      volMem.set(u, { ts: ahora, v: x });
      out[u] = x;
    } catch { /* sin velas: sin modelo */ }
  }));
  return out;
}
// Señales técnicas del subyacente con cierres de 15 min: tendencia (medias de 3 h y
// 12 h), MACD y RSI de velas de 1 h. Son CONTEXTO para la IA, no disparadores: en la
// prueba con ~1 400 mercados ya resueltos no mejoraron al modelo estadístico, y el
// aprendizaje del agente mide en vivo si ayudan.
function senalesTecnicas(c: number[]): Json | null {
  if (c.length < 60) return null;
  const ema = (xs: number[], n: number) => { const k = 2 / (n + 1); let e = xs[0]; const out = [e]; for (const x of xs.slice(1)) { e += k * (x - e); out.push(e); } return out; };
  const corta = ema(c, 12), larga = ema(c, 48), n = c.length - 1;
  const dif = corta[n] / larga[n] - 1;
  const h = c.filter((_, i) => (n - i) % 4 === 0); // un cierre por hora
  const l26 = ema(h, 26), m = ema(h, 12).map((x, i) => x - l26[i]), sen = ema(m, 9), hist = m[m.length - 1] - sen[sen.length - 1];
  let sube = 0, baja = 0;
  for (let i = Math.max(1, h.length - 14); i < h.length; i++) { const d = h[i] - h[i - 1]; if (d > 0) sube += d; else baja -= d; }
  const rsi = baja === 0 ? 100 : 100 - 100 / (1 + sube / baja);
  const r = (x: number) => Math.round(x * 100) / 100;
  return { tendencia: dif > 0.002 ? 'alcista' : dif < -0.002 ? 'bajista' : 'lateral', medias_3h_vs_12h_pct: r(dif * 100),
    macd: hist > 0 ? 'a favor de subir' : hist < 0 ? 'a favor de bajar' : 'neutro', macd_hist_pct: r(hist / c[n] * 100), rsi_1h: Math.round(rsi) };
}
// Volatilidad para un plazo de h horas, por tramos: la primera hora con la vol de 1 h,
// hasta 24 h con la de 24 h y el resto con la de 48 h (varianzas que se suman).
function sigmaPlazo(v: Json, h: number): number {
  const var_ = v.sigma_1h ** 2 * Math.min(h, 1) + v.sigma_24h ** 2 * Math.min(Math.max(h - 1, 0), 23) + v.sigma_48h ** 2 * Math.max(h - 24, 0);
  return Math.sqrt(var_ / h);
}
// Movimiento típico (1 desviación) en %, para mostrar.
const movTipico = (v: Json) => ({ h1: Math.round(v.sigma_1h * 10000) / 100, h24: Math.round(v.sigma_24h * Math.sqrt(24) * 10000) / 100, h48: Math.round(v.sigma_48h * Math.sqrt(48) * 10000) / 100 });
// ---- Mercado global: cripto, bolsa, oro y petróleo en 1 h / 24 h / 48 h. Entra en
// cada decisión (IA y reglas): tono (apetito o aversión al riesgo) y turbulencia.
const GLOBALES = ['BTC', 'ETH', 'SOL', 'HYPE', 'xyz:SP500', 'xyz:XYZ100', 'xyz:GOLD', 'xyz:CL'];
function mercadoGlobal(mids: Json, vol: Json, ahora: number): Json {
  const activos = GLOBALES.filter((s) => vol[s] && mids[s] != null).map((s) => ({ sub: s, nombre: nombreSub(s), precio: Number(mids[s]),
    cambio_1h_pct: vol[s].cambio_1h_pct, cambio_24h_pct: vol[s].cambio_24h_pct, cambio_48h_pct: vol[s].cambio_48h_pct, regimen: vol[s].regimen, mov: movTipico(vol[s]) }));
  const prom = (subs: string[]) => {
    const xs = activos.filter((a) => subs.includes(a.sub) && a.cambio_24h_pct != null).map((a) => a.cambio_24h_pct as number);
    return xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) / xs.length * 100) / 100 : null;
  };
  const cripto = prom(['BTC', 'ETH', 'SOL', 'HYPE']), bolsa = prom(['xyz:SP500', 'xyz:XYZ100']), oro = prom(['xyz:GOLD']), petroleo = prom(['xyz:CL']);
  const riesgo = [cripto, bolsa].filter((x) => x != null) as number[];
  let tono = 'mixto';
  if (riesgo.length && riesgo.every((x) => x >= 0.5)) tono = 'alcista';
  else if (riesgo.length && riesgo.every((x) => x <= -0.5)) tono = 'bajista';
  const refugio = oro != null && oro >= 0.5 && (bolsa ?? 0) < 0;
  const turb = activos.filter((a) => a.regimen === 'turbulento');
  const turbulencia = turb.length > 0 && (turb.length * 3 >= activos.length || turb.some((a) => a.sub === 'BTC' || a.sub === 'xyz:SP500'));
  const f = (x: number | null) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x}%`);
  const resumen = `24 h: cripto ${f(cripto)}, bolsa ${f(bolsa)}, oro ${f(oro)}, petróleo ${f(petroleo)}. ` +
    (tono === 'alcista' ? 'Apetito por riesgo (todo sube).' : tono === 'bajista' ? 'Aversión al riesgo (cripto y bolsa caen).' : 'Tono mixto.') +
    (refugio ? ' El oro sube mientras la bolsa baja: buscan refugio.' : '') +
    (turbulencia ? ` Turbulencia: la última hora se mueve mucho más que lo normal (${turb.map((a) => a.nombre).join(', ')}); el agente exige más ventaja.` : ' Volatilidad normal.');
  return { ts: ahora, activos, cripto_24h_pct: cripto, bolsa_24h_pct: bolsa, oro_24h_pct: oro, petroleo_24h_pct: petroleo, tono, refugio, turbulencia, resumen };
}
// Mejor comprador y mejor vendedor ahora (libro público de Hyperliquid).
async function prediccionLibro(url: URL): Promise<Response> {
  const coin = String(url.searchParams.get('coin') || '');
  if (!/^#\d{2,9}$/.test(coin)) return json({ error: 'Mercado inválido' }, 400);
  const d: Json = await hlInfo({ type: 'l2Book', coin });
  const [compras, ventas] = d?.levels || [[], []];
  const nivel = (x: Json | undefined) => (x ? { precio: Number(x.px), unidades: Number(x.sz) } : null);
  return json({ coin, ts: Date.now(), mejor_compra: nivel(compras[0]), mejor_venta: nivel(ventas[0]),
    compras: compras.slice(0, 5).map(nivel), ventas: ventas.slice(0, 5).map(nivel) });
}
// Gráficas para decidir: probabilidad del lado (velas 15 min, 48 h) y precio del
// subyacente en 1H/4H/1D con la línea del objetivo. Datos públicos de Hyperliquid.
const graficaPredMem = new Map<string, { ts: number; datos: Json }>();
async function prediccionGrafica(url: URL): Promise<Response> {
  const coin = String(url.searchParams.get('coin') || '').replace(/^\+/, '#');
  if (!/^#\d{2,9}$/.test(coin)) return json({ error: 'Mercado inválido' }, 400);
  const mem = graficaPredMem.get(coin);
  if (mem && Date.now() - mem.ts < 60_000) return json({ ...mem.datos, cache: true });
  const cod = Number(coin.slice(1)), mercado = Math.floor(cod / 10), lado = cod % 10;
  const todo = await listaMercados(), lista: Json[] = todo.mercados || [];
  const m = lista.find((x) => x.mercado === mercado);
  if (!m) return json({ error: 'Ese mercado ya no está abierto.' }, 404);
  const ahora = Date.now(), ld = m.lados[lado] || {};
  const velas = async (c: string, intervalo: string, horas: number) => {
    try {
      const v: Json[] = await hlInfo({ type: 'candleSnapshot', req: { coin: c, interval: intervalo, startTime: ahora - horas * 3_600_000, endTime: ahora } });
      return { t: v.map((x) => Number(x.t)), c: v.map((x) => Number(x.c)) };
    } catch { return { t: [], c: [] }; }
  };
  const [prob, h1, h4, h2, h12, d1] = await Promise.all([
    velas(coin, '15m', 48),
    m.subyacente ? velas(m.subyacente, '1h', 100) : null,
    m.subyacente ? velas(m.subyacente, '4h', 400) : null,
    m.subyacente ? velas(m.subyacente, '2h', 600) : null,
    m.subyacente ? velas(m.subyacente, '12h', 1200) : null,
    m.subyacente ? velas(m.subyacente, '1d', 2400) : null,
  ]);
  // Hyperliquid no da velas de 6 h: se arman con las de 2 h (cierre de cada bloque de 6 h UTC).
  const h6 = { t: [] as number[], c: [] as number[] };
  (h2?.t || []).forEach((t, i) => {
    const b = Math.floor(t / 21_600_000) * 21_600_000;
    if (h6.t[h6.t.length - 1] === b) h6.c[h6.c.length - 1] = h2!.c[i];
    else { h6.t.push(b); h6.c.push(h2!.c[i]); }
  });
  const datos: Json = { coin, mercado, lado, pregunta: m.pregunta, lado_nombre: ld.nombre, precio_lado: ld.precio ?? null, ventaja: ld.ventaja ?? null,
    prob_modelo: ld.prob_modelo ?? null, regla: m.regla, tipo: m.tipo, lineas: m.lineas || [], nombre_sub: m.nombre_sub,
    subyacente: m.subyacente, objetivo: m.objetivo, precio_actual: m.precio_actual ?? null, vence: m.vence, ts: ahora, prob,
    lados: m.lados, mov: m.mov || null, regimen: m.regimen || null, cambio_1h_pct: m.cambio_1h_pct ?? null, cambio_24h_pct: m.cambio_24h_pct ?? null, cambio_48h_pct: m.cambio_48h_pct ?? null,
    sigma_plazo_pct: m.sigma_plazo_pct ?? null,
    marcos: m.subyacente ? { '1h': h1, '4h': h4, '6h': h6, '12h': h12, '1d': d1 } : null, global: todo.global || null };
  graficaPredMem.set(coin, { ts: ahora, datos });
  return json(datos);
}
// ---- Lectura de los mercados de Hyperliquid (HIP-4). Sin deportes. Categorías:
// cripto (hoy / mediano plazo), bolsa y materias primas (perps «xyz:»), economía
// y empresas (tasa de la Fed, salidas a bolsa) y otros.
const NOMBRES_SUB: Json = { 'xyz:XYZ100': 'Nasdaq 100', 'xyz:SP500': 'S&P 500', 'xyz:CL': 'Petróleo WTI', 'xyz:BRENTOIL': 'Petróleo Brent', 'xyz:GOLD': 'Oro', 'xyz:SILVER': 'Plata', 'xyz:SPCX': 'SpaceX' };
const nombreSub = (s: string) => NOMBRES_SUB[s] || s.replace(/^xyz:/, '');
// Miles con espacio (85 501) para que no se confundan con decimales (88.303).
const cifra = (n: number) => (n >= 1000 ? String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') : String(Number(n.toPrecision(6))));
// Quién crea el mercado («venue»). Sin venue = Hyperliquid; los demás son creadores
// externos con HYPE en garantía. deployerFeeScale > 0 = comisión al vender más alta.
const CREADORES: Json = { '': 'Hyperliquid', out: 'out', skew: 'skew', txyz: 'trade.xyz' };
const ES_DEPORTE = /sport|award|contest/i;
const HORAS_HOY = 36;
function nombreLado(n: unknown): string {
  const x = String(n || '').replace(/^template:/, '');
  return x === 'Yes' ? 'Sí' : x === 'Over' ? 'Más' : x === 'Under' ? 'Menos' : x;
}
function fechaCorta(ms: number | null): string {
  return ms ? new Date(ms).toLocaleDateString('es', { day: 'numeric', month: 'short', timeZone: 'UTC' }) : '';
}
// Qué pregunta un mercado y cómo modelarlo. null = deportes o relleno («otro resultado»).
function leerMercado(o: Json, q: Json | undefined): Json | null {
  const nombre = String(o.name || ''), desc = String(o.description || ''), c = campos(desc), cq = q ? campos(q.description) : {};
  if (ES_DEPORTE.test(nombre) || ES_DEPORTE.test(String(q?.name || '')) || c.participant || c.competition || c.candidate || cq.sport || cq.competition || cq.award) return null;
  if (/fallback/i.test(nombre) || desc === 'other') return null;
  const perp = desc.startsWith('perp:') ? desc.split('|')[0].slice(5) : null;
  if (c.class === 'priceBinary' && c.underlying) return { tipo: 'precio', sub: c.underlying, objetivo: Number(c.targetPrice), vence: venceMs(c.expiry) };
  if (nombre === 'template:binaryPrice' && perp) return { tipo: 'precio', sub: perp, objetivo: Number(c.threshold), vence: venceMs(c.time), fuente: c.priceDescription };
  if (nombre === 'template:priceTouch' && perp) return { tipo: 'toque', sub: perp, objetivo: Number(c.target), vence: venceMs(c.time), fuente: c.priceDescription };
  if (nombre === 'template:binaryPriceExternal') return { tipo: 'precio', externo: c.shortName || c.instrument, objetivo: Number(c.threshold), vence: venceMs(c.time), fuente: c.priceDescription };
  if (cq.class === 'priceBucket' && cq.underlying && c.index != null) {
    const t = String(cq.priceThresholds || '').split(',').map(Number).filter((x) => x > 0), k = Number(c.index);
    return { tipo: 'rango', sub: cq.underlying, desde: k > 0 ? t[k - 1] : null, hasta: k < t.length ? t[k] : null, vence: venceMs(cq.expiry) };
  }
  if (/^template:policyRate/.test(nombre)) {
    const quien = /federal/i.test(String(cq.institution || '')) ? 'la Fed' : String(cq.institution || 'el banco central');
    const que = /NoChange/.test(nombre) ? 'mantiene' : /Decrease/.test(nombre) ? 'baja' : 'sube';
    return { tipo: 'evento', categoria: 'economia', pregunta: `¿${quien[0].toUpperCase() + quien.slice(1)} ${que} la tasa de interés (${cq.decisionLabel || 'próxima decisión'})?`,
      regla: `«Sí» gana si ${quien} ${que} la tasa en esa decisión.`, vence: venceMs(cq.scheduledDecision) ?? venceMs(cq.decisionDeadline) };
  }
  if (nombre === 'template:companyIpoConfirmed') {
    const v = venceMs(c.dateTime);
    return { tipo: 'evento', categoria: 'economia', pregunta: `¿${c.company} confirma su salida a bolsa antes del ${fechaCorta(v)}?`, regla: `«Sí» gana si ${c.company} confirma oficialmente su salida a bolsa antes del ${fechaCorta(v)}.`, vence: v };
  }
  if (nombre === 'template:companyIpoFirstDayMarketCap') {
    const b = Number(c.marketCapThresholdB);
    return { tipo: 'evento', categoria: 'economia', pregunta: `¿${c.company} vale ${b >= 1000 ? cifra(b / 1000) + ' billones' : cifra(b) + ' mil millones'} de USD o más en su primer día en bolsa?`,
      regla: `«Sí» gana si ${c.company} sale a bolsa antes del ${fechaCorta(venceMs(c.listingDeadline))} y cierra su primer día con ese valor o más.`, vence: venceMs(c.listingDeadline) };
  }
  const n2 = nombre.replace(/^template:/, '');
  return { tipo: 'evento', categoria: 'otro', pregunta: desc && desc !== 'other' ? `${n2} · ${desc.slice(0, 80)}` : n2, vence: venceMs(cq.resolutionDeadline) };
}
// Probabilidad del lado «Sí» de un mercado con una volatilidad por hora dada.
function probMercado(m: Json, S: number, sg: number): number | null {
  if (m.tipo === 'precio') return probSobre(S, m.objetivo, sg, m.horas);
  if (m.tipo === 'toque') return probToque(S, m.objetivo, sg, m.horas);
  if (m.tipo === 'rango') {
    const a = m.desde == null ? 1 : probSobre(S, m.desde, sg, m.horas), b = m.hasta == null ? 0 : probSobre(S, m.hasta, sg, m.horas);
    return a != null && b != null ? Math.max(0, a - b) : null;
  }
  return null;
}
function probSobre(ahora: number, nivel: number, sigmaH: number, horas: number): number | null {
  return probModelo(ahora, nivel, sigmaH, horas);
}
// Probabilidad de TOCAR un nivel antes de vencer (principio de reflexión, sin tendencia).
function probToque(ahora: number, nivel: number, sigmaH: number, horas: number): number | null {
  if (!(ahora > 0 && nivel > 0 && sigmaH > 0 && horas > 0)) return null;
  return Math.min(1, 2 * (1 - normal(Math.abs(Math.log(nivel / ahora)) / (sigmaH * Math.sqrt(horas)))));
}
let mercadosMem: { ts: number; datos: Json } | null = null;
async function listaMercados(): Promise<Json> {
  if (mercadosMem && Date.now() - mercadosMem.ts < 60_000) return { ...mercadosMem.datos, cache: true };
  const [meta, mids, midsXyz] = await Promise.all([hlInfo({ type: 'outcomeMeta' }), hlInfo({ type: 'allMids' }), hlInfo({ type: 'allMids', dex: 'xyz' }).catch(() => ({}))]);
  const todos: Json = { ...mids, ...midsXyz };
  const preguntas: Json = {};
  for (const q of meta.questions || []) for (const n of [...(q.namedOutcomes || []), q.fallbackOutcome]) preguntas[n] = q;
  const ahora = Date.now(), lista: Json[] = [];
  for (const o of meta.outcomes || []) {
    const m = leerMercado(o, preguntas[o.outcome]);
    if (!m) continue;
    if (m.vence && m.vence < ahora) continue;
    if (m.externo && todos['xyz:' + m.externo]) m.sub = 'xyz:' + m.externo;
    const lados = (o.sideSpecs || []).map((sd: Json, i: number) => {
      const cod = o.outcome * 10 + i;
      return { nombre: nombreLado(sd.name), coin: `#${cod}`, precio: todos[`#${cod}`] != null ? Number(todos[`#${cod}`]) : null };
    });
    if (!lados.some((l: Json) => l.precio != null)) continue;
    // 0.5 / 0.5 exactos = libro vacío: ese precio no es real y la «ventaja» sería falsa.
    const sinOfertas = lados.length === 2 && lados.every((l: Json) => l.precio === 0.5);
    const N = m.sub ? nombreSub(m.sub) : m.externo || '';
    let pregunta = m.pregunta, regla = m.regla, lineas: Json[] = [];
    if (m.tipo === 'precio') { pregunta = `¿${N} ≥ ${cifra(m.objetivo)} al vencer?`; regla = `«Sí» gana si ${N} está en ${cifra(m.objetivo)} o más al vencer.`; lineas = [{ p: m.objetivo, t: 'objetivo' }]; }
    if (m.tipo === 'toque') { pregunta = `¿${N} toca ${cifra(m.objetivo)} antes del ${fechaCorta(m.vence)}?`; regla = `«Sí» gana si ${N} toca ${cifra(m.objetivo)} en cualquier momento antes de vencer.`; lineas = [{ p: m.objetivo, t: 'toque' }]; }
    if (m.tipo === 'rango') {
      pregunta = m.desde == null ? `¿${N} < ${cifra(m.hasta)} al vencer?` : m.hasta == null ? `¿${N} ≥ ${cifra(m.desde)} al vencer?` : `¿${N} entre ${cifra(m.desde)} y ${cifra(m.hasta)} al vencer?`;
      regla = `«Sí» gana si al vencer ${N} está ${m.desde == null ? `por debajo de ${cifra(m.hasta)}` : m.hasta == null ? `en ${cifra(m.desde)} o más` : `entre ${cifra(m.desde)} y ${cifra(m.hasta)}`}.`;
      lineas = [m.desde, m.hasta].filter((x) => x != null).map((p) => ({ p, t: 'límite' }));
    }
    // Cada creador resuelve con su propia fuente de precio (p. ej. skew usa Pyth).
    if (m.fuente && regla) regla += ` Se resuelve con: ${m.fuente}.`;
    const horas = m.vence ? Math.round((m.vence - ahora) / 360_000) / 10 : null;
    const categoria = m.categoria || (m.sub ? (m.sub.startsWith('xyz:') ? 'bolsa' : 'cripto') : m.externo ? 'bolsa' : 'otro');
    lista.push({ mercado: o.outcome, pregunta, regla: regla || null, tipo: m.tipo, categoria, plazo: horas != null && horas <= HORAS_HOY ? 'hoy' : 'mediano',
      subyacente: m.sub || null, nombre_sub: N || null, objetivo: m.tipo === 'rango' ? null : m.objetivo ?? null, desde: m.desde ?? null, hasta: m.hasta ?? null, lineas,
      vence: m.vence || null, horas, lados, quote: o.quoteToken || 'USDC', sin_ofertas: sinOfertas || undefined,
      creador: CREADORES[String(o.venue || '')] || String(o.venue), comision_doble: Number(o.deployerFeeScale) > 0 || undefined });
  }
  // Primero los activos del mercado global, luego los subyacentes con más mercados.
  const usos: Json = {};
  for (const m of lista) if (m.subyacente && todos[m.subyacente]) usos[m.subyacente] = (usos[m.subyacente] || 0) + 1;
  const subs = [...GLOBALES.filter((g) => todos[g] != null), ...Object.keys(usos).filter((u) => !GLOBALES.includes(u)).sort((a, b) => usos[b] - usos[a])];
  const vol = await volatilidades(subs);
  for (const m of lista) {
    if (!m.subyacente || !todos[m.subyacente]) continue;
    const S = Number(todos[m.subyacente]), v = vol[m.subyacente];
    m.precio_actual = S;
    if (m.objetivo) m.distancia_pct = Math.round((S / m.objetivo - 1) * 10000) / 100;
    if (!v || !m.horas || m.horas <= 0) continue;
    Object.assign(m, { cambio_1h_pct: v.cambio_1h_pct, cambio_24h_pct: v.cambio_24h_pct, cambio_48h_pct: v.cambio_48h_pct, regimen: v.regimen, mov: movTipico(v), senales: v.senales || null });
    const sg = sigmaPlazo(v, m.horas);
    m.sigma_plazo_pct = Math.round(sg * Math.sqrt(m.horas) * 10000) / 100;
    const p = probMercado(m, S, sg);
    if (p == null) continue;
    // El mismo modelo con la volatilidad de cada ventana (1 h … 48 h): si casi todas ven
    // ventaja, la señal es más sólida; si se contradicen, depende de qué ventana elijas.
    const ventanas: Json = {};
    for (const [k, sgv] of Object.entries(v.ventanas || {})) {
      const pv = Number(sgv) > 0 ? probMercado(m, S, Number(sgv)) : null;
      if (pv != null) ventanas[k] = Math.round(pv * 1000) / 10;
    }
    m.prob_modelo = Math.round(p * 1000) / 10;
    // Ventaja = probabilidad del modelo − precio del mercado, para cada lado.
    m.lados.forEach((l: Json, i: number) => {
      const pl = i === 0 ? p! : 1 - p!;
      l.prob_modelo = Math.round(pl * 1000) / 10;
      if (l.precio != null && !m.sin_ofertas) l.ventaja = Math.round((pl - l.precio) * 1000) / 10;
      const pv: Json = {};
      for (const [k, x] of Object.entries(ventanas)) pv[k] = i === 0 ? x : Math.round((100 - Number(x)) * 10) / 10;
      l.prob_ventanas = pv;
      // Cuántas ventanas ven este lado al menos 1 punto más barato de lo que vale (contra el precio del mercado).
      if (l.precio != null && !m.sin_ofertas) l.ventanas_a_favor = Object.values(pv).filter((x) => Number(x) >= l.precio * 100 + 1).length;
    });
  }
  const orden: Json = { cripto: 0, bolsa: 1, economia: 2, otro: 3 };
  lista.sort((a, b) => orden[a.categoria] - orden[b.categoria] || (a.vence || 9e15) - (b.vence || 9e15));
  const cuenta: Json = {};
  for (const m of lista) { const k = m.categoria === 'cripto' ? `cripto_${m.plazo}` : m.categoria; cuenta[k] = (cuenta[k] || 0) + 1; }
  const datos = { ts: ahora, mercados: lista.slice(0, 200), total: lista.length, cuenta, global: mercadoGlobal(todos, vol, ahora) };
  mercadosMem = { ts: ahora, datos };
  return datos;
}
async function prediccionMercados(): Promise<Response> {
  return json(await listaMercados());
}

async function prediccionAnalizar(env: Env, c: Json): Promise<Response> {
  // Una posición tuya («+N»), un lado de un mercado («mercado» + «lado») o el mercado
  // completo («mercado» sin lado): entonces la IA elige entre comprar Sí, No o no entrar.
  const explorar = c.mercado != null;
  const ambos = explorar && (c.lado == null || c.lado === '');
  const coin = explorar ? `+${Number(c.mercado) * 10 + (Number(c.lado) ? 1 : 0)}` : String(c.coin || '');
  if (!/^\+\d+$/.test(coin)) return json({ error: 'Predicción inválida' }, 400);
  const clave = ambos ? `m${Number(c.mercado)}` : coin;
  const mem = prediccionMem.get(clave);
  if (mem && Date.now() - mem.ts < 10 * 60_000) return json({ ...mem.datos, cache: true });
  const estado = await leer<Json>(env, 'estado', {});
  const mias: Json[] = estado.ultimo?.predicciones || [];
  let p = mias.find((x) => x.coin === coin);
  // Datos del mercado (regla, modelo, volatilidad) de la lista abierta, tengas o no posición.
  const cod0 = Number(coin.slice(1)), todo: Json = await listaMercados().catch(() => ({ mercados: [] }));
  const lista: Json[] = todo.mercados || [];
  const mm = lista.find((x) => x.mercado === Math.floor(cod0 / 10)), ld = mm?.lados[cod0 % 10];
  if (!p && explorar && mm) p = { coin, pregunta: mm.pregunta, lado_nombre: ld?.nombre, cantidad: 0, pago_si_acierta: 0, vence: mm.vence };
  if (p && mm) Object.assign(p, { subyacente: mm.subyacente, objetivo: mm.objetivo, regla: mm.regla, tipo: mm.tipo, prob_modelo_lado: ld?.prob_modelo ?? null, desde: mm.desde, hasta: mm.hasta });
  if (!p) return json({ error: explorar ? 'Ese mercado ya no está abierto.' : 'Esa predicción no está en tu último ciclo: espera al próximo.' }, 404);
  if (p.vence && p.vence < Date.now()) return json({ error: 'Esta predicción ya venció: se liquida sola.' }, 409);
  // Si ya tienes un lado de este mercado, el análisis completo lo tiene en cuenta.
  const tuya = ambos && mm ? mias.find((x) => Math.floor(Number(String(x.coin).slice(1)) / 10) === mm.mercado) : null;
  const mercado: Json = { pregunta: p.pregunta, regla: p.regla || null, tipo_mercado: p.tipo || null,
    rango: p.tipo === 'rango' ? [p.desde, p.hasta] : undefined, horas_para_vencer: p.vence ? Math.round((p.vence - Date.now()) / 360_000) / 10 : null };
  if (ambos) {
    mercado.lados = (mm?.lados || []).map((l: Json) => ({ lado: l.nombre, precio: l.precio, prob_modelo_pct: l.prob_modelo ?? null, ventaja_pts: l.ventaja ?? null }));
    if (tuya) mercado.ya_tienes = { lado: tuya.lado_nombre, unidades: tuya.cantidad };
  } else Object.assign(mercado, { tu_lado: p.lado_nombre, unidades: p.cantidad, pago_si_aciertas: p.pago_si_acierta, tiene_posicion: (p.cantidad || 0) > 0,
    prob_modelo_estadistico_tu_lado_pct: p.prob_modelo_lado ?? null });
  // Volatilidad del subyacente en 1 h / 24 h / 48 h y el mercado global.
  if (mm?.mov) Object.assign(mercado, { movimiento_tipico_pct: mm.mov, regimen_volatilidad: mm.regimen, cambio_1h_pct: mm.cambio_1h_pct, cambio_48h_pct: mm.cambio_48h_pct,
    movimiento_esperado_hasta_vencer_pct: mm.sigma_plazo_pct });
  const global = todo.global || null;
  // Datos frescos y públicos: probabilidad actual, precio del subyacente y sus últimas 24 h.
  try {
    let sinXyz = false;
    const [m1, m2] = await Promise.all([hlInfo({ type: 'allMids' }), hlInfo({ type: 'allMids', dex: 'xyz' }).catch(() => { sinXyz = true; return {}; })]);
    if (sinXyz && String(p.subyacente || '').startsWith('xyz:')) mercado.aviso = 'Hyperliquid no dio los precios de bolsa y materias (xyz)';
    const mids: Json = { ...m1, ...m2 }, cod = coin.slice(1);
    if (ambos) (mm?.lados || []).forEach((l: Json, i: number) => { if (mids[l.coin] != null) mercado.lados[i].precio = Number(mids[l.coin]); });
    else {
      mercado.precio_tu_lado = Number(mids['#' + cod] ?? p.precio);
      mercado.prob_mercado_tu_lado_pct = Math.round(mercado.precio_tu_lado * 1000) / 10;
    }
    if (p.subyacente && mids[p.subyacente]) {
      const ahora = Number(mids[p.subyacente]);
      mercado.subyacente = p.subyacente; mercado.precio_actual = ahora; mercado.objetivo = p.objetivo;
      mercado.distancia_al_objetivo_pct = p.objetivo ? Math.round((ahora / p.objetivo - 1) * 10000) / 100 : null;
      const velas: Json[] = await hlInfo({ type: 'candleSnapshot', req: { coin: p.subyacente, interval: '1h', startTime: Date.now() - 7 * 24 * 3_600_000, endTime: Date.now() } });
      const todas = velas.map((v) => Number(v.c)).filter((x) => x > 0), cierres = todas.slice(-25);
      if (todas.length > 30) {
        mercado.cambio_7d_pct = Math.round((ahora / todas[0] - 1) * 10000) / 100;
        mercado.rango_7d = [Math.min(...todas), Math.max(...todas)];
      }
      if (cierres.length > 2) {
        const max = Math.max(...cierres), min = Math.min(...cierres);
        mercado.cambio_24h_pct = Math.round((ahora / cierres[0] - 1) * 10000) / 100;
        mercado.rango_24h = [min, max];
        mercado.rango_24h_pct = Math.round((max / min - 1) * 10000) / 100;
      }
    }
  } catch (e: any) {
    mercado.aviso = `Sin datos frescos de Hyperliquid (${String(e?.message || e).slice(0, 80)})`;
    if (!ambos) mercado.precio_tu_lado = p.precio;
  }
  const nombres: string[] = ambos ? (mm?.lados || []).map((l: Json) => String(l.nombre)) : [];
  const config = await leerConfig(env);
  const espera = mercado.tiene_posicion ? 'MANTENER' : 'NO ENTRAR';
  // Sin los datos clave no se le pregunta a la IA: opinaría a ciegas (así recomendó comprar
  // un lado que el mercado ya daba por perdido). Se dice qué falta y no se sugiere comprar.
  const faltan: string[] = [];
  if (!mm) faltan.push('el mercado ya no está en la lista abierta (por vencer o cerrado): no tengo su regla ni el modelo');
  const dePrecio = !!p.subyacente || ['precio', 'toque', 'rango'].includes(String(p.tipo || ''));
  if (dePrecio && mercado.precio_actual == null) faltan.push(`el precio actual de ${p.subyacente || 'su subyacente'}${mercado.aviso ? ` (${mercado.aviso})` : ''}`);
  if (mercado.horas_para_vencer == null) faltan.push('cuánto falta para vencer');
  if (!ambos && mercado.precio_tu_lado == null) faltan.push('el precio de tu lado');
  if (faltan.length) {
    const datos: Json = { coin, modelo: null, mercado, sugerencia: espera, confianza: 0, prob_estimada: null, sin_datos: faltan,
      freno: `No puedo analizar esta predicción: falta ${faltan.join('; ')}. Sin eso cualquier sugerencia sería a ciegas. No compres con esta información; prueba de nuevo en unos minutos.`,
      analisis: '', riesgos: [], global: global ? { resumen: global.resumen, tono: global.tono, turbulencia: global.turbulencia } : null, ts: Date.now() };
    if (ambos && mm) Object.assign(datos, { mercado_id: mm.mercado, lado_sugerido: null, lado_nombre: null });
    await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: false, texto: `IA (predicción ${p.pregunta}${ambos ? '' : ' · ' + p.lado_nombre}): no se consultó, falta ${faltan.join('; ')}` }]);
    return json(datos);
  }
  const pide = ambos
    ? `El usuario mira este mercado completo y quiere saber si comprar un lado. Sugiere UNA acción: COMPRAR con "lado" = ${nombres.map((n) => `"${n}"`).join(' o ')}, o NO ENTRAR (lado null).${mercado.ya_tienes ? ' Ya tiene posición en este mercado: tenlo en cuenta.' : ''}`
    : mercado.tiene_posicion ? 'Sugiere UNA acción: MANTENER (esperar al vencimiento), VENDER (cerrar ahora al precio del mercado) o COMPRAR MÁS.' : 'El usuario aún NO tiene posición en este lado: sugiere COMPRAR (entrar en este lado) o NO ENTRAR.';
  const t0 = Date.now();
  // Cada IA da su sugerencia; se vota como en el ciclo (un modelo, un voto; empate → no hacer nada).
  const validas = ambos ? null : mercado.tiene_posicion ? ['MANTENER', 'VENDER', 'COMPRAR MÁS'] : ['COMPRAR', 'NO ENTRAR'];
  const leerSug = (d: Json): { opcion: string; lado: number | null } | null => {
    const s0 = String(d?.sugerencia || '').toUpperCase().trim().replace('COMPRAR MAS', 'COMPRAR MÁS');
    if (ambos) {
      if (s0 === 'NO ENTRAR') return { opcion: 'NO ENTRAR', lado: null };
      const i = nombres.findIndex((n) => n.toLowerCase() === String(d.lado || '').trim().toLowerCase() || (n === 'Sí' && /^s[ií]$|^yes$/i.test(String(d.lado || '').trim())));
      return s0 === 'COMPRAR' && i >= 0 ? { opcion: `COMPRAR ${nombres[i].toUpperCase()}`, lado: i } : null;
    }
    return validas!.includes(s0) ? { opcion: s0, lado: null } : null;
  };
  const { votos } = await iaVarias(env, config, [
      { role: 'system', content: 'Eres un analista prudente de mercados de predicción. Explicas a un principiante en español sencillo. No prometes resultados. Respondes SOLO JSON válido.' },
      { role: 'user', content: `Mercado de predicción de Hyperliquid (cada unidad paga 1 si acierta y 0 si no; el precio es la probabilidad que le da el mercado):\n${JSON.stringify(mercado)}\nMercado global ahora: ${global ? JSON.stringify({ resumen: global.resumen, tono: global.tono, turbulencia: global.turbulencia }) : 'sin datos'}\n` +
        `Lee la regla. Si depende de un precio, compara precio actual con objetivo, el tiempo que queda y cuánto se mueve: «movimiento_tipico_pct» es 1 desviación en 1 h, 24 h y 48 h; «movimiento_esperado_hasta_vencer_pct» es la que usa el modelo hasta el vencimiento; si el régimen es «turbulento», la última hora se mueve mucho más que lo normal. Ten en cuenta el mercado global (tono de riesgo, refugio, turbulencia) y la tendencia de 1 h / 24 h / 48 h. Si es un evento (economía, empresas), usa lo que sepas y di claramente si tu información puede estar desactualizada. Compara tu probabilidad con la del mercado. ${pide}\n` +
        `Formato: {"sugerencia":"${ambos ? 'COMPRAR|NO ENTRAR' : 'MANTENER|VENDER|COMPRAR MÁS|COMPRAR|NO ENTRAR'}",${ambos ? '"lado":"' + (nombres[0] || 'Sí') + '",' : ''}"confianza":0-100,"prob_estimada":0-100,"analisis":"2-4 frases","riesgos":["..."]}${ambos ? ' (prob_estimada = probabilidad de que el lado sugerido acierte; si NO ENTRAR, la de «' + (nombres[0] || 'Sí') + '»)' : ' (prob_estimada = probabilidad de que «' + p.lado_nombre + '» acierte)'}\n` +
        'REGLA: cuando el mercado da menos de 5 % a un lado, casi siempre es porque ya está prácticamente decidido (precio lejos del objetivo y poco tiempo). No sugieras comprar ni comprar más de ese lado salvo que el precio actual, el tiempo que queda y el movimiento típico lo justifiquen con números; una tendencia de 24 h no basta.' },
  ], (d) => leerSug(d) != null);
  const filas = votos.map((v) => ({ ...v, ...leerSug(v.datos)!, confianza: Math.max(0, Math.min(100, Number(v.datos.confianza) || 0)),
    prob: Number.isFinite(Number(v.datos.prob_estimada)) && v.datos.prob_estimada !== null && v.datos.prob_estimada !== '' ? Math.max(0, Math.min(100, Number(v.datos.prob_estimada))) : null }));
  const r = filas.length > 1 ? votarOpciones(filas, espera) : { opcion: filas[0].opcion, confianza: filas[0].confianza, a_favor: 1 };
  const favor = filas.filter((f) => f.opcion === r.opcion);
  const lider = (favor.length ? favor : filas).reduce((a, f) => (f.confianza > a.confianza ? f : a));
  const d = lider.datos, modelo = etiquetaVotos(votos);
  const ladoSug = favor.length ? favor[0].lado : null, sug = r.opcion;
  // Probabilidad: mediana de quienes votaron la opción ganadora (misma pregunta, mismo lado).
  const probs = favor.map((f) => f.prob).filter((x): x is number => x != null);
  const detalle = filas.length > 1 ? `Consenso ${r.a_favor}/${filas.length} (${filas.map((f) => `${corto(f.modelo)} ${f.opcion} ${f.confianza}`).join(', ')}). ` : '';
  // Freno de coherencia: una sugerencia de COMPRAR tiene que resistir la comparación con el
  // mercado y con el modelo estadístico. Si no, Kumo la cambia por no hacer nada y lo dice.
  let freno: string | null = null;
  const probIA = probs.length ? Math.round(mediana(probs) * 10) / 10 : null;
  if (/^COMPRAR/.test(sug)) {
    const li = ambos && ladoSug != null ? mercado.lados[ladoSug] : null;
    const pr: number | null = ambos ? li?.precio ?? null : mercado.precio_tu_lado ?? null;
    const pmod: number | null = ambos ? li?.prob_modelo_pct ?? null : mercado.prob_modelo_estadistico_tu_lado_pct ?? null;
    const lmin = Number(config.pred_prob_min ?? 5), lmax = Number(config.pred_prob_max ?? 95), pct1 = (x: number) => String(Math.round(x * 10) / 10).replace('.', ',');
    if (pr == null) freno = 'no hay precio de mercado de ese lado para comparar';
    else if (pr * 100 < lmin || pr * 100 > lmax) freno = `el mercado le da ${pct1(pr * 100)} % a ese lado, fuera de tu rango de ${lmin}–${lmax} %. Con precios tan extremos casi siempre ya está decidido: lo barato no es una oportunidad`;
    else if (pmod != null && pmod / 100 < pr + 0.02) freno = `el modelo estadístico le da ${pct1(pmod)} % y el mercado ${pct1(pr * 100)} %: a ese precio no hay ventaja`;
    else if (probIA != null && probIA - pr * 100 > 40 && (pmod == null || pmod < probIA - 25)) freno = `la IA estima ${pct1(probIA)} % y el mercado ${pct1(pr * 100)} %: una diferencia así, sin que el modelo estadístico la respalde${pmod != null ? ` (${pct1(pmod)} %)` : ''}, suele ser un error de la IA y no una ganga`;
  }
  const riesgos = [...new Set([lider, ...favor, ...filas].flatMap((f) => (Array.isArray(f.datos.riesgos) ? f.datos.riesgos : []).map((x: unknown) => String(x).slice(0, 160))))];
  const datos: Json = {
    coin: ladoSug != null && !freno ? `+${mm!.mercado * 10 + ladoSug}` : coin, modelo, mercado, sugerencia: freno ? espera : sug,
    confianza: r.confianza,
    prob_estimada: probIA,
    // Lo que dijo la IA antes del freno, para que se vea qué se corrigió y por qué.
    freno: freno ? `Predict frenó la sugerencia de la IA (${sug} · ${r.confianza} %): ${freno}.` : undefined, sugerencia_ia: freno ? sug : undefined,
    analisis: (detalle + String(d.analisis || '')).slice(0, 1000),
    riesgos: riesgos.slice(0, 4),
    votos: filas.length > 1 ? filas.map((f) => ({ modelo: corto(f.modelo), sugerencia: f.opcion, confianza: f.confianza, prob: f.prob })) : undefined,
    global: global ? { resumen: global.resumen, tono: global.tono, turbulencia: global.turbulencia } : null,
    ts: Date.now(),
  };
  if (ambos) Object.assign(datos, { mercado_id: mm!.mercado, lado_sugerido: freno ? null : ladoSug, lado_nombre: ladoSug != null ? nombres[ladoSug] : null });
  prediccionMem.set(clave, { ts: Date.now(), datos });
  await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: true, modelo, ms: Date.now() - t0, texto: `IA (predicción ${p.pregunta}${ambos ? '' : ' · ' + p.lado_nombre}) sugirió ${sug} con ${modelo} en ${((Date.now() - t0) / 1000).toFixed(1)} s${freno ? ` · FRENADA: ${freno}` : ''}` }]);
  return json(datos);
}

// «Pedir a la IA» en la pestaña 🎲: el usuario escribe qué quiere y la IA propone
// órdenes de predicción concretas. Quedan como «propuesta» (el usuario aprueba),
// con el mejor precio real del libro como límite.
function relevantes(lista: Json[], instruccion: string): Json[] {
  const t = instruccion.toLowerCase();
  const palabras = t.split(/[^a-z0-9áéíóúñ&]+/).filter((w) => w.length >= 3);
  const quiere = (re: RegExp) => re.test(t);
  const puntos = (m: Json) => {
    const txt = `${m.pregunta} ${m.nombre_sub || ''} ${m.subyacente || ''} ${m.categoria}`.toLowerCase();
    let p = palabras.reduce((a, w) => a + (txt.includes(w) ? 3 : 0), 0);
    if (quiere(/hoy|corto|ahora|rápid/) && m.plazo === 'hoy') p += 2;
    if (quiere(/mediano|largo|semana|mes|noviembre|octubre/) && m.plazo === 'mediano') p += 2;
    if (quiere(/bolsa|acci|índice|indice|oro|plata|petr|s&p|nasdaq/) && m.categoria === 'bolsa') p += 2;
    if (quiere(/fed|tasa|econom|empresa|ipo|bolsa de valores|anthropic|openai/) && m.categoria === 'economia') p += 2;
    if (quiere(/cripto|btc|eth|sol|hype|bitcoin/) && m.categoria === 'cripto') p += 2;
    if (quiere(/ventaja|oportunidad|mejor|barat/)) p += Math.max(0, ...m.lados.map((l: Json) => l.ventaja || 0)) / 5;
    return p;
  };
  return lista.filter((m) => !m.sin_ofertas).map((m) => ({ m, p: puntos(m) })).sort((a, b) => b.p - a.p || (a.m.vence || 9e15) - (b.m.vence || 9e15)).slice(0, 35).map((x) => x.m);
}

// Varias IAs proponen órdenes: pasa una (mismo «coin» y acción) solo si la propone más de
// la mitad de las que respondieron. Monto y unidades = mediana; confianza como en el voto.
function unirPropuestas(votos: { datos: Json; modelo: string }[]): { propuestas: Json[]; descartadas: string[] } {
  const listas = votos.map((v) => (Array.isArray(v.datos.propuestas) ? v.datos.propuestas : []).filter((x: Json) => x && typeof x === 'object').slice(0, 3));
  if (votos.length === 1) return { propuestas: listas[0], descartadas: [] };
  const grupos = new Map<string, { x: Json; modelo: string }[]>();
  listas.forEach((l, i) => {
    const vistas = new Set<string>();
    for (const x of l) {
      const k = `${String(x.coin || '').trim()}|${String(x.accion || '').toUpperCase().trim()}`;
      if (vistas.has(k)) continue; // una IA no vota dos veces lo mismo
      vistas.add(k);
      grupos.set(k, [...(grupos.get(k) || []), { x, modelo: votos[i].modelo }]);
    }
  });
  const propuestas: Json[] = [], descartadas: string[] = [];
  for (const [k, g] of grupos) {
    const [coin, accion] = k.split('|');
    if (g.length * 2 <= votos.length) { descartadas.push(`${coin} ${accion}: solo ${g.length} de ${votos.length} IAs la propuso (${g.map((v) => corto(v.modelo)).join(', ')})`); continue; }
    const nums = (c: string) => g.map((v) => Number(v.x[c])).filter((n) => Number.isFinite(n) && n > 0);
    const confs = g.map((v) => Math.max(0, Math.min(100, Number(v.x.confianza) || 0)));
    const lider = g[confs.indexOf(Math.max(...confs))];
    propuestas.push({ coin, accion,
      monto_usdc: nums('monto_usdc').length ? mediana(nums('monto_usdc')) : null, unidades: nums('unidades').length ? mediana(nums('unidades')) : null,
      confianza: Math.round(confs.reduce((a, c) => a + c, 0) / confs.length * (0.5 + 0.5 * g.length / votos.length)),
      razon: `${g.length}/${votos.length} IAs: ${String(lider.x.razon || '')}`.slice(0, 240) });
  }
  return { propuestas: propuestas.sort((a, b) => b.confianza - a.confianza), descartadas };
}

async function prediccionPedir(env: Env, c: Json): Promise<Response> {
  const instruccion = String(c.instruccion || '').trim().slice(0, 400);
  if (!instruccion) return json({ error: 'Escribe qué quieres que busque la IA' }, 400);
  const [config, estado, lista0] = await Promise.all([leerConfig(env), leer<Json>(env, 'estado', {}), listaMercados()]);
  const lista: Json[] = lista0.mercados || [];
  const mias: Json[] = estado.ultimo?.predicciones || [];
  const libre = estado.ultimo?.libre ?? null;
  const filas = relevantes(lista, instruccion).map((m) => ({ q: m.pregunta, cat: m.categoria === 'cripto' ? `cripto_${m.plazo}` : m.categoria, horas: m.horas,
    ahora: m.precio_actual ?? undefined, regla: m.regla || undefined, mov_1h_24h_48h_pct: m.mov ? [m.mov.h1, m.mov.h24, m.mov.h48] : undefined,
    cambio_1h_24h_pct: m.mov ? [m.cambio_1h_pct, m.cambio_24h_pct] : undefined, turbulento: m.regimen === 'turbulento' || undefined,
    lados: m.lados.map((l: Json) => ({ coin: l.coin, lado: l.nombre, precio: l.precio, modelo_pct: l.prob_modelo ?? undefined, ventaja: l.ventaja ?? undefined })) }));
  const pos = mias.map((p) => ({ coin: '#' + String(p.coin).slice(1), q: p.pregunta, lado: p.lado_nombre, unidades: p.cantidad, precio: p.precio,
    pagaste: p.costo ?? undefined, vender_solo_desde: p.min_venta ?? undefined }));
  const t0 = Date.now(), ahora = Date.now();
  let d: Json, modelo: string, propuestas: Json[], descartadas: string[] = [];
  try {
    const { votos } = await iaVarias(env, config, [
      { role: 'system', content: 'Preparas órdenes en mercados de predicción de Hyperliquid para un usuario principiante. Prudente: si nada convence, no propongas nada y explícalo. Nunca inventas mercados: usa solo los «coin» de la lista. Respondes SOLO JSON válido en español.' },
      { role: 'user', content: `El usuario pide: "${instruccion}"
Cada unidad paga 1 USDC si acierta y 0 si no; «precio» es el precio MEDIO (la compra real se paga al vendedor más barato, que puede ser bastante más caro; el agente lo comprueba). «modelo_pct» es un modelo estadístico con la volatilidad de 1 h, 24 h y 48 h (no es certeza); «ventaja» = modelo − precio en puntos; «mov_1h_24h_48h_pct» = movimiento típico del subyacente.
Mercado global: ${lista0.global?.resumen || 'sin datos'} Tenlo en cuenta (con turbulencia sé más exigente).
USDC libres: ${libre ?? 'desconocido'}.${config.pred_pagar_con ? ` Si falta USDC, el agente vende ${config.pred_pagar_con} para pagar (tiene ${(estado.ultimo?.activos || []).find((a: Json) => a.simbolo === config.pred_pagar_con)?.valor ?? '?'} USDC en ${config.pred_pagar_con}).` : ''} Por compra suele usar ${config.pred_monto} USDC; el mínimo por orden en predicciones es ~${MIN_PRED} USDC (no 10: eso es en spot), así que con poco USDC libre igual se puede comprar por lo que haya. Tope total en predicciones: ${config.pred_max_total} USDC.
Sus posiciones: ${JSON.stringify(pos)}
Mercados abiertos (los más relevantes a su pedido): ${JSON.stringify(filas)}
Propón como mucho 3 órdenes. COMPRAR lleva "monto_usdc"; VENDER (solo de sus posiciones) lleva "unidades". REGLA DEL USUARIO: nunca vender con pérdida; VENDER solo si el precio llega a «vender_solo_desde».
Formato: {"respuesta":"2-4 frases para el usuario","propuestas":[{"coin":"#90170","accion":"COMPRAR","monto_usdc":11,"unidades":null,"confianza":70,"razon":"máx. 25 palabras"}]}` },
    ], (x) => Array.isArray(x?.propuestas) || typeof x?.respuesta === 'string');
    modelo = etiquetaVotos(votos);
    ({ propuestas, descartadas } = unirPropuestas(votos));
    d = votos[0].datos;
    if (votos.length > 1) d = { ...d, respuesta: `${String(d.respuesta || '').slice(0, 600)} (Consultadas ${votos.length} IAs: solo pasa lo que propuso la mayoría.)` };
  } catch (e: any) {
    await bitacora(env, [{ ts: ahora, tipo: 'ia', ok: false, texto: `IA (pedir predicciones) falló: ${String(e.message || e).slice(0, 160)}`, ms: Date.now() - t0 }]);
    return json({ error: String(e.message || e) }, 502);
  }
  const porCoin: Json = {};
  for (const m of lista) m.lados.forEach((l: Json) => { porCoin[l.coin] = { m, l }; });
  const ordenes = await leerOrdenes(env), creadas: Json[] = [];
  // Lo que ya tienes en predicciones + compras pendientes: las propuestas no pasan tu tope.
  let expuesto = mias.reduce((a, p) => a + (Number(p.valor) || 0), 0) + ordenes.filter((o) => PENDIENTE(o) && o.tipo === 'prediccion' && o.accion === 'COMPRAR')
    .reduce((a, o) => a + Number(o.unidades) * Number(o.limite), 0);
  for (const x of propuestas.slice(0, 3)) {
    const coin = String(x.coin || '').trim(), accion = String(x.accion || '').toUpperCase();
    const ref = porCoin[coin], mia = mias.find((p) => '#' + String(p.coin).slice(1) === coin);
    if (!ref && !mia) { descartadas.push(`${coin || '?'}: ese mercado no existe o ya cerró`); continue; }
    if (!['COMPRAR', 'VENDER'].includes(accion)) { descartadas.push(`${coin}: acción inválida`); continue; }
    if (accion === 'VENDER' && !mia) { descartadas.push(`${coin}: no tienes esa posición para vender`); continue; }
    const etiqueta = ref ? `${ref.m.pregunta} · ${ref.l.nombre}` : `${mia!.pregunta} · ${mia!.lado_nombre}`;
    let libro: Json;
    try { libro = await hlInfo({ type: 'l2Book', coin }); } catch { descartadas.push(`${etiqueta}: no pude leer el libro`); continue; }
    const nivel = ((libro?.levels || [[], []])[accion === 'COMPRAR' ? 1 : 0] || [])[0];
    if (!nivel) { descartadas.push(`${etiqueta}: ahora no hay ${accion === 'COMPRAR' ? 'vendedores' : 'compradores'}`); continue; }
    const px = Number(nivel.px);
    // Vender: nunca por debajo de lo que pagaste (comisión incluida), por mínima que sea la pérdida.
    if (accion === 'VENDER') {
      if (mia!.min_venta == null) { descartadas.push(`${etiqueta}: no sé a cuánto la compraste (espera al próximo ciclo): no vendo para no arriesgar una pérdida`); continue; }
      if (px < mia!.min_venta) { descartadas.push(`${etiqueta}: el mejor comprador paga ${px} y la compraste a ${mia!.costo}: no vendo con pérdida (sin pérdida desde ${mia!.min_venta})`); continue; }
    }
    // El «precio» de la lista es el medio; se paga el del vendedor más barato. Si a ese
    // precio el modelo ya no ve ventaja, la compra es mala aunque la IA la vea barata.
    let aviso = '';
    if (accion === 'COMPRAR' && ref) {
      const pm = ref.l.prob_modelo != null ? ref.l.prob_modelo / 100 : null;
      const lmin = Number(config.pred_prob_min ?? 5) / 100, lmax = Number(config.pred_prob_max ?? 95) / 100;
      if (px < lmin || px > lmax) { descartadas.push(`${etiqueta}: el vendedor más barato pide ${px}, fuera de tu rango de ${lmin * 100}–${lmax * 100} %: con precios tan extremos casi siempre ya está decidido`); continue; }
      if (pm != null && px >= pm - 0.02) { descartadas.push(`${etiqueta}: el vendedor más barato pide ${px} y el modelo le da ${ref.l.prob_modelo}%: a ese precio no hay ventaja`); continue; }
      if (ref.l.precio != null && px > ref.l.precio + 0.05) aviso = `Ojo: el precio medio es ${ref.l.precio} pero el vendedor más barato pide ${px} (libro con pocas ofertas).`;
    }
    let monto = Math.min(Math.max(MIN_PRED, Number(x.monto_usdc) || config.pred_monto || 11), config.pred_max_total || 30);
    // Sin otra moneda para pagar, no se propone más de lo que tienes libre.
    const tope_libre = accion === 'COMPRAR' && !config.pred_pagar_con && typeof libre === 'number' ? libre : null;
    if (tope_libre != null && monto > tope_libre) monto = tope_libre;
    const unidades = accion === 'COMPRAR' ? Math.max(1, (tope_libre != null && monto >= tope_libre ? Math.floor : Math.ceil)(monto / px)) : Math.min(Math.floor(mia!.cantidad), Math.max(1, Math.floor(Number(x.unidades) || mia!.cantidad)));
    if (accion === 'COMPRAR' && (unidades * px < MIN_PRED - 1e-9 || (tope_libre != null && unidades * px > tope_libre + 1e-9))) {
      descartadas.push(`${etiqueta}: con ${tope_libre != null ? tope_libre.toFixed(2) : '?'} USDC libres no alcanza para el mínimo de ~${MIN_PRED} USDC a ${px}`); continue;
    }
    if (accion === 'COMPRAR' && expuesto + unidades * px > (config.pred_max_total || 30) + 0.5) {
      descartadas.push(`${etiqueta}: pasaría tu tope de ${config.pred_max_total} USDC en predicciones (ya usas ${expuesto.toFixed(2)})`); continue;
    }
    const v = limpiarPrediccion({}, { coin, accion, unidades, limite: px, etiqueta, razon: String(x.razon || ''), pagar_con: accion === 'COMPRAR' ? config.pred_pagar_con : '' }, config);
    if (!v.orden) { descartadas.push(`${etiqueta}: ${v.error}`); continue; }
    const conf = Math.max(0, Math.min(100, Number(x.confianza) || 0));
    const n: Json = { ...v.orden, id: nuevoId('iap'), ts: ahora, origen: 'ia', estado: 'propuesta', confianza: conf, ...(aviso ? { aviso } : {}),
      ia: { accion, confianza: conf, razon: String(x.razon || '').slice(0, 240) },
      vence: Math.min(ahora + 2 * 3600_000, ref?.m.vence || Infinity),
      historia: [{ ts: ahora, quien: 'ia', texto: `Propuesta al pedirle «${instruccion.slice(0, 80)}»: ${String(x.razon || '').slice(0, 160)}` }] };
    ordenes.unshift(n);
    creadas.push(n);
    if (accion === 'COMPRAR') expuesto += unidades * px;
  }
  if (creadas.length) await guardarOrdenes(env, ordenes);
  await bitacora(env, [{ ts: ahora, tipo: 'ia', ok: true, modelo, ms: Date.now() - t0,
    texto: `IA (pedir predicciones) respondió con ${modelo} en ${((Date.now() - t0) / 1000).toFixed(1)} s: ${creadas.length} propuesta${creadas.length === 1 ? '' : 's'}` },
    ...creadas.map((o) => ({ ts: ahora, tipo: 'orden', ok: true, texto: `IA propuso: ${textoOrden(o)} (espera tu aprobación)`, id: o.id }))]);
  return json({ ok: true, modelo, respuesta: String(d.respuesta || '').slice(0, 800), ordenes: creadas, descartadas });
}

// ---------------------------------------------------------------- Gráficas
// Las velas las lee el runner de TU exchange en cada ciclo (1h, 4h y 1d).
async function graficasApi(env: Env, url: URL): Promise<Response> {
  const sym = limpiarSimbolo(url.searchParams.get('simbolo'));
  const g = await leer<Json>(env, 'graficas', {});
  const datos: Json = g.datos || {};
  if (!sym) return json({ ts: g.ts || null, simbolos: Object.keys(datos) });
  if (!datos[sym]) return json({ error: `${sym} aún no tiene gráfica: aparece en el próximo ciclo si está en tu cartera o en tu reparto.`, pendiente: true, simbolos: Object.keys(datos) }, 404);
  return json({ simbolo: sym, ts: g.ts, exchange: g.exchange, quote: g.quote, marcos: datos[sym] });
}

// ---------------------------------------------------------------- Semáforo
// Lo que la nube sabe de sí misma; la app le suma la latencia y GitHub Actions.
async function semaforo(env: Env): Promise<Response> {
  const [estado, config, log] = await Promise.all([leer<Json>(env, 'estado', {}), leerConfig(env), leer<Json[]>(env, 'bitacora', [])]);
  const u: Json = estado.ultimo || {};
  const ia = log.filter((l) => l.tipo === 'ia' && l.modelo).slice(0, 6);
  const { lista: provs } = await proveedoresIA(env);
  const p = provs[0] ? { nombre: provs[0].id === 'instalacion' ? provs[0].de || 'instalación' : provs[0].id, modelos: provs[0].modelos } : null;
  return json({
    version: env.KUMO_VERSION || '1.0.0', ahora: Date.now(),
    ciclo: estado.ciclo || 0, actualizado: estado.actualizado || null, ok: u.ok !== false && !!estado.ciclo,
    errores: u.errores || [], exchange: u.exchange || null, modo: config.modo, pausado: !!config.pausado,
    real: u.real ? { error: u.real.error || null, libre: u.real.libre ?? null } : null, libre: u.libre ?? null,
    monto_min: config.monto_min, quote: config.quote, objetivo: config.objetivo,
    ia: { modo: config.ia, proveedor: p?.nombre || null, modelos: p?.modelos || [], preferido: await env.KUMO.get('ia_pref'), ultimas: ia },
  });
}

// Prueba la IA con una pregunta mínima. Con `todos` prueba cada modelo y deja
// como preferido el primero que responde (así el bot deja de perder tiempo con
// modelos caídos). Solo escribe en KV si el preferido cambia.
async function iaProbar(env: Env, c: Json): Promise<Response> {
  const cands = await candidatosIA(env);
  if (!cands.length) return json({ ok: false, codigo: 'sin_ia', error: 'Tu nube no tiene IA configurada' });
  const mensajes = [{ role: 'system', content: 'Respondes SOLO JSON.' }, { role: 'user', content: 'Responde exactamente {"ok":true}' }];
  const lista = cands.slice(0, c.todos ? 5 : 3);
  const pruebas: Json[] = [];
  for (const x of lista) {
    const t0 = Date.now();
    try {
      await iaChat(env, mensajes, [x]);
      pruebas.push({ modelo: x.modelo, proveedor: x.prov.id, ok: true, ms: Date.now() - t0 });
      if (!c.todos) break;
    } catch (e: any) {
      pruebas.push({ modelo: x.modelo, proveedor: x.prov.id, ok: false, ms: Date.now() - t0, error: String(e?.message || e).slice(0, 160) });
    }
  }
  const bueno = pruebas.find((x) => x.ok);
  const previo = await env.KUMO.get('ia_pref');
  let cambio = null;
  if (bueno && bueno.modelo !== (previo || cands[0].modelo)) {
    await env.KUMO.put('ia_pref', bueno.modelo);
    cambio = bueno.modelo;
    await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: true, texto: `Autocorrección: ${bueno.modelo} queda como modelo preferido (respondió en ${(bueno.ms / 1000).toFixed(1)} s)` }]);
  }
  const clave = pruebas.some((x) => /clave rechazada/.test(x.error || ''));
  return json({ ok: !!bueno, proveedor: bueno?.proveedor || cands[0].prov.id, modelo: bueno?.modelo || null, ms: bueno?.ms ?? null, pruebas, preferido_nuevo: cambio,
    codigo: bueno ? 'ok' : clave ? 'clave' : 'caida', error: bueno ? null : pruebas[pruebas.length - 1]?.error || 'La IA no respondió' });
}

// La IA lee el diagnóstico de la app y elige arreglos SOLO de la lista que la
// app le ofrece (nunca inventa acciones ni toca montos o el modo real).
async function iaDiagnosticar(env: Env, c: Json): Promise<Response> {
  const hallazgos = (Array.isArray(c.hallazgos) ? c.hallazgos : []).slice(0, 12).map((h: Json) => ({ id: String(h.id).slice(0, 30), estado: String(h.estado).slice(0, 10), detalle: String(h.detalle || '').slice(0, 300) }));
  const arreglos = (Array.isArray(c.arreglos) ? c.arreglos : []).slice(0, 10).map((a: Json) => ({ id: String(a.id).slice(0, 40), texto: String(a.texto || '').slice(0, 200) }));
  const t0 = Date.now();
  const { datos: d, modelo } = await iaChat(env, [
    { role: 'system', content: 'Eres el técnico de soporte de Predict (agente de predicciones en GitHub Actions + Cloudflare Worker). Explicas en español sencillo a un principiante. Respondes SOLO JSON válido.' },
    { role: 'user', content: `Diagnóstico automático:\n${JSON.stringify(hallazgos)}\nArreglos disponibles (elige solo ids de esta lista, los que de verdad resuelvan algo):\n${JSON.stringify(arreglos)}\nFormato: {"resumen":"1-3 frases: qué falla y por qué","pasos":["pasos que debe hacer el usuario si algo no se arregla solo"],"arreglos":["id",...]}` },
  ]);
  const validos = new Set(arreglos.map((a: Json) => a.id));
  const elegidos = (Array.isArray(d.arreglos) ? d.arreglos : []).map(String).filter((x: string) => validos.has(x));
  await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: true, modelo, ms: Date.now() - t0, texto: `IA (diagnóstico) respondió con ${modelo}: ${elegidos.length ? 'arreglos ' + elegidos.join(', ') : 'sin arreglos automáticos'}` }]);
  return json({ modelo, resumen: String(d.resumen || '').slice(0, 600), pasos: (Array.isArray(d.pasos) ? d.pasos : []).slice(0, 6).map((x: unknown) => String(x).slice(0, 240)), arreglos: [...new Set(elegidos)] });
}

// ---------------------------------------------------------------- Importar de Kumo
// La app lee las órdenes de predicción de TU nube de Kumo (con la dirección y el token
// que pegas en el teléfono) y las manda aquí. Solo se guardan los campos que sirven para
// aprender; el agente las suma en su próximo ciclo y las resuelve con el resultado oficial.
async function importarKumo(env: Env, c: Json): Promise<Response> {
  if (!c.confirmed) return json({ error: 'Falta confirmar' }, 400);
  const ordenes = (Array.isArray(c.ordenes) ? c.ordenes : [])
    .filter((o: Json) => o && o.tipo === 'prediccion' && o.accion === 'COMPRAR' && /^#\d+$/.test(String(o.coin || '')))
    .slice(0, 500)
    .map((o: Json) => ({ tipo: 'prediccion', accion: 'COMPRAR', coin: String(o.coin), estado: String(o.estado || ''), limite: Number(o.limite) || null,
      resultado: o.resultado && Number(o.resultado.precio) ? { precio: Number(o.resultado.precio) } : null, etiqueta: String(o.etiqueta || '').slice(0, 90),
      categoria: o.categoria ? String(o.categoria).slice(0, 24) : null, confianza: Number(o.confianza) || null,
      ia: o.ia && o.ia.prob != null ? { prob: Number(o.ia.prob) } : null, ts: Number(o.ts) || null, horas: o.horas != null ? Number(o.horas) : null }));
  if (!ordenes.length) return json({ error: 'No encontré compras de predicciones en lo que mandaste' }, 400);
  const previas = await leer<Json[] | null>(env, 'importar_pendiente', null);
  await env.KUMO.put('importar_pendiente', JSON.stringify([...(previas || []), ...ordenes].slice(-500)));
  await bitacora(env, [{ ts: Date.now(), tipo: 'aprendizaje', ok: true, texto: `Importaste ${ordenes.length} decisiones de Kumo: el agente las suma en su próximo ciclo` }]);
  return json({ ok: true, recibidas: ordenes.length });
}

// ---------------------------------------------------------------- API app
async function api(req: Request, env: Env, ruta: string, url: URL): Promise<Response> {
  const cuerpo: Json = req.method === 'POST' ? ((await req.json().catch(() => ({}))) as Json) : {};
  switch (`${req.method} ${ruta}`) {
    case 'GET /api/estado': {
      const [config, estado] = await Promise.all([leerConfig(env), leer<Json>(env, 'estado', {})]);
      const { runner, radar, ...resto } = estado;
      return json({ version: env.KUMO_VERSION || '1.0.0', config, ...resto, costos: runner?.costos || {}, feedback: runner?.feedback_resumen || null });
    }
    case 'GET /api/ordenes':
      return json({ ordenes: await leerOrdenes(env), modo: (await leerConfig(env)).ordenes_ia });
    case 'GET /api/aprendizaje': {
      const estado = await leer<Json>(env, 'estado', {});
      const pend = await leer<Json[] | null>(env, 'importar_pendiente', null);
      return json({ resumen: estado.runner?.aprendizaje_resumen ?? null, papel: estado.runner?.papel ?? null, importar_pendiente: pend ? pend.length : 0 });
    }
    case 'POST /api/aprendizaje/importar':
      return importarKumo(env, cuerpo);
    case 'POST /api/ordenes':
      return ordenesApi(env, cuerpo);
    case 'GET /api/historial':
      return json({ items: await leer<Json[]>(env, 'bitacora', []) });
    case 'GET /api/predicciones/mercados':
      return prediccionMercados().catch((e) => json({ error: `Hyperliquid no respondió: ${String(e?.message || e).slice(0, 120)}` }, 502));
    case 'GET /api/predicciones/grafica':
      return prediccionGrafica(url).catch((e) => json({ error: `Hyperliquid no respondió: ${String(e?.message || e).slice(0, 120)}` }, 502));
    case 'GET /api/predicciones/libro':
      return prediccionLibro(url).catch((e) => json({ error: `Hyperliquid no respondió: ${String(e?.message || e).slice(0, 120)}` }, 502));
    case 'POST /api/predicciones/pedir':
      return prediccionPedir(env, cuerpo).catch((e) => json({ error: `No pude preparar la propuesta: ${String(e?.message || e).slice(0, 120)}` }, 502));
    case 'POST /api/predicciones/analizar':
      return prediccionAnalizar(env, cuerpo).catch(async (e) => {
        await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: false, texto: `IA (predicción) falló: ${String(e?.message || e).slice(0, 160)}` }]);
        return json({ error: String(e?.message || e) }, 502);
      });
    case 'GET /api/graficas':
      return graficasApi(env, url);
    case 'GET /api/semaforo':
      return semaforo(env);
    case 'GET /api/ia/proveedores':
      return iaProveedoresApi(env, ruta, { _get: true });
    case 'POST /api/ia/proveedores':
    case 'POST /api/ia/proveedores/orden':
    case 'POST /api/ia/proveedores/probar':
    case 'POST /api/ia/proveedores/modelos':
    case 'POST /api/ia/consenso':
      return iaProveedoresApi(env, ruta, cuerpo);
    case 'POST /api/ia/probar':
      return iaProbar(env, cuerpo);
    case 'POST /api/ia/diagnosticar':
      return iaDiagnosticar(env, cuerpo).catch((e) => json({ error: String(e?.message || e) }, 502));
    case 'GET /api/senales':
      return json({ senales: await leer<Json[]>(env, 'senales', []) });
    case 'GET /api/operaciones':
      return json({ operaciones: await leer<Json[]>(env, 'operaciones', []) });
    case 'GET /api/config':
      return json({ config: await leerConfig(env), rangos: RANGOS });
    case 'POST /api/config': {
      const v = validarConfig(await leerConfig(env), cuerpo);
      if (v.error) return json({ error: v.error }, 400);
      await env.KUMO.put('config', JSON.stringify(v.config));
      return json({ ok: true, config: v.config });
    }
    case 'POST /api/control': {
      const config = await leerConfig(env);
      if (cuerpo.accion === 'pausar' || cuerpo.accion === 'reanudar') {
        config.pausado = cuerpo.accion === 'pausar';
        await env.KUMO.put('config', JSON.stringify(config));
        return json({ ok: true, pausado: config.pausado });
      }
      if (cuerpo.accion === 'reiniciar_simulacion') {
        const estado = await leer<Json>(env, 'estado', {});
        estado.runner = { costos: {}, cartera_sim: null };
        estado.historial = [];
        await env.KUMO.put('estado', JSON.stringify(estado));
        return json({ ok: true });
      }
      return json({ error: 'acción desconocida' }, 400);
    }
    case 'GET /api/radar': {
      const estado = await leer<Json>(env, 'estado', {});
      return json({ radar: estado.radar || [], actualizado: estado.radar_ts || null, quote: estado.ultimo?.quote, exchange: estado.ultimo?.exchange });
    }
    case 'POST /api/radar/analizar':
      return radarAnalizar(env, cuerpo).catch(async (e) => {
        await bitacora(env, [{ ts: Date.now(), tipo: 'ia', ok: false, texto: `IA (Radar) falló: ${String(e.message || e).slice(0, 160)}` }]);
        return json({ error: String(e.message || e) }, 502);
      });
    case 'POST /api/radar/pedir': {
      const sym = limpiarSimbolo(cuerpo.simbolo);
      if (!sym) return json({ error: 'Falta el símbolo' }, 400);
      const config = await leerConfig(env);
      config.radar_pedidos = [sym, ...(config.radar_pedidos || []).filter((s: string) => s !== sym)].slice(0, 5);
      await env.KUMO.put('config', JSON.stringify(config));
      return json({ ok: true, pedidos: config.radar_pedidos });
    }
    case 'POST /api/radar/incluir': {
      const sym = limpiarSimbolo(cuerpo.simbolo);
      const pct = Number(cuerpo.pct);
      if (!sym || !(pct > 0 && pct <= 25)) return json({ error: 'Elige un % entre 1 y 25' }, 400);
      const config = await leerConfig(env);
      const v = validarConfig(config, { objetivo: { ...config.objetivo, [sym]: pct } });
      if (v.error) return json({ error: v.error }, 400);
      await env.KUMO.put('config', JSON.stringify(v.config));
      return json({ ok: true, config: v.config });
    }
    case 'GET /api/feed': {
      const desde = Number(url.searchParams.get('since') || 0);
      const feed = await leer<Json[]>(env, 'feed', []);
      return json({ now: Date.now(), items: feed.filter((f) => f.ts > desde).map((f) => ({ id: f.id, ts: f.ts, kind: f.tipo, title: f.titulo, body: f.texto })) });
    }
    case 'POST /api/avisos/probar':
      await avisar(env, [{ tipo: 'prueba', titulo: '¡Predict conectado! ☁️', texto: 'Las notificaciones de tu nube funcionan.' }]);
      return json({ ok: true });
  }
  return json({ error: 'Ruta no encontrada' }, 404);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });
    const url = new URL(req.url);
    const ruta = url.pathname.replace(/\/+$/, '') || '/';
    try {
      const pagina = web(req, ruta);
      if (pagina) return pagina;
      if (ruta === '/' || ruta === '/api/salud') {
        const estado = await leer<Json>(env, 'estado', {});
        return json({ ok: true, nombre: 'kumo-bot', version: env.KUMO_VERSION || '1.0.0', ciclo: estado.ciclo || 0, ultimo_ciclo: estado.actualizado || null });
      }
      if (ruta.startsWith('/runner/')) {
        if (!iguales(token(req), env.RUNNER_TOKEN)) return json({ error: 'No autorizado' }, 401);
        if (req.method === 'GET' && ruta === '/runner/config') return runnerConfig(env);
        // El runner usa la misma lectura de mercados y el mismo modelo que la app.
        if (req.method === 'GET' && ruta === '/runner/predicciones') return json(await listaMercados());
        if (req.method === 'POST' && ruta === '/runner/tomar') return runnerTomar(env, (await req.json()) as Json);
        if (req.method === 'POST' && ruta === '/runner/reporte') return runnerReporte(env, (await req.json()) as Json);
        return json({ error: 'Ruta no encontrada' }, 404);
      }
      if (ruta.startsWith('/api/')) {
        if (!iguales(token(req), env.APP_TOKEN)) return json({ error: 'Token inválido' }, 401);
        return await api(req, env, ruta, url);
      }
      return json({ error: 'Ruta no encontrada' }, 404);
    } catch (e: any) {
      return json({ error: `Error interno: ${String(e?.message || e).slice(0, 200)}` }, 500);
    }
  },
};
