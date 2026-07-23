#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extrae las tareas de Planner desde la nube usando la "sesión virtual" de
Microsoft guardada en el secreto MS_SESION (la exporta el Mac con
exportar_sesion_ms.py cada vez que se corre "Actualizar Reporte.command").

Si la sesión expiró o no existe, NO falla la corrida: se conserva el
planner_raw.txt anterior y queda el aviso en el log.
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

# Lee las tareas del plan desde el estado de React. Microsoft (jul-2026)
# renombró el arreglo de tareas a 'allTasks'/'rows' (antes 'rowData') y el
# campo de completado a 'finishDateTime' (antes 'completedDateTime').
# Devuelve {ok, tareas, total} si encontró el arreglo, o null si no.
JS_LEER_PLANNER = r"""
() => {
  // Preferir 'allTasks' (conjunto COMPLETO del plan) sobre 'rows' (solo las
  // visibles). Se recorren todos los fibers buscando allTasks primero.
  const util = a => a && a.length && a[0] &&
                    typeof a[0].displayName === 'string';
  let all = null, rowsArr = null;
  for (const el of document.querySelectorAll(
         '[role="row"],[role="grid"],[role="treegrid"],div')) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    if (!key) continue;
    let fib = el[key], hops = 0;
    while (fib && hops < 40) {
      const p = fib.memoizedProps;
      if (p) {
        if (!all && Array.isArray(p.allTasks) && util(p.allTasks)) all = p.allTasks;
        if (!rowsArr && Array.isArray(p.rows) && util(p.rows)) rowsArr = p.rows;
      }
      if (all) break;
      fib = fib.return; hops++;
    }
    if (all) break;
  }
  const arr = all || rowsArr;
  if (!arr) return null;
  const f = d => {
    if (!d) return '';
    const m = JSON.stringify(d).match(/\d{4}-\d{2}-\d{2}/);
    return m ? m[0].slice(2) : '';
  };
  const tareas = arr
    .filter(t => t && /O\d{1,5}-\d{2}/.test(t.displayName || ''))
    .map(t => t.displayName.match(/O\d{1,5}-\d{2}/)[0] + '~' +
         f(t.dueDateTime) + '~' +
         (t.percentComplete === 100 ? 'C' :
          t.percentComplete > 0 ? 'E' : 'N') +
         '~' + f(t.finishDateTime));
  return { ok: true, tareas, total: arr.length };
}
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def salir_suave(motivo):
    log(f"AVISO: {motivo}")
    log("Se conserva la última foto de Planner (planner_raw.txt del repo). "
        "Para renovarla: correr 'Actualizar Reporte.command' en el Mac.")
    # marca para que el workflow avise (crea un issue en GitHub) que la
    # sesión de Planner necesita renovarse
    (CARPETA / "_planner_expirado.flag").write_text(motivo, encoding="utf-8")
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

        # Detección + extracción robustas. El arreglo de tareas vive ahora en
        # los props de React 'allTasks'/'rows' (antes 'rowData'); las tareas
        # completadas usan 'finishDateTime' (antes 'completedDateTime'). El
        # JS devuelve {ok, tareas, total} si halló el arreglo, o null si no.
        js_leer = JS_LEER_PLANNER
        fin = time.time() + 150
        res = None
        while time.time() < fin:
            try:
                res = page.evaluate(js_leer)
                if res and res.get("ok"):
                    break
                res = None
                if page.locator("input[type=password], #i0116").count():
                    break                     # redirigió al login: sesión muerta
            except Exception:
                res = None
            time.sleep(3)
        navegador.close()
        ruta_estado.unlink(missing_ok=True)

    if not res or not res.get("ok"):
        salir_suave("la sesión de Microsoft expiró o fue rechazada "
                    "(no se pudieron leer las tareas de Planner).")
    tareas = res["tareas"]
    log(f"Planner leído: {res['total']} tareas en el plan, "
        f"{len(tareas)} con ID de ocurrencia O####-##.")
    # sesión válida: quitar la marca de expiración si existía (no es expiración)
    flag = CARPETA / "_planner_expirado.flag"
    if flag.exists():
        flag.unlink()
    if tareas:
        (CARPETA / "planner_raw.txt").write_text(" ".join(tareas),
                                                 encoding="utf-8")
        log(f"Planner actualizado desde la nube: {len(tareas)} tareas con ID.")
    else:
        log(f"AVISO: el plan tiene {res['total']} tareas pero NINGUNA usa el "
            "formato O####-##. Se conserva la última foto. Probablemente las "
            "tareas ahora usan otro identificador (p.ej. YY/AI/####); hay que "
            "revisar la llave de cruce con Bitácora.")


if __name__ == "__main__":
    main()
