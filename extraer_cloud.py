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
    Path(ruta).write_text("\n".join(lineas), encoding="utf-8")


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
                const fijar = (sel, v) => {{
                    const e = document.querySelector(sel);
                    if (e) e.value = v;
                }};
                fijar('#Criteria_FromDate', '{HOY.year}-01-01');
                fijar('#CriteriaDateStartFromPicker', '01/01/{HOY.year}');
                fijar('#Criteria_ToDate', '{HOY.isoformat()}');
                fijar('#CriteriaDateStartToPicker', '{HOY.strftime("%d/%m/%Y")}');
                const txt = document.querySelector('[id="Criteria_Text"]');
                if (txt) txt.value = '';
                const num = document.querySelector('[id="Criteria_OccurrenceNo"]');
                if (num) num.value = '';
                occurrencesSearchClicked();   // guarda los criterios en el servidor
            }}""")

            datos = []
            for intento in range(8):
                time.sleep(8 if intento == 0 else 6)
                # De a 500 filas por página: pedir más de 1000 de un golpe
                # devuelve 200 con la lista VACÍA cuando el tipo supera esas
                # 1000 ocurrencias (le pasaba a GRH, que ronda las 1400).
                todas = page.evaluate("""async () => {
                    const limpio = v => (v === null || v === undefined || v === 'null')
                                        ? '' : String(v);
                    const vistos = new Set();
                    const todas = [];
                    let records = null;
                    for (let pagina = 1; pagina <= 40; pagina++) {
                        const ctl = new AbortController();
                        setTimeout(() => ctl.abort(), 120000);
                        const r = await fetch('/AQDPortal/safety.aspx/Home/SearchOccurrencesList' +
                            '?withOccTypes=True&_search=false&rows=500&page=' + pagina +
                            '&sidx=OccurrenceDate&sord=desc',
                            {headers: {'Accept': 'application/json'}, signal: ctl.signal});
                        const j = await r.json();
                        const filas = j.rows || [];
                        if (!filas.length) break;
                        if (j.records != null) records = j.records;
                        let nuevas = 0;
                        for (const f of filas) {
                            const c = f.cell || f;
                            const id = limpio(c.OccurrenceID);
                            if (vistos.has(id)) continue;   // la última se repite
                            vistos.add(id);
                            nuevas++;
                            todas.push({id: id,
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
                        if (!nuevas) break;
                        if (records != null && todas.length >= records) break;
                    }
                    return todas;
                }""")
                datos = [d for d in todas
                         if any(a.upper() in d["tipo"].upper() for a in alias)]
                if todas and len(datos) >= len(todas) * 0.5:
                    break
                log(f"  criterios aún no aplicados ({len(datos)}/{len(todas)}); "
                    "reintentando…")
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
            guardar_csv(datos, CARPETA / archivo)
            abiertas = sum(1 for d in datos if d["estado"] == "Open")
            log(f"  {codigo}: {len(datos)} ocurrencias "
                f"({abiertas} Open, {len(datos) - abiertas} In Progress).")

        navegador.close()
    log("Extracción de Bitácora completa.")


if __name__ == "__main__":
    main()
