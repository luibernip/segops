#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convierte los datos crudos extraídos del navegador a los CSV estándar
que consume comparar_bitacora_planner.py:

  bitacora_real.csv (';')  ->  bitacora.csv
  planner_raw.txt   ('~')  ->  planner.csv
"""
import csv
from pathlib import Path

CARPETA = Path(__file__).resolve().parent

INVESTIGACIONES = {
    "LS": "Logged for Statistics",
    "AO": "Assessment Only",
    "FI": "Full Investigation",
    "QR": "Quick Review",
    "": "",
}
PROGRESOS = {"C": "Completada", "E": "En curso", "N": "No iniciada"}
ESTADOS = {"O": "Open", "P": "In Progress"}


def riesgo_largo(codigo):
    """'Ac4' -> 'Acceptable (4)', 'To20' -> 'Tolerable (20)', etc."""
    import re
    if not codigo:
        return ""
    if "(" in codigo or " " in codigo:
        return codigo                  # ya viene en formato largo
    m = re.match(r"([A-Za-z]+)\s*(\d+)?", codigo)
    if not m:
        return codigo
    nombre = {"Ac": "Acceptable", "To": "Tolerable", "Un": "Unacceptable",
              "Rv": "Review"}.get(m.group(1), m.group(1))
    return f"{nombre} ({m.group(2)})" if m.group(2) else nombre


def fecha_larga(yy_mm_dd):
    """'26-07-10' (yy-mm-dd) -> '10/07/2026' (dd/mm/yyyy)"""
    if not yy_mm_dd:
        return ""
    yy, mm, dd = yy_mm_dd.split("-")
    return f"{dd}/{mm}/20{yy}"


def preparar_bitacora(nombre_origen="bitacora_real.csv",
                      nombre_destino="bitacora.csv", opcional=False):
    origen = CARPETA / nombre_origen
    destino = CARPETA / nombre_destino
    if opcional and not origen.exists():
        print(f"{nombre_origen} no existe; se omite.")
        return
    with open(origen, encoding="utf-8") as f:
        filas = list(csv.reader(f, delimiter=";"))
    tiene_reg = "registrada" in ";".join(filas[0])
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Occurrence Number", "Occurrence Date", "Registered On",
                    "State", "Investigation Type", "Risk", "Title", "Reg Mark"])
        for fila in filas[1:]:
            if len(fila) < 7:
                continue
            if not tiene_reg:               # formato viejo de 7 columnas
                fila = fila[:2] + [""] + fila[2:]
            oid, fecha, reg, estado, inv, riesgo = fila[:6]
            titulo = ";".join(fila[6:-1])   # por si el título trae ';'
            mat = fila[-1]
            w.writerow([oid, fecha, reg, ESTADOS.get(estado, estado),
                        INVESTIGACIONES.get(inv, inv), riesgo_largo(riesgo),
                        titulo, mat])
    print(f"{nombre_destino}: {len(filas) - 1} ocurrencias")


def preparar_planner():
    origen = CARPETA / "planner_raw.txt"
    destino = CARPETA / "planner.csv"
    tokens = origen.read_text(encoding="utf-8").split()
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Task Name", "Due Date", "Progress", "Completed Date"])
        for t in tokens:
            oid, due, prog, comp = (t.split("~") + [""] * 4)[:4]
            w.writerow([oid, fecha_larga(due), PROGRESOS.get(prog, prog),
                        fecha_larga(comp)])
    print(f"planner.csv: {len(tokens)} tareas")


if __name__ == "__main__":
    preparar_bitacora()
    preparar_bitacora("bitacora_cbn_real.csv", "bitacora_cbn.csv", opcional=True)
    preparar_bitacora("bitacora_frm_real.csv", "bitacora_frm.csv", opcional=True)
    preparar_planner()
