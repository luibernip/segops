#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor de Bitácora AQD para la nube (GitHub Actions).

Corre headless, inicia sesión con las credenciales de las variables de
entorno AQD_USUARIO / AQD_CLAVE, y extrae las ocurrencias activas Year to
Date de los tipos FLT, CBN, FRM y GRH usando el endpoint JSON del portal.

Escribe: bitacora_real.csv, bitacora_cbn_real.csv, bitacora_frm_real.csv,
bitacora_grh_real.csv
(formato: id;fecha;registrada;estado;investigacion;riesgo;titulo;matricula)
"""

import json
import re
import os
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

CARPETA = Path(__file__).resolve().parent
URL = "https://bitacora.avianca.com/AQDPortal/safety.aspx"
# (código para el reporte, CSV de salida, textos con que el portal nombra el
# tipo: se prueba primero el rel exacto y luego rel/etiqueta que los contenga.
# El rel y la etiqueta no siempre coinciden: rel CBN = "CAB Safety Occurrence"
# y rel GRN = "GRH/ATO Safety Occurrence", de ahí que el código del reporte y
# el alias del portal sean campos distintos)
TIPOS = (("FLT", "bitacora_real.csv", ("FLT",)),
         ("CBN", "bitacora_cbn_real.csv", ("CBN",)),
         ("FRM", "bitacora_frm_real.csv", ("FRM",)),
         ("GRH", "bitacora_grh_real.csv", ("GRN", "GRH/ATO")))
# Fechas en hora de Colombia (el runner de GitHub está en UTC)
from datetime import datetime
from zoneinfo import ZoneInfo
HOY = datetime.now(ZoneInfo("America/Bogota")).date()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def esperar(page, cond_js, seg):
    fin = time.time() + seg
    while time.time() < fin:
        try:
            if page.evaluate(cond_js):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# El rango de fechas NO se fija escribiendo #Criteria_FromDate/#Criteria_ToDate:
# los gobierna un widget mcDropdown de preajustes y el servidor usa el preajuste
# GUARDADO, no lo que uno escriba en el campo oculto. Hay que hacer clic de
# verdad en "Year to Date" y dejar que el widget rellene las fechas.
# (06-ago-2026: la búsqueda guardada quedó en julio y la nube publicó días
# enteros con solo ese mes — FRM 70 en vez de 460 — sin que nada lo detectara.)
JS_CLIC_YTD = """() => {
    const ul = [...document.querySelectorAll('ul')].find(u =>
        [...u.querySelectorAll('li')].some(li =>
            /year to date/i.test(li.textContent || '')));
    if (!ul) throw new Error('No encontré el menú de rango de fechas');
    const li = [...ul.querySelectorAll('li')].find(li =>
        /year to date/i.test(li.textContent || ''));
    const a = li.querySelector('a') || li;
    ['mousedown', 'mouseup'].forEach(t =>
        a.dispatchEvent(new MouseEvent(t, {bubbles: true})));
    a.click();
}"""

JS_LEER_FECHAS = ("() => ({desde: (document.querySelector('#Criteria_FromDate')"
                  " || {}).value, hasta: (document.querySelector"
                  "('#Criteria_ToDate') || {}).value})")


def es_primero_de_enero(valor, anio):
    """¿El campo de fecha corresponde al 1 de enero de `anio`?

    El portal devuelve el valor en formatos distintos según el navegador:
    "2026-01-01" en el Mac y "01/01/2026 12:00:00 a. m." en el runner de la
    nube (otra configuración regional). Comparar contra un formato fijo tumbó
    la extracción entera el 06-ago-2026 aunque el rango era correcto.
    """
    if not valor:
        return False
    v = str(valor).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (anio, 1, 1)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)   # dd/mm o mm/dd: da igual
    if m:                                              # para el 1 de enero
        return (int(m.group(3)), int(m.group(2)), int(m.group(1))) == (anio, 1, 1)
    return False


def fijar_rango_ytd(page):
    """Deja la búsqueda en Year to Date y comprueba que el widget lo aplicó."""
    page.evaluate(JS_CLIC_YTD)
    time.sleep(1)
    anio = HOY.year
    fechas = page.evaluate(JS_LEER_FECHAS)
    if not es_primero_de_enero(fechas.get("desde"), anio):
        raise RuntimeError(
            f"El rango quedó en {fechas.get('desde')} → {fechas.get('hasta')} "
            f"cuando se esperaba desde el 1 de enero de {anio}: el preajuste "
            "'Year to Date' no se aplicó y el portal devolvería solo parte "
            "del año.")
    return fechas


def guardia_desplome(ruta, nuevas, codigo):
    """¿La extracción se desploma frente al último dato bueno? Red de seguridad
    para causas que no sepamos prever: cualquier filtro que recorte la búsqueda
    se ve como una caída brusca. No aplica si el CSV anterior es de otro año
    (en enero el Year to Date sí se reinicia de verdad)."""
    ruta = Path(ruta)
    if not ruta.exists():
        return True
    if datetime.fromtimestamp(ruta.stat().st_mtime).year != HOY.year:
        return True
    anteriores = max(0, len(ruta.read_text(encoding="utf-8").splitlines()) - 1)
    if anteriores >= 50 and nuevas < anteriores * 0.6:
        log(f"  AVISO: {codigo} trajo {nuevas} ocurrencias y la vez anterior "
            f"eran {anteriores}. Una caída así casi siempre es un filtro mal "
            "aplicado, no la realidad: se conserva el dato anterior.")
        return False
    return True


def guardar_csv(datos, ruta):
    inv_cod = {"Logged for Statistics": "LS", "Assessment Only": "AO",
               "Full Investigation": "FI", "Quick Review": "QR"}
    est_cod = {"Open": "O", "In Progress": "P"}
    limpiar = lambda s: str(s).replace(";", ",").replace("\n", " ").strip()
    lineas = ["id;fecha;registrada;estado;investigacion;riesgo;titulo;matricula"]
    for d in datos:
        lineas.append(";".join([
            limpiar(d["id"]), limpiar(d["fecha"]), limpiar(d["reg"]),
            est_cod.get(d["estado"], limpiar(d["estado"])),
            inv_cod.get(d["inv"], limpiar(d["inv"])),
            limpiar(d["riesgo"]), limpiar(d["titulo"]), limpiar(d["mat"])]))
    # newline="\n": sin esto, en Windows los saltos salen como CRLF y el CSV
    # aparece cambiado entero frente al que genera la nube (Linux, LF).
    Path(ruta).write_text("\n".join(lineas), encoding="utf-8", newline="\n")


def main():
    usuario = os.environ.get("AQD_USUARIO", "")
    clave = os.environ.get("AQD_CLAVE", "")
    if not usuario or not clave:
        sys.exit("Faltan los secretos AQD_USUARIO / AQD_CLAVE.")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        page = navegador.new_page(viewport={"width": 1400, "height": 900})
        log("Abriendo Bitácora…")
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)

        if page.locator("#txtPassword").count():
            log("Iniciando sesión…")
            page.fill("#txtUsername", usuario)
            page.fill("#txtPassword", clave)
            page.click("#Submit1")
            if not esperar(page, "() => !document.querySelector('#txtPassword')", 60):
                sys.exit("ERROR: el login de Bitácora no se completó "
                         "(¿credenciales incorrectas o el portal bloquea esta IP?).")

        if not esperar(page, "() => !!(window.jQuery && "
                             "jQuery('#SAF_SearchOccurrences').length)", 90):
            sys.exit("ERROR: no cargó Safety Management.")

        # Desplegar el panel Search Occurrences (su contenido carga al abrirse)
        log("Desplegando panel de búsqueda…")
        inicio, intento = time.time(), 0
        while time.time() - inicio < 90:
            if page.evaluate("() => !!document.querySelector('#Criteria_Status')"):
                break
            page.evaluate("""(i) => {
                const esTitulo = e => {
                    const t = (e.textContent || '').replace(/\\s+/g, ' ').trim();
                    return t.startsWith('Search Occurrences') && t.length < 40;
                };
                const heads = [...document.querySelectorAll('div, span, a, h1, h2, h3')]
                    .filter(e => e.childElementCount <= 4 && esTitulo(e));
                for (const h of heads) {
                    const cont = h.closest('[id^="Collapsing_Section"], .ui-collapsible')
                                 || h.parentElement;
                    const flecha = cont ? cont.querySelector(
                        'a[onclick], .ui-icon, img, [class*="collaps" i] a, button') : null;
                    const objetivos = [flecha, h, h.parentElement].filter(Boolean);
                    try { objetivos[i % objetivos.length].click(); } catch (e) {}
                }
            }""", intento)
            intento += 1
            time.sleep(3)
        if not page.evaluate("() => !!document.querySelector('#Criteria_Status')"):
            sys.exit("ERROR: el panel Search Occurrences no cargó.")
        time.sleep(2)

        for codigo, archivo, alias in TIPOS:
            log(f"Extrayendo {codigo}…")
            page.evaluate(f"""() => {{
                const clicTipo = cods => {{
                    const ul = [...document.querySelectorAll('ul')].find(u =>
                        [...u.querySelectorAll('li')].some(li =>
                            li.getAttribute('rel') === 'FLT'));
                    const lis = [...ul.querySelectorAll('li')];
                    const rel = l => (l.getAttribute('rel') || '').toUpperCase();
                    const txt = l => (l.textContent || '').toUpperCase();
                    let li = null;
                    for (const c of cods) {{ li = lis.find(l => rel(l) === c); if (li) break; }}
                    for (const c of cods) {{
                        if (li) break;
                        li = lis.find(l => rel(l).includes(c) || txt(l).includes(c));
                    }}
                    if (!li) throw new Error('Tipo no encontrado; opciones: '
                        + lis.map(rel).join(', '));
                    const a = li.querySelector('a') || li;
                    ['mousedown', 'mouseup'].forEach(t =>
                        a.dispatchEvent(new MouseEvent(t, {{bubbles: true}})));
                    a.click();
                }};
                clicTipo({json.dumps([a.upper() for a in alias])});   // tipo de ocurrencia
                const ulSt = [...document.querySelectorAll('ul')].find(u =>
                    [...u.querySelectorAll('li')].some(li =>
                        (li.textContent || '').includes('All Active')));
                const liAA = [...ulSt.querySelectorAll('li')]
                    .find(li => li.getAttribute('rel') === '0');
                const aAA = liAA.querySelector('a') || liAA;
                ['mousedown', 'mouseup'].forEach(t =>
                    aAA.dispatchEvent(new MouseEvent(t, {{bubbles: true}})));
                aAA.click();                                 // All Active
                // las fechas las pone el preajuste Year to Date, aparte
                const txt = document.querySelector('[id="Criteria_Text"]');
                if (txt) txt.value = '';
                const num = document.querySelector('[id="Criteria_OccurrenceNo"]');
                if (num) num.value = '';
            }}""")
            fijar_rango_ytd(page)         # clic real en el preajuste de fechas
            page.evaluate("() => occurrencesSearchClicked()")  # guarda criterios

            datos = []
            for intento in range(8):
                time.sleep(8 if intento == 0 else 6)
                # De a 500 filas por página: pedir más de 1000 de un golpe
                # devuelve 200 con la lista VACÍA cuando el tipo supera esas
                # 1000 ocurrencias (le pasaba a GRH, que ronda las 1400).
                # Se devuelve `records` (el total declarado por el portal)
                # para exigir que estén TODAS: parar cuando una página no
                # aporta filas nuevas acepta resultados parciales cuando las
                # páginas se solapan (30-jul-2026: GRH quedó en 519 de 1405).
                res = page.evaluate("""async () => {
                    const limpio = v => (v === null || v === undefined || v === 'null')
                                        ? '' : String(v);
                    const vistos = new Set();
                    const filas = [];
                    let records = null, maxPag = 60;
                    for (let pagina = 1; pagina <= maxPag; pagina++) {
                        const ctl = new AbortController();
                        setTimeout(() => ctl.abort(), 120000);
                        const r = await fetch('/AQDPortal/safety.aspx/Home/SearchOccurrencesList' +
                            '?withOccTypes=True&_search=false&rows=500&page=' + pagina +
                            '&sidx=OccurrenceDate&sord=desc',
                            {headers: {'Accept': 'application/json'}, signal: ctl.signal});
                        const j = await r.json();
                        const lote = j.rows || [];
                        if (j.records != null) {
                            records = j.records;
                            maxPag = Math.min(60, Math.ceil(records / 500) + 3);
                        }
                        if (!lote.length) break;
                        for (const f of lote) {
                            const c = f.cell || f;
                            const id = limpio(c.OccurrenceID);
                            if (vistos.has(id)) continue;  // pueden solaparse
                            vistos.add(id);
                            filas.push({id: id,
                                fecha: limpio(c.OccurrenceDate),
                                reg: limpio(c.RegisteredOn),
                                estado: limpio(c.Status),
                                inv: limpio(c.InvestigationRequired),
                                riesgo: limpio(c.RiskLevel) + (limpio(c.RiskRating)
                                        ? ' (' + limpio(c.RiskRating) + ')' : ''),
                                titulo: limpio(c.OccurrenceTitle),
                                mat: limpio(c.RegistrationMark),
                                tipo: limpio(c.OccurrenceType)});
                        }
                        if (records != null && filas.length >= records) break;
                    }
                    return {records: records, filas: filas};
                }""")
                todas, records = res["filas"], res["records"]
                datos = [d for d in todas
                         if any(a.upper() in d["tipo"].upper() for a in alias)]
                completo = records is None or len(todas) >= records
                if todas and completo and len(datos) >= len(todas) * 0.5:
                    break
                log(f"  resultado incompleto o con criterios viejos "
                    f"({len(datos)} del tipo, {len(todas)} de {records} "
                    "declaradas); reintentando…")
                datos = []          # parcial o de otro tipo: no sirve, y si
                                    # se agotan los intentos vale más conservar
                                    # el dato anterior que publicar un truncado
                # el servidor puede no haber registrado la búsqueda: reintentarla
                if intento in (2, 4):
                    try:
                        page.evaluate("() => occurrencesSearchClicked()")
                    except Exception:
                        pass
            if not datos:
                # FLT es el tipo principal: si falla, no hay reporte que valga.
                # CBN/FRM/GRH son secundarios: se conserva su último dato y se
                # continúa, para no tumbar toda la actualización por un tipo.
                if codigo == "FLT":
                    sys.exit("ERROR: FLT (tipo principal) no devolvió resultados.")
                log(f"  AVISO: {codigo} no devolvió resultados; se conserva "
                    "el dato anterior de esa pestaña y se continúa.")
                continue
            if not guardia_desplome(CARPETA / archivo, len(datos), codigo):
                if codigo == "FLT":
                    sys.exit("ERROR: FLT se desplomó frente al dato anterior; "
                             "no se publica para no dañar el reporte.")
                continue
            guardar_csv(datos, CARPETA / archivo)
            abiertas = sum(1 for d in datos if d["estado"] == "Open")
            log(f"  {codigo}: {len(datos)} ocurrencias "
                f"({abiertas} Open, {len(datos) - abiertas} In Progress).")

        navegador.close()
    log("Extracción de Bitácora completa.")


if __name__ == "__main__":
    main()
