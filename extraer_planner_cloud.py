#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extrae las tareas de Planner desde la nube (GitHub Actions) usando la
"sesión virtual" de Microsoft guardada en el secreto MS_SESION (creada por
exportar_sesion_ms.py en el Mac).

Si la sesión expiró o no existe, NO falla la corrida: conserva el
planner_raw.txt anterior y deja el aviso en el log.
"""

import base64
import gzip
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CARPETA = Path(__file__).resolve().parent
URL_PLANNER = ("https://planner.cloud.microsoft/webui/plan/"
               "yC2AO9h21ku3dDlygaHj6WQAAHdx/view/grid"
               "?tid=a2addd3e-8397-4579-ba30-7a38803fc3bf")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def salir_suave(motivo):
    log(f"AVISO: {motivo}")
    log("Se conserva la última foto de Planner (planner_raw.txt del repo). "
        "Para renovar la sesión: correr 'Actualizar Reporte.command' en el Mac.")
    sys.exit(0)


def main():
    empacado = os.environ.get("MS_SESION", "").strip()
    if not empacado:
        salir_suave("no hay secreto MS_SESION configurado.")
    try:
        estado = json.loads(gzip.decompress(base64.b64decode(empacado)))
    except Exception as e:
        salir_suave(f"el secreto MS_SESION no se pudo decodificar ({e}).")

    ruta_estado = CARPETA / "_sesion_ms.json"
    ruta_estado.write_text(json.dumps(estado), encoding="utf-8")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True)
        ctx = navegador.new_context(storage_state=str(ruta_estado),
                                    viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        log("Abriendo Planner con la sesión virtual…")
        page.goto(URL_PLANNER, wait_until="domcontentloaded", timeout=120_000)

        fin = time.time() + 150
        listo = False
        while time.time() < fin:
            try:
                if page.evaluate("() => document.querySelectorAll("
                                 "'[role=\"row\"].grid-row').length > 0"):
                    listo = True
                    break
                if page.locator("input[type=password], #i0116").count():
                    break                     # redirigió al login: sesión muerta
            except Exception:
                pass
            time.sleep(3)
        if not listo:
            navegador.close()
            ruta_estado.unlink(missing_ok=True)
            salir_suave("la sesión de Microsoft expiró o fue rechazada "
                        "(no cargó la grilla de Planner).")

        time.sleep(3)
        tareas = page.evaluate("""() => {
            const row = document.querySelector('[role="row"].grid-row');
            const key = Object.keys(row).find(k => k.startsWith('__reactFiber$'));
            let fib = row[key];
            while (fib) {
                const p = fib.memoizedProps;
                if (p && Array.isArray(p.rowData)) break;
                fib = fib.return;
            }
            if (!fib) return null;
            const f = d => {
                if (!d) return '';
                const m = JSON.stringify(d).match(/\\d{4}-\\d{2}-\\d{2}/);
                return m ? m[0].slice(2) : '';
            };
            return fib.memoizedProps.rowData
                .filter(t => /O\\d{1,5}-\\d{2}/.test(t.displayName || ''))
                .map(t => t.displayName.match(/O\\d{1,5}-\\d{2}/)[0] + '~' +
                     f(t.dueDateTime) + '~' +
                     (t.percentComplete === 100 ? 'C' :
                      t.percentComplete > 0 ? 'E' : 'N') +
                     '~' + f(t.completedDateTime));
        }""")
        navegador.close()
        ruta_estado.unlink(missing_ok=True)

    if not tareas:
        salir_suave("no se pudieron leer las tareas del plan.")
    (CARPETA / "planner_raw.txt").write_text(" ".join(tareas), encoding="utf-8")
    log(f"Planner actualizado desde la nube: {len(tareas)} tareas con ID.")


if __name__ == "__main__":
    main()
