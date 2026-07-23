#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnóstico: abre Planner con el perfil ya autenticado e informa DÓNDE están
ahora los datos de las tareas (Microsoft cambió la estructura). No sube nada;
solo imprime un reporte para ajustar el extractor.
"""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

CARPETA = Path(__file__).resolve().parent
PERFIL = CARPETA / ".perfil_navegador"
URL = ("https://planner.cloud.microsoft/webui/plan/"
       "yC2AO9h21ku3dDlygaHj6WQAAHdx/view/grid"
       "?tid=a2addd3e-8397-4579-ba30-7a38803fc3bf")

JS = r"""
() => {
  const out = {
    url: location.href,
    hasPassword: !!document.querySelector('input[type=password], #i0116'),
    rowsRole: document.querySelectorAll('[role="row"]').length,
    gridRole: document.querySelectorAll('[role="grid"],[role="treegrid"]').length,
    gridcell: document.querySelectorAll('[role="gridcell"]').length,
    bodyLen: (document.body.innerText || '').length
  };
  // muestra de texto de filas visibles
  const filas = [...document.querySelectorAll('[role="row"]')].slice(1, 4)
    .map(r => (r.innerText || '').replace(/\s+/g,' ').trim().slice(0, 120));
  out.muestraFilas = filas;
  // ¿aparece algún ID O####-## en el texto de la página?
  const ids = (document.body.innerText || '').match(/O\d{1,5}-\d{2}/g);
  out.idsEnTexto = ids ? ids.slice(0, 5) : [];
  out.totalIdsEnTexto = ids ? ids.length : 0;

  // escanear fibers de React buscando arrays de objetos
  const conTarea = [], otros = [], seen = new Set();
  let scanned = 0;
  for (const el of document.querySelectorAll('*')) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    if (!key) continue;
    let fib = el[key], hops = 0;
    while (fib && hops < 20) {
      const props = fib.memoizedProps;
      if (props && typeof props === 'object') {
        for (const [k, v] of Object.entries(props)) {
          if (Array.isArray(v) && v.length && v[0] && typeof v[0] === 'object') {
            const sig = k + ':' + v.length;
            if (!seen.has(sig)) {
              seen.add(sig);
              const s = JSON.stringify(v[0]);
              const hasTask = v.some(it => it &&
                /O\d{1,5}-\d{2}/.test(JSON.stringify(
                  (it.displayName||it.title||it.name||it.text||''))));
              const rec = {prop: k, len: v.length,
                           keys: Object.keys(v[0]).slice(0, 18)};
              if (hasTask) conTarea.push(rec);
              else if (otros.length < 15) otros.push(rec);
            }
          }
        }
      }
      fib = fib.return; hops++;
    }
    if (++scanned > 5000) break;
  }
  out.arraysConTarea = conTarea;
  out.otrosArrays = otros;
  return out;
}
"""


def main():
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PERFIL), headless=False,
            viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(12)          # dar tiempo a que la grilla cargue
        rep = page.evaluate(JS)
        ctx.close()
    print("\n===== REPORTE DIAGNÓSTICO PLANNER =====")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print("===== FIN =====\n")


if __name__ == "__main__":
    main()
