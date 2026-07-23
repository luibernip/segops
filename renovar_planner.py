#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Renueva la "sesión virtual" de Microsoft para que la nube pueda refrescar
Planner. Abre el perfil automático, espera a que inicies sesión (detección
robusta que NO depende de un selector fijo: lee directamente los datos de
las tareas desde React), y sube la sesión como secreto de GitHub.

Uso:  python3 renovar_planner.py
"""

import base64
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CARPETA = Path(__file__).resolve().parent
PERFIL = CARPETA / ".perfil_navegador"
URL_PLANNER = ("https://planner.cloud.microsoft/webui/plan/"
               "yC2AO9h21ku3dDlygaHj6WQAAHdx/view/grid"
               "?tid=a2addd3e-8397-4579-ba30-7a38803fc3bf")
REPO = "luibernip/segops"
GH = "/opt/homebrew/bin/gh"
ESPERA_MAX_SEG = 600           # hasta 10 minutos para que inicies sesión

DOMINIOS_COOKIES = ("microsoftonline", "cloud.microsoft", "msauth",
                    "msftauth", "planner")
DOMINIOS_ORIGENES = ("cloud.microsoft",)

# Detección robusta: recorre los fibers de React buscando el arreglo de
# tareas (rowData con IDs O####-##). Sirve aunque cambien las clases del DOM.
JS_LEER_TAREAS = r"""
() => {
  const cand = document.querySelectorAll(
    '[role="row"], [role="grid"], [role="treegrid"], [class*="row" i], [class*="grid" i], div');
  const vistos = new Set();
  for (const el of cand) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    if (!key) continue;
    let fib = el[key], hops = 0;
    while (fib && hops < 40) {
      const p = fib.memoizedProps;
      if (p && Array.isArray(p.rowData) &&
          p.rowData.some(t => /O\d{1,5}-\d{2}/.test((t && t.displayName) || ''))) {
        const f = d => {
          if (!d) return '';
          const m = JSON.stringify(d).match(/\d{4}-\d{2}-\d{2}/);
          return m ? m[0].slice(2) : '';
        };
        return p.rowData
          .filter(t => /O\d{1,5}-\d{2}/.test((t && t.displayName) || ''))
          .map(t => t.displayName.match(/O\d{1,5}-\d{2}/)[0] + '~' +
               f(t.dueDateTime) + '~' +
               (t.percentComplete === 100 ? 'C' :
                t.percentComplete > 0 ? 'E' : 'N') +
               '~' + f(t.completedDateTime));
      }
      fib = fib.return; hops++;
    }
  }
  return null;
}
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    if not PERFIL.exists():
        sys.exit("No existe .perfil_navegador. Corre primero una "
                 "actualización normal para crear el perfil.")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PERFIL), headless=False,
            viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("Abriendo Planner… inicia sesión SI te lo pide EN ESTA ventana.")
        page.goto(URL_PLANNER, wait_until="domcontentloaded", timeout=120_000)

        tareas = None
        inicio = time.time()
        aviso = False
        while time.time() - inicio < ESPERA_MAX_SEG:
            try:
                tareas = page.evaluate(JS_LEER_TAREAS)
            except Exception:
                tareas = None
            if tareas:
                break
            if not aviso and time.time() - inicio > 6:
                log(">>> Si ves una pantalla de Microsoft, INICIA SESIÓN en "
                    "ESTA ventana (no en tu Brave normal). Esperando…")
                aviso = True
            time.sleep(3)

        if not tareas:
            ctx.close()
            sys.exit("No se pudieron leer las tareas de Planner (¿no se "
                     "completó el inicio de sesión?). Vuelve a intentar.")

        log(f"Planner reconocido: {len(tareas)} tareas leídas. "
            "Guardando la sesión…")
        estado = ctx.storage_state()
        ctx.close()

    estado["cookies"] = [c for c in estado.get("cookies", [])
                         if any(d in c.get("domain", "")
                                for d in DOMINIOS_COOKIES)]
    estado["origins"] = [o for o in estado.get("origins", [])
                         if any(d in o.get("origin", "")
                                for d in DOMINIOS_ORIGENES)]

    def empacar():
        return base64.b64encode(gzip.compress(json.dumps(estado).encode(), 9))

    empacado = empacar()
    if len(empacado) > 40_000:
        estado["origins"] = []          # aún grande: bastan las cookies
        empacado = empacar()

    # subir a GitHub, partido en hasta 3 secretos si hace falta (48 KB c/u)
    pedazos = [empacado[i:i + 40_000] for i in range(0, len(empacado), 40_000)]
    if len(pedazos) > 3:
        sys.exit("La sesión es demasiado grande incluso partida.")
    nombres = ["MS_SESION", "MS_SESION_2", "MS_SESION_3"]
    for i, nombre in enumerate(nombres):
        if i < len(pedazos):
            subprocess.run([GH, "secret", "set", nombre, "-R", REPO],
                           input=pedazos[i], check=True)
        else:
            subprocess.run([GH, "secret", "delete", nombre, "-R", REPO],
                           capture_output=True)
    log(f"✔ Sesión de Planner renovada y subida ({len(pedazos)} secreto(s)). "
        "La nube volverá a refrescar Planner en la próxima corrida.")


if __name__ == "__main__":
    main()
