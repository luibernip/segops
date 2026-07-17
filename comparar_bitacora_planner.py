#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comparador Bitácora (AQD Portal) vs Microsoft Planner (Tasks)
=============================================================

Compara las ocurrencias exportadas desde el AQD Portal
(https://bitacora.avianca.com/AQDPortal/safety.aspx con filtros:
Text: AVA-, Occurrence type: FLT, State: All Active, Date: Year to date)
contra las tareas exportadas desde Microsoft Planner, y genera un
reporte HTML interactivo con la paleta de colores de Avianca.

CÓMO OBTENER LOS DATOS
----------------------
1) Bitácora (AQD Portal):
   - Entrar a safety.aspx, aplicar los filtros indicados en "Search Occurrence"
     (Text: AVA-, Occurrence type: FLT, State: All Active, Date: Year to date).
   - Exportar los resultados a Excel/CSV y guardarlo como "bitacora.xlsx"
     (o "bitacora.csv") en esta misma carpeta.

2) Planner (Tasks):
   - Abrir el plan en Planner -> menú "..." -> "Exportar plan a Excel".
   - Guardar el archivo como "planner.xlsx" en esta misma carpeta.

USO
---
    python3 comparar_bitacora_planner.py
    python3 comparar_bitacora_planner.py --bitacora otra_ruta.xlsx --planner plan.xlsx
    python3 comparar_bitacora_planner.py --demo      # genera datos de ejemplo y el reporte

Requiere: pandas, openpyxl   (pip install pandas openpyxl)

Salida: reporte_bitacora_vs_tasks.html
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

CARPETA = Path(__file__).resolve().parent

ARCHIVO_BITACORA = CARPETA / "bitacora.xlsx"     # también acepta .csv
ARCHIVO_PLANNER  = CARPETA / "planner.xlsx"      # exportación nativa de Planner
ARCHIVO_SALIDA   = CARPETA / "reporte_bitacora_vs_tasks.html"

# Patrón para identificar el número de evento (llave de cruce entre sistemas).
# En AQD el ID de ocurrencia es "O####-##" y las tareas de Planner lo incluyen
# al inicio del nombre (p.ej. "O14466-26 AVA- GPWS PULL UP AT CUC").
PATRON_EVENTO = re.compile(r"O\d{1,5}-\d{2}", re.IGNORECASE)

# COAs (certificados de operación) de Avianca: se detectan por el prefijo
# del título de la ocurrencia (p.ej. "GLG- GO AROUND AT UIO ...").
COAS = ["AVA", "GLG", "TAI", "LRC", "AVR", "TPA", "TNO", "GUG"]
PATRON_COA = re.compile(r"^\s*(" + "|".join(COAS) + r")\b", re.IGNORECASE)

# Días máximos que una ocurrencia puede llevar abierta (desde su registro)
# según el tipo de investigación; al excederlos se marca en rojo.
LIMITES_DIAS = {
    "Full Investigation":    20,
    "Assessment Only":       15,
    "Logged for Statistics":  3,
}
LIMITE_DIAS_SIN_INV = 30            # sin investigación asignada (u otros)

# Si la detección automática de columnas falla, defina aquí el nombre exacto
# de cada columna de su exportación (deje None para autodetectar).
MAPEO_BITACORA = {
    "evento":       None,   # p.ej. "Occurrence Number"
    "fecha":        None,   # p.ej. "Occurrence Date"
    "estado":       None,   # p.ej. "State"
    "investigacion": None,  # p.ej. "Investigation Type"
    "tipo":         None,   # p.ej. "Occurrence Type"
    "descripcion":  None,   # p.ej. "Title" / "Description"
}
MAPEO_PLANNER = {
    "tarea":       None,    # p.ej. "Nombre de la tarea" / "Task Name"
    "bucket":      None,    # p.ej. "Nombre del depósito" / "Bucket Name"
    "progreso":    None,    # p.ej. "Progreso" / "Progress"
    "vencimiento": None,    # p.ej. "Fecha de vencimiento" / "Due Date"
    "finalizacion": None,   # p.ej. "Fecha de finalización" / "Completed Date"
    "asignado":    None,    # p.ej. "Asignado a" / "Assigned To"
}

# Paleta Avianca (orden: 1 rojo, 2 gris, 3 azul oscuro, 4 azul claro)
COLORES = {
    "rojo":        "#D6001C",
    "rojo_oscuro": "#A50113",
    "negro":       "#54565B",   # gris oscuro (reemplaza el negro)
    "azul_oscuro": "#1F4E79",
    "azul_claro":  "#8DB4E2",
    "gris":        "#797B80",
    "gris_claro":  "#F4F4F4",
    "blanco":      "#FFFFFF",
    "dorado":      "#C8A96A",
}

HOY = date.today()

# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------

def _norm(texto):
    """Minúsculas y sin tildes, para comparar nombres de columnas."""
    s = str(texto).strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def buscar_columna(df, candidatos, manual=None):
    """Devuelve el nombre real de la primera columna que coincida."""
    if manual:
        if manual in df.columns:
            return manual
        raise SystemExit(f"ERROR: la columna configurada '{manual}' no existe. "
                         f"Columnas disponibles: {list(df.columns)}")
    normalizadas = {_norm(c): c for c in df.columns}
    for cand in candidatos:                       # coincidencia exacta primero
        if _norm(cand) in normalizadas:
            return normalizadas[_norm(cand)]
    for cand in candidatos:                       # luego coincidencia parcial
        for norm, real in normalizadas.items():
            if _norm(cand) in norm:
                return real
    return None


def extraer_evento(texto):
    """Extrae y normaliza el identificador de ocurrencia (O####-##)."""
    if pd.isna(texto):
        return None
    m = PATRON_EVENTO.search(str(texto))
    return m.group(0).upper() if m else None


def extraer_coa(titulo):
    """Deduce el COA a partir del prefijo del título de la ocurrencia."""
    m = PATRON_COA.match(str(titulo))
    return m.group(1).upper() if m else "Otros"


def categoria_riesgo(riesgo):
    """'Tolerable (20)' -> 'Tolerable'; vacío -> 'Sin riesgo asignado'."""
    r = str(riesgo).strip().lower()
    if not r or r == "nan":
        return "Sin riesgo asignado"
    for cat in ("Intolerable", "Unacceptable", "Tolerable", "Acceptable"):
        if r.startswith(cat.lower()):
            return cat
    return riesgo


def leer_tabla(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        raise SystemExit(f"ERROR: no se encontró el archivo {ruta}\n"
                         "Exporte los datos según las instrucciones del "
                         "encabezado del script, o ejecute con --demo.")
    if ruta.suffix.lower() == ".csv":
        return pd.read_csv(ruta)
    return pd.read_excel(ruta)


# ---------------------------------------------------------------------------
# CARGA DE DATOS
# ---------------------------------------------------------------------------

def cargar_bitacora(ruta, filtrar_ytd=True):
    df = leer_tabla(ruta)
    col_ev  = buscar_columna(df, ["Occurrence Number", "Occurrence", "Event",
                                  "Number", "Evento", "Numero", "Text", "ID"],
                             MAPEO_BITACORA["evento"])
    col_fec = buscar_columna(df, ["Occurrence Date", "Event Date", "Date",
                                  "Fecha"], MAPEO_BITACORA["fecha"])
    col_reg = buscar_columna(df, ["Registered On", "Registrada", "Registered"])
    col_est = buscar_columna(df, ["State", "Status", "Estado"],
                             MAPEO_BITACORA["estado"])
    col_inv = buscar_columna(df, ["Investigation Type", "Investigation",
                                  "Inv Type", "Tipo de investigacion",
                                  "Investigacion"], MAPEO_BITACORA["investigacion"])
    col_tip = buscar_columna(df, ["Occurrence Type", "Type", "Tipo"],
                             MAPEO_BITACORA["tipo"])
    col_des = buscar_columna(df, ["Title", "Description", "Descripcion",
                                  "Titulo", "Summary"], MAPEO_BITACORA["descripcion"])
    col_rie = buscar_columna(df, ["Risk", "Riesgo", "Risk Level"])

    if col_ev is None or col_est is None:
        raise SystemExit("ERROR: no se pudieron identificar las columnas de "
                         f"evento/estado en la Bitácora. Columnas: {list(df.columns)}\n"
                         "Configure MAPEO_BITACORA en el script.")

    out = pd.DataFrame({
        "Evento":        df[col_ev].map(extraer_evento),
        "Fecha":         pd.to_datetime(df[col_fec], errors="coerce", dayfirst=True) if col_fec else pd.NaT,
        "Registrada":    pd.to_datetime(df[col_reg], errors="coerce", dayfirst=True) if col_reg else pd.NaT,
        "Estado":        df[col_est].astype(str).str.strip(),
        "Investigación": df[col_inv].astype(str).str.strip() if col_inv else "",
        "Tipo":          df[col_tip].astype(str).str.strip() if col_tip else "",
        "Descripción":   df[col_des].astype(str).str.strip() if col_des else "",
        "Riesgo":        df[col_rie].fillna("").astype(str).str.strip().replace("nan", "") if col_rie else "",
    })
    out = out.dropna(subset=["Evento"]).drop_duplicates(subset=["Evento"])

    # Los NaN convertidos a texto ("nan", "none"...) se muestran en blanco
    for col in ["Estado", "Investigación", "Tipo", "Descripción", "Riesgo"]:
        out[col] = out[col].replace(
            {"nan": "", "None": "", "none": "", "null": "", "NaN": ""})

    out["COA"] = out["Descripción"].map(extraer_coa)
    out["Categoría riesgo"] = out["Riesgo"].map(categoria_riesgo)
    # Días que lleva abierta la ocurrencia, contados desde su REGISTRO en el
    # sistema (Registered On); si falta, se usa la fecha del evento.
    base = out["Registrada"].fillna(out["Fecha"])
    out["Días abierta"] = (pd.Timestamp(HOY) - base).dt.days
    # Umbral de alerta según el tipo de investigación
    out["Límite días"] = out["Investigación"].map(LIMITES_DIAS) \
                            .fillna(LIMITE_DIAS_SIN_INV)

    # Reaplicar defensivamente los filtros de la búsqueda
    if col_tip:
        con_tipo = out["Tipo"].str.upper().str.contains("FLT", na=False)
        if con_tipo.any():
            out = out[con_tipo]
    if filtrar_ytd and col_fec and out["Fecha"].notna().any():
        out = out[(out["Fecha"].isna()) |
                  (out["Fecha"] >= pd.Timestamp(HOY.year, 1, 1))]

    vacios = {"", "nan", "none", "null", "n/a", "-"}
    out["Sin investigación"] = out["Investigación"].str.lower().isin(vacios)
    out["Abierto"] = out["Estado"].str.upper().str.contains("OPEN", na=False)
    return out.reset_index(drop=True)


def cargar_planner(ruta):
    """Lee la exportación de Planner (salta las filas de metadatos iniciales)."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise SystemExit(f"ERROR: no se encontró el archivo {ruta}\n"
                         "En Planner use '...' -> 'Exportar plan a Excel', "
                         "o ejecute con --demo.")
    if ruta.suffix.lower() == ".csv":
        crudo = pd.read_csv(ruta, header=None, dtype=str)
    else:
        crudo = pd.read_excel(ruta, header=None, dtype=str)

    # Buscar la fila de encabezados (contiene "tarea"/"task")
    fila_header = 0
    for i in range(min(10, len(crudo))):
        fila = " ".join(_norm(v) for v in crudo.iloc[i].dropna())
        if "task" in fila or "tarea" in fila:
            fila_header = i
            break
    df = crudo.iloc[fila_header + 1:].copy()
    df.columns = [str(c).strip() for c in crudo.iloc[fila_header]]
    df = df.dropna(how="all")

    col_tar = buscar_columna(df, ["Nombre de la tarea", "Task Name", "Tarea",
                                  "Task", "Title"], MAPEO_PLANNER["tarea"])
    col_buc = buscar_columna(df, ["Nombre del deposito", "Bucket Name",
                                  "Bucket", "Deposito"], MAPEO_PLANNER["bucket"])
    col_pro = buscar_columna(df, ["Progreso", "Progress", "Estado"],
                             MAPEO_PLANNER["progreso"])
    col_ven = buscar_columna(df, ["Fecha de vencimiento", "Due Date",
                                  "Vencimiento", "Due"], MAPEO_PLANNER["vencimiento"])
    col_fin = buscar_columna(df, ["Fecha de finalizacion", "Completed Date",
                                  "Finalizacion"], MAPEO_PLANNER["finalizacion"])
    col_asi = buscar_columna(df, ["Asignado a", "Assigned To", "Asignado"],
                             MAPEO_PLANNER["asignado"])

    if col_tar is None:
        raise SystemExit("ERROR: no se identificó la columna del nombre de la "
                         f"tarea en Planner. Columnas: {list(df.columns)}\n"
                         "Configure MAPEO_PLANNER en el script.")

    out = pd.DataFrame({
        "Tarea":        df[col_tar].astype(str).str.strip(),
        "Evento":       df[col_tar].map(extraer_evento),
        "Bucket":       df[col_buc].astype(str).str.strip() if col_buc else "",
        "Progreso":     df[col_pro].astype(str).str.strip() if col_pro else "",
        "Vencimiento":  pd.to_datetime(df[col_ven], errors="coerce", dayfirst=True) if col_ven else pd.NaT,
        "Finalización": pd.to_datetime(df[col_fin], errors="coerce", dayfirst=True) if col_fin else pd.NaT,
        "Asignado a":   df[col_asi].astype(str).str.strip() if col_asi else "",
    })
    out = out[out["Tarea"].str.lower() != "nan"]

    prog = out["Progreso"].str.lower()
    out["Completada"] = (prog.str.contains("complet", na=False) |
                         out["Finalización"].notna())
    out["Vencida"] = (~out["Completada"] &
                      out["Vencimiento"].notna() &
                      (out["Vencimiento"] < pd.Timestamp(HOY)))
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# COMPARACIONES
# ---------------------------------------------------------------------------

def comparar(bit, pla):
    # Las tareas completadas se excluyen del análisis; solo cuentan las
    # activas (No iniciada / En curso). La excepción es el punto 4, que por
    # definición examina las completadas.
    activas = pla[~pla["Completada"]]
    eventos_task = set(activas["Evento"].dropna())

    r = {}
    # 1. OPEN en Bitácora sin ningún tipo de investigación
    r["open_sin_inv"] = bit[bit["Abierto"] & bit["Sin investigación"]]

    # 2. En Bitácora pero sin tarea activa en Tasks
    r["bit_sin_task"] = bit[~bit["Evento"].isin(eventos_task)]

    # 3. Con tarea activa en Tasks pero sin tipo de investigación en Bitácora
    sin_inv = set(bit.loc[bit["Sin investigación"], "Evento"])
    r["task_sin_inv"] = activas[activas["Evento"].isin(sin_inv)]

    # 4. Completadas en Tasks pero el evento sigue SIN CERRAR en Bitácora
    #    (activo = Open o In Progress; bit ya viene filtrado a All Active)
    activos_bit = set(bit["Evento"].dropna())
    r["task_comp_bit_open"] = pla[pla["Completada"] & pla["Evento"].isin(activos_bit)]

    # 5. Vencidas en Tasks (una tarea completada nunca está vencida)
    r["task_vencidas"] = activas[activas["Vencida"]]

    # 6. OPEN en Bitácora y con tarea activa
    r["open_con_task"] = bit[bit["Abierto"] & bit["Evento"].isin(eventos_task)]

    # 6. Eventos activos sin nivel de riesgo asignado en Bitácora
    r["sin_riesgo"] = bit[bit["Categoría riesgo"] == "Sin riesgo asignado"]

    return r


# ---------------------------------------------------------------------------
# REPORTE HTML
# ---------------------------------------------------------------------------

def df_a_html(df, columnas):
    """Convierte un DataFrame en filas <tr> con formato de fechas."""
    if df.empty:
        return ('<tr><td class="vacio" colspan="{}">Sin registros ✓</td></tr>'
                .format(len(columnas)))
    filas = []
    for _, fila in df.iterrows():
        celdas = []
        for col in columnas:
            v = fila.get(col, "")
            orden = ""                      # valor auxiliar para ordenar fechas
            clase = ""
            if isinstance(v, (pd.Timestamp, datetime)):
                if pd.isna(v):
                    v = ""
                else:
                    orden = f' data-orden="{v.strftime("%Y-%m-%d %H:%M")}"'
                    v = v.strftime("%d-%b-%Y")
            elif pd.isna(v):
                v = ""
            elif isinstance(v, bool):
                v = "Sí" if v else "No"
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            if col == "Días abierta" and isinstance(v, int):
                limite = fila.get("Límite días", LIMITE_DIAS_SIN_INV)
                if pd.isna(limite):
                    limite = LIMITE_DIAS_SIN_INV
                clase = ' class="dias-alto"' if v > limite else ""
            celdas.append(f"<td{orden}{clase}>{v}</td>")
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    return "\n".join(filas)


def seccion(numero, titulo, subtitulo, df, columnas, abierta=False):
    n = len(df)
    badge = "badge-ok" if n == 0 else "badge-alerta"
    return f"""
    <details class="seccion" {'open' if abierta else ''}>
      <summary>
        <span class="num">{numero}</span>
        <span class="titulo-sec">{titulo}<small>{subtitulo}</small></span>
        <span class="badge {badge}">{n}</span>
        <span class="flecha">▾</span>
      </summary>
      <div class="tabla-wrap">
        <table>
          <thead><tr>{"".join(f"<th>{c}</th>" for c in columnas)}</tr></thead>
          <tbody>{df_a_html(df, columnas)}</tbody>
        </table>
      </div>
    </details>"""


def resumen_coa(bit):
    """Tabla resumen por COA: totales, OPEN y estado de investigación."""
    filas = []
    orden = [c for c in COAS if c in set(bit["COA"])] + \
            (["Otros"] if "Otros" in set(bit["COA"]) else [])
    for coa in orden:
        g = bit[bit["COA"] == coa]
        con_inv = int((~g["Sin investigación"]).sum())
        filas.append({
            "COA": coa, "Total": len(g),
            "OPEN": int(g["Abierto"].sum()),
            "In Progress": int((~g["Abierto"]).sum()),
            "Con investigación": con_inv,
            "Sin investigación": int(g["Sin investigación"].sum()),
            "% con investigación": f"{con_inv / len(g) * 100:.0f}%" if len(g) else "0%",
        })
    return pd.DataFrame(filas)


def generar_html(bit, pla, r, salida, extras=None):
    cols_b = ["Evento", "COA", "Fecha", "Registrada", "Días abierta",
              "Estado", "Investigación", "Riesgo", "Descripción"]
    if "Título (Bitácora)" in pla.columns:
        cols_t = ["Evento", "COA", "Título (Bitácora)", "Progreso",
                  "Vencimiento", "Finalización", "Estado Bitácora"]
    else:
        cols_t = ["Evento", "Tarea", "Bucket", "Progreso", "Vencimiento",
                  "Asignado a"]

    # Datos para los gráficos
    conteos = {
        "OPEN sin investigación":        len(r["open_sin_inv"]),
        "En Bitácora sin Task":          len(r["bit_sin_task"]),
        "Task sin investigación":        len(r["task_sin_inv"]),
        "Task completa / sin cerrar Bitácora": len(r["task_comp_bit_open"]),
        "Tasks vencidas":                len(r["task_vencidas"]),
        "OPEN con Task":                 len(r["open_con_task"]),
    }
    activas = pla[~pla["Completada"]]
    estados_bit = bit["Estado"].str.upper().value_counts().to_dict()
    prog_pla = activas["Progreso"].replace("", "Sin progreso").value_counts().to_dict()
    por_mes = {}
    if bit["Fecha"].notna().any():
        serie = bit.dropna(subset=["Fecha"]).groupby(bit["Fecha"].dt.strftime("%Y-%m")).size()
        por_mes = serie.sort_index().to_dict()

    abiertos_tot = int(bit["Abierto"].sum())
    # Cobertura sobre TODAS las ocurrencias (Open + In Progress):
    # cuántas tienen tarea activa en Planner y cuántas no
    ev_task = set(activas["Evento"].dropna())
    con_task_tot = int(bit["Evento"].isin(ev_task).sum())

    # Resumen por COA (tabla + gráfico apilado OPEN / In Progress)
    df_coa = resumen_coa(bit)
    coa_labels = df_coa["COA"].tolist()
    coa_open = df_coa["OPEN"].tolist()
    coa_prog = df_coa["In Progress"].tolist()
    # Tasks vs Bitácora por COA: cuántos eventos de Bitácora tienen tarea
    # activa en Planner y cuántos no (r["bit_sin_task"])
    sin_task_por_coa = r["bit_sin_task"]["COA"].value_counts()
    coa_sin_task = [int(sin_task_por_coa.get(c, 0)) for c in coa_labels]
    coa_con_task = [int(t) - s for t, s in zip(df_coa["Total"], coa_sin_task)]

    # Riesgo promedio por COA: promedio del puntaje numérico del riesgo
    # (p. ej. "Tolerable (20)" -> 20) contando SOLO ocurrencias con riesgo
    riesgo_num = bit["Riesgo"].str.extract(r"(\d+)", expand=False).astype(float)
    prom_riesgo = riesgo_num.groupby(bit["COA"]).mean()
    riesgo_prom = {
        "labels": coa_labels,
        "valores": [round(float(prom_riesgo[c]), 1)
                    if c in prom_riesgo.index and pd.notna(prom_riesgo[c])
                    else None for c in coa_labels],
        "n": [int(riesgo_num.notna()[bit["COA"] == c].sum())
              for c in coa_labels],
    }

    # Días promedio que lleva abierta cada ocurrencia, por COA
    prom = bit.groupby("COA")["Días abierta"].mean()
    orden_coa = [c for c in COAS if c in prom.index] + \
                [c for c in prom.index if c not in COAS]
    dias_prom = {"labels": orden_coa,
                 "valores": [round(float(prom[c]), 1) for c in orden_coa]}

    # Distribución de nivel de riesgo
    orden_riesgo = ["Intolerable", "Unacceptable", "Tolerable", "Acceptable",
                    "Sin riesgo asignado"]
    vc = bit["Categoría riesgo"].value_counts()
    riesgos = {k: int(vc[k]) for k in orden_riesgo if k in vc}
    for k in vc.index:
        if k not in riesgos:
            riesgos[k] = int(vc[k])

    # Datos por COA para el filtro interactivo de las gráficas
    por_coa = {}
    for coa in ["Todos"] + orden_coa:
        sb = bit if coa == "Todos" else bit[bit["COA"] == coa]
        sa = activas if coa == "Todos" else activas[activas["COA"] == coa]
        rc = {k: (df if coa == "Todos" else df[df["COA"] == coa])
              for k, df in r.items()}
        vc2 = sb["Categoría riesgo"].value_counts()
        rg = {k: int(vc2[k]) for k in orden_riesgo if k in vc2}
        for k in vc2.index:
            if k not in rg:
                rg[k] = int(vc2[k])
        pm = {}
        if sb["Fecha"].notna().any():
            pm = (sb.dropna(subset=["Fecha"])
                    .groupby(sb["Fecha"].dt.strftime("%Y-%m")).size()
                    .sort_index().to_dict())
        con_sub = int(sb["Evento"].isin(ev_task).sum())
        por_coa[coa] = {
            "conteos": {
                "OPEN sin investigación":        len(rc["open_sin_inv"]),
                "En Bitácora sin Task":          len(rc["bit_sin_task"]),
                "Task sin investigación":        len(rc["task_sin_inv"]),
                "Task completa / Bitácora OPEN": len(rc["task_comp_bit_open"]),
                "Tasks vencidas":                len(rc["task_vencidas"]),
                "OPEN con Task":                 len(rc["open_con_task"]),
            },
            "cobertura": {"Con Task": con_sub,
                          "Sin Task": max(len(sb) - con_sub, 0)},
            "progreso": sa["Progreso"].replace("", "Sin progreso")
                          .value_counts().to_dict(),
            "riesgos": rg,
            "por_mes": pm,
        }

    datos_js = json.dumps({
        "conteos": conteos, "estados": estados_bit, "progreso": prog_pla,
        "por_mes": por_mes,
        "cobertura": {"Con Task": con_task_tot,
                      "Sin Task": max(len(bit) - con_task_tot, 0)},
        "coa": {"labels": coa_labels, "open": coa_open, "prog": coa_prog,
                "con_task": coa_con_task, "sin_task": coa_sin_task},
        "dias_prom": dias_prom,
        "riesgo_prom": riesgo_prom,
        "riesgos": riesgos,
        "por_coa": por_coa,
        "colores": COLORES,
    }, ensure_ascii=False)

    tarjetas = f"""
      <div class="tarjeta"><div class="valor">{len(bit)}</div><div class="etq">Ocurrencias FLT<br>(todos los COA · YTD)</div></div>
      <div class="tarjeta"><div class="valor">{abiertos_tot}</div><div class="etq">OPEN en Bitácora</div></div>
      <div class="tarjeta"><div class="valor">{len(bit) - abiertos_tot}</div><div class="etq">In Progress en Bitácora</div></div>
      <div class="tarjeta"><div class="valor">{len(activas)}</div><div class="etq">Tareas activas en Planner<br>(se excluyen {int(pla["Completada"].sum())} completadas)</div></div>
      <div class="tarjeta rojo"><div class="valor">{len(r["task_vencidas"])}</div><div class="etq">Tasks vencidas</div></div>
      <div class="tarjeta rojo"><div class="valor">{len(r["open_sin_inv"])}</div><div class="etq">OPEN sin investigación</div></div>
    """

    filas_coa = "".join(
        "<tr>" + "".join(f"<td{' class=coa' if c == 'COA' else ''}>{f[c]}</td>"
                         for c in df_coa.columns) + "</tr>"
        for _, f in df_coa.iterrows())
    tabla_coa = f"""
    <details class="seccion" open>
      <summary>
        <span class="num">★</span>
        <span class="titulo-sec">Resumen por COA<small>Ocurrencias FLT activas por certificado de operación:
          estado e investigación</small></span>
        <span class="badge badge-ok">{len(bit)}</span>
        <span class="flecha">▾</span>
      </summary>
      <div class="tabla-wrap">
        <table>
          <thead><tr>{"".join(f"<th>{c}</th>" for c in df_coa.columns)}</tr></thead>
          <tbody>{filas_coa}</tbody>
        </table>
      </div>
    </details>"""

    listado = bit.sort_values(["COA", "Fecha"], ascending=[True, False])
    seccion_listado = seccion(
        "≡", "Listado completo de ocurrencias FLT con nivel de riesgo",
        "Todas las ocurrencias activas (Open + In Progress) de todos los COA",
        listado, cols_b)

    # ------- Pestañas adicionales por tipo de ocurrencia (CBN, FRM, …) -------
    extras = extras or {}
    barra_tabs, abre_flt, cierra_flt = "", "", ""
    datos_extras = {}
    paneles_extras = ""
    for codigo, df_t in extras.items():
        if df_t is None or not len(df_t):
            continue
        con_riesgo = int((df_t["Categoría riesgo"] != "Sin riesgo asignado").sum())
        prom_t = df_t.groupby("COA")["Días abierta"].mean()
        orden_t = [c for c in COAS if c in prom_t.index] + \
                  [c for c in prom_t.index if c not in COAS]
        datos_extras[codigo] = {
            "riesgo": {"Con riesgo": con_riesgo,
                       "Sin riesgo asignado": len(df_t) - con_riesgo},
            "dias": {"labels": orden_t,
                     "valores": [round(float(prom_t[c]), 1) for c in orden_t]},
        }
        t_open = int(df_t["Abierto"].sum())
        tarjetas_t = f"""
          <div class="tarjeta"><div class="valor">{len(df_t)}</div><div class="etq">Ocurrencias {codigo} activas<br>(Year to Date)</div></div>
          <div class="tarjeta"><div class="valor">{t_open}</div><div class="etq">OPEN</div></div>
          <div class="tarjeta"><div class="valor">{len(df_t) - t_open}</div><div class="etq">In Progress</div></div>
          <div class="tarjeta"><div class="valor">{con_riesgo}</div><div class="etq">Con riesgo asignado</div></div>
          <div class="tarjeta rojo"><div class="valor">{len(df_t) - con_riesgo}</div><div class="etq">Sin riesgo asignado</div></div>"""
        listado_t = df_t.sort_values(["COA", "Fecha"], ascending=[True, False])
        paneles_extras += f"""
  <div id="tab-{codigo.lower()}" class="panel-tab">
          <div class="tarjetas">{tarjetas_t}</div>
          <div class="graficos">
            <div class="grafico"><h3>Ocurrencias con riesgo vs sin riesgo</h3>
              <canvas id="gRiesgo{codigo}"></canvas></div>
            <div class="grafico"><h3>Días promedio abierta por COA</h3>
              <canvas id="gDias{codigo}"></canvas></div>
          </div>
          {seccion("≡", f"Listado completo de ocurrencias {codigo} con nivel de riesgo",
                   f"Todas las ocurrencias {codigo} activas (Open + In Progress), Year to Date",
                   listado_t, cols_b, abierta=True)}
  </div>"""
    if paneles_extras:
        botones = "".join(
            f'<button class="tab-btn" data-tab="{c.lower()}">{c}</button>'
            for c in extras if extras[c] is not None and len(extras[c]))
        barra_tabs = f"""
  <div class="tabs-barra">
    <button class="tab-btn activo" data-tab="flt">FLT</button>
    {botones}
  </div>"""
        abre_flt = '<div id="tab-flt" class="panel-tab activo">'
        cierra_flt = "</div>\n" + paneles_extras
    datos_extras_js = json.dumps(datos_extras, ensure_ascii=False) if datos_extras else "null"

    secciones = "".join([
        seccion(1, "Eventos OPEN en Bitácora sin tipo de investigación",
                "Ocurrencias abiertas cuyo campo de investigación está vacío",
                r["open_sin_inv"], cols_b, abierta=True),
        seccion(2, "Eventos en Bitácora que no están en Tasks",
                "Ocurrencias sin tarea activa de seguimiento en Planner (las completadas no cuentan)",
                r["bit_sin_task"], cols_b),
        seccion(3, "Eventos en Tasks sin tipo de investigación en Bitácora",
                "Tareas activas cuya ocurrencia no tiene investigación asignada",
                r["task_sin_inv"], cols_t),
        seccion(4, "Tasks completadas sin cerrar en Bitácora",
                "La tarea se completó en Planner pero la ocurrencia sigue "
                "activa (Open o In Progress) en Bitácora",
                r["task_comp_bit_open"], cols_t),
        seccion(5, "Tasks vencidas",
                "Tareas no completadas con fecha de vencimiento anterior a hoy",
                r["task_vencidas"], cols_t),
        seccion(6, "Eventos en Bitácora sin riesgo asignado",
                "Ocurrencias activas (Open + In Progress) cuyo nivel de riesgo está vacío",
                r["sin_riesgo"], cols_b),
    ])

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive">
<title>Bitácora vs Tasks · avianca</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
  :root {{
    --rojo: {COLORES["rojo"]}; --rojo-osc: {COLORES["rojo_oscuro"]};
    --negro: {COLORES["negro"]}; --gris: {COLORES["gris"]};
    --azul-osc: {COLORES["azul_oscuro"]}; --azul-claro: {COLORES["azul_claro"]};
    --gris-claro: {COLORES["gris_claro"]}; --blanco: {COLORES["blanco"]};
    --dorado: {COLORES["dorado"]};
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "Segoe UI", -apple-system, Helvetica, Arial,
         sans-serif; background: var(--gris-claro); color: var(--negro); }}
  header {{ background: linear-gradient(100deg, var(--negro) 0%, var(--negro) 55%,
            var(--rojo) 55.2%, var(--rojo-osc) 100%);
            color: var(--blanco); padding: 28px 40px; }}
  header h1 {{ margin: 0; font-size: 26px; font-weight: 700; letter-spacing: .3px; }}
  header .logo {{ font-style: italic; font-weight: 800; }}
  header p {{ margin: 6px 0 0; opacity: .85; font-size: 13px; }}
  main {{ max-width: 1200px; margin: 0 auto; padding: 26px 20px 60px; }}

  .tarjetas {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px,1fr));
               gap: 14px; margin-bottom: 26px; }}
  .tarjeta {{ background: var(--blanco); border-radius: 10px; padding: 16px 18px;
              border-top: 4px solid var(--negro);
              box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .tarjeta.rojo {{ border-top-color: var(--rojo); }}
  .tarjeta .valor {{ font-size: 32px; font-weight: 800; }}
  .tarjeta.rojo .valor {{ color: var(--rojo); }}
  .tarjeta .etq {{ font-size: 12px; color: var(--gris); margin-top: 2px; }}

  .graficos {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px,1fr));
               gap: 16px; margin-bottom: 30px; }}
  .grafico {{ background: var(--blanco); border-radius: 10px; padding: 16px;
              box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .grafico h3 {{ margin: 0 0 10px; font-size: 14px; color: var(--gris);
                 text-transform: uppercase; letter-spacing: .5px; }}
  .grafico.ancho {{ grid-column: 1 / -1; }}
  .grafico canvas {{ max-height: 300px; }}
  #gCoaInv {{ height: 340px !important; max-height: 340px !important; }}

  .chips-graficas {{ background: var(--blanco); border-radius: 10px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08); border-top: none;
      margin-bottom: 16px; }}

  /* Pestañas FLT / CBN */
  .tabs-barra {{ display: flex; gap: 10px; margin-bottom: 20px; }}
  .tab-btn {{ border: none; background: var(--blanco); color: var(--gris);
      padding: 11px 34px; border-radius: 10px; font-weight: 800;
      font-size: 16px; letter-spacing: .5px; cursor: pointer;
      font-family: inherit; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .tab-btn:hover {{ color: var(--rojo); }}
  .tab-btn.activo {{ background: var(--rojo); color: var(--blanco); }}
  .panel-tab {{ display: none; }}
  .panel-tab.activo {{ display: block; }}

  .seccion {{ background: var(--blanco); border-radius: 10px; margin-bottom: 14px;
              box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }}
  .seccion summary {{ list-style: none; cursor: pointer; display: flex;
                      align-items: center; gap: 14px; padding: 15px 20px;
                      user-select: none; }}
  .seccion summary::-webkit-details-marker {{ display: none; }}
  .seccion summary:hover {{ background: #faf5f5; }}
  .num {{ background: var(--negro); color: var(--blanco); min-width: 30px;
          height: 30px; border-radius: 50%; display: flex; align-items: center;
          justify-content: center; font-weight: 700; font-size: 14px; }}
  .titulo-sec {{ flex: 1; font-weight: 600; font-size: 15px; }}
  .titulo-sec small {{ display: block; font-weight: 400; color: var(--gris);
                       font-size: 12px; margin-top: 2px; }}
  .badge {{ min-width: 42px; text-align: center; padding: 5px 12px;
            border-radius: 999px; font-weight: 700; font-size: 14px; }}
  .badge-alerta {{ background: var(--rojo); color: var(--blanco); }}
  .badge-ok {{ background: #e6e6e6; color: var(--gris); }}
  .flecha {{ color: var(--gris); transition: transform .2s; }}
  details[open] .flecha {{ transform: rotate(180deg); }}

  .tabla-wrap {{ overflow-x: auto; border-top: 1px solid #eee; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: var(--negro); color: var(--blanco); text-align: left;
        padding: 9px 14px; font-weight: 600; white-space: nowrap;
        position: sticky; top: 0; }}
  td {{ padding: 8px 14px; border-bottom: 1px solid #f0f0f0; }}
  tbody tr:nth-child(even) {{ background: #fafafa; }}
  tbody tr:hover {{ background: #fdf1f2; }}
  td.vacio {{ text-align: center; color: var(--gris); padding: 18px;
              font-style: italic; }}
  td.coa {{ font-weight: 700; }}
  td.dias-alto {{ color: var(--rojo); font-weight: 700; }}

  /* Orden por columna */
  th.ordenable {{ cursor: pointer; }}
  th.ordenable:hover {{ background: #333; }}
  th.ordenable::after {{ content: " ↕"; opacity: .35; font-size: 11px; }}
  th.ordenable.asc::after {{ content: " ▲"; opacity: 1; }}
  th.ordenable.desc::after {{ content: " ▼"; opacity: 1; }}

  /* Fila de filtros por columna */
  tr.fila-filtros th {{ position: sticky; top: 35px; background: #efefef;
                        padding: 5px 8px; }}
  tr.fila-filtros input {{ width: 100%; min-width: 60px; padding: 4px 8px;
      border: 1px solid #ccc; border-radius: 6px; font-size: 12px;
      font-family: inherit; }}
  tr.fila-filtros input:focus {{ outline: 2px solid var(--rojo); border-color: var(--rojo); }}

  /* Chips de filtro por COA y contador */
  .chips {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
            padding: 10px 20px; border-top: 1px solid #eee; }}
  .chips .etiq-chips {{ font-size: 12px; color: var(--gris); margin-right: 4px; }}
  .chips button {{ border: 1px solid #ccc; background: var(--blanco);
      color: var(--negro); border-radius: 999px; padding: 4px 14px;
      font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit; }}
  .chips button:hover {{ border-color: var(--rojo); color: var(--rojo); }}
  .chips button.activo {{ background: var(--rojo); border-color: var(--rojo);
      color: var(--blanco); }}
  .chips .contador {{ margin-left: auto; font-size: 12px; color: var(--gris); }}
  footer {{ text-align: center; color: var(--gris); font-size: 12px;
            padding: 20px; }}
</style>
</head>
<body>
<header>
  <h1><span class="logo">avianca</span> · Bitácora AQD vs Planner Tasks</h1>
  <p>Bitácora: Occurrence type FLT · State: All Active · Year to date ·
     COAs: {", ".join(COAS)} &nbsp;|&nbsp; Generado: {datetime.now().strftime("%d-%b-%Y %H:%M")}</p>
</header>
<main>
  {barra_tabs}
  {abre_flt}
  <div class="tarjetas">{tarjetas}</div>

  <div class="graficos">
    <div class="grafico ancho"><h3>Ocurrencias por COA (OPEN / In Progress)</h3>
      <canvas id="gCoa"></canvas></div>
    <div class="grafico"><h3>Tasks vs Bitácora por COA</h3>
      <canvas id="gCoaInv"></canvas></div>
    <div class="grafico"><h3>Nivel de riesgo (todas las ocurrencias)</h3>
      <canvas id="gRiesgo"></canvas></div>
    <div class="grafico ancho"><h3>Días promedio abierta por COA</h3>
      <canvas id="gDias"></canvas></div>
    <div class="grafico ancho"><h3>Riesgo promedio por COA</h3>
      <canvas id="gBarras"></canvas></div>
    <div class="grafico"><h3>Ocurrencias vs Tasks (todas: Open + In Progress)</h3>
      <canvas id="gCobertura"></canvas></div>
    <div class="grafico"><h3>Progreso de tareas en Planner</h3>
      <canvas id="gProgreso"></canvas></div>
    <div class="grafico ancho"><h3>Ocurrencias de Bitácora por mes (YTD)</h3>
      <canvas id="gMes"></canvas></div>
  </div>

  {tabla_coa}
  {secciones}
  {seccion_listado}
  {cierra_flt}
</main>
<footer>Reporte generado automáticamente · Safety / AQD Dashboard · avianca</footer>

<script>
const D = {datos_js};
const C = D.colores;
const paleta = [C.rojo, C.negro, C.gris, C.azul_claro, C.rojo_oscuro, C.dorado];
Chart.defaults.font.family = '"Segoe UI", Helvetica, Arial, sans-serif';
Chart.defaults.color = C.negro;

// Valores numéricos visibles en todas las gráficas
Chart.register(ChartDataLabels);
function colorEtiqueta(ctx) {{
  let bg = ctx.dataset.backgroundColor;
  if (Array.isArray(bg)) bg = bg[ctx.dataIndex];
  if (typeof bg !== "string" || bg[0] !== "#") return "#fff";
  const r = parseInt(bg.slice(1, 3), 16), g = parseInt(bg.slice(3, 5), 16),
        b = parseInt(bg.slice(5, 7), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#333" : "#fff";
}}
Chart.defaults.set("plugins.datalabels", {{
  color: colorEtiqueta,
  font: {{ weight: 700, size: 11 }},
  display: ctx => (ctx.dataset.data[ctx.dataIndex] || 0) > 0
}});
// etiquetas sobre la barra (para gráficas de barras no apiladas)
const etiquetaArriba = {{ anchor: "end", align: "end", offset: -2, color: C.negro }};

const chCoa = new Chart(document.getElementById("gCoa"), {{
  type: "bar",
  data: {{ labels: D.coa.labels,
    datasets: [
      {{ label: "OPEN", data: D.coa.open, backgroundColor: C.rojo, borderRadius: 4 }},
      {{ label: "In Progress", data: D.coa.prog, backgroundColor: C.gris, borderRadius: 4 }}
    ] }},
  options: {{ plugins: {{ legend: {{ position: "bottom" }} }},
    scales: {{ x: {{ stacked: true }},
               y: {{ stacked: true, beginAtZero: true, ticks: {{ precision: 0 }} }} }} }}
}});

const chCoaInv = new Chart(document.getElementById("gCoaInv"), {{
  type: "bar",
  data: {{ labels: D.coa.labels,
    datasets: [
      {{ label: "Con Task", data: D.coa.con_task, backgroundColor: C.azul_claro, borderRadius: 4 }},
      {{ label: "Sin Task (solo en Bitácora)", data: D.coa.sin_task, backgroundColor: C.rojo, borderRadius: 4 }}
    ] }},
  options: {{ indexAxis: "y", maintainAspectRatio: false,
    plugins: {{ legend: {{ position: "bottom" }} }},
    scales: {{ x: {{ stacked: true, beginAtZero: true, ticks: {{ precision: 0 }} }},
               y: {{ stacked: true, ticks: {{ autoSkip: false }} }} }} }}
}});

// Paleta semántica de riesgo (tonos suaves y armónicos)
const colorRiesgo = {{ "Intolerable": "#A63D40", "Unacceptable": "#E15759",
  "Tolerable": "#EDC948", "Acceptable": "#59A14F",
  "Sin riesgo asignado": "#CBCBCB" }};
// color de una barra de riesgo promedio según su magnitud
// (Acceptable: puntajes 1-10 · Tolerable: 20-101 · Intolerable: 500+)
const colorPromedio = v => v == null ? colorRiesgo["Sin riesgo asignado"]
    : v < 20 ? colorRiesgo["Acceptable"]
    : v < 500 ? colorRiesgo["Tolerable"]
    : colorRiesgo["Intolerable"];
// color para días promedio abierta: verde ≤15 · amarillo >15 · rojo >20
const colorDias = v => v == null ? colorRiesgo["Sin riesgo asignado"]
    : v > 20 ? colorRiesgo["Unacceptable"]
    : v > 15 ? colorRiesgo["Tolerable"]
    : colorRiesgo["Acceptable"];
const chRiesgo = new Chart(document.getElementById("gRiesgo"), {{
  type: "doughnut",
  data: {{ labels: Object.keys(D.riesgos),
    datasets: [{{ data: Object.values(D.riesgos),
      backgroundColor: Object.keys(D.riesgos).map(k => colorRiesgo[k] || C.gris),
      borderWidth: 2 }}] }},
  options: {{ cutout: "62%", plugins: {{ legend: {{ position: "bottom" }} }} }}
}});

const chDias = new Chart(document.getElementById("gDias"), {{
  type: "bar",
  data: {{ labels: D.dias_prom.labels,
    datasets: [{{ label: "Días promedio", data: D.dias_prom.valores,
      backgroundColor: D.dias_prom.valores.map(colorDias),
      borderRadius: 6 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }}, datalabels: etiquetaArriba }},
    scales: {{ y: {{ beginAtZero: true, grace: "10%",
                     title: {{ display: true, text: "días" }} }} }} }}
}});

// Riesgo promedio por COA (solo ocurrencias con riesgo asignado)
const chBarras = new Chart(document.getElementById("gBarras"), {{
  type: "bar",
  data: {{ labels: D.riesgo_prom.labels,
    datasets: [{{ label: "Riesgo promedio", data: D.riesgo_prom.valores,
      backgroundColor: D.riesgo_prom.valores.map(colorPromedio),
      borderRadius: 6 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }}, datalabels: etiquetaArriba,
      tooltip: {{ callbacks: {{ footer: items =>
        "sobre " + D.riesgo_prom.n[items[0].dataIndex] + " ocurrencia(s) con riesgo" }} }} }},
    scales: {{ y: {{ beginAtZero: true, grace: "10%",
                     title: {{ display: true, text: "puntaje de riesgo" }} }} }} }}
}});

const chCobertura = new Chart(document.getElementById("gCobertura"), {{
  type: "doughnut",
  data: {{ labels: Object.keys(D.cobertura),
    datasets: [{{ data: Object.values(D.cobertura),
      backgroundColor: [C.gris, C.rojo], borderWidth: 2 }}] }},
  options: {{ cutout: "62%", plugins: {{ legend: {{ position: "bottom" }} }} }}
}});

const chProgreso = new Chart(document.getElementById("gProgreso"), {{
  type: "doughnut",
  data: {{ labels: Object.keys(D.progreso),
    datasets: [{{ data: Object.values(D.progreso),
      backgroundColor: paleta, borderWidth: 2 }}] }},
  options: {{ cutout: "62%", plugins: {{ legend: {{ position: "bottom" }} }} }}
}});

const chMes = new Chart(document.getElementById("gMes"), {{
  type: "bar",
  data: {{ labels: Object.keys(D.por_mes),
    datasets: [{{ label: "Ocurrencias", data: Object.values(D.por_mes),
      backgroundColor: C.rojo, borderRadius: 6 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }}, datalabels: etiquetaArriba }},
    scales: {{ y: {{ beginAtZero: true, grace: "10%", ticks: {{ precision: 0 }} }} }} }}
}});

// ---------------------------------------------------------------------------
// Pestaña CBN: gráficas y conmutador de pestañas
// ---------------------------------------------------------------------------
const DX = {datos_extras_js};
Object.entries(DX || {{}}).forEach(([cod, d]) => {{
  const cR = document.getElementById("gRiesgo" + cod);
  const cD = document.getElementById("gDias" + cod);
  if (!cR || !cD) return;
  new Chart(cR, {{
    type: "doughnut",
    data: {{ labels: Object.keys(d.riesgo),
      datasets: [{{ data: Object.values(d.riesgo),
        backgroundColor: [C.azul_claro, C.rojo], borderWidth: 2 }}] }},
    options: {{ cutout: "62%", plugins: {{ legend: {{ position: "bottom" }} }} }}
  }});
  new Chart(cD, {{
    type: "bar",
    data: {{ labels: d.dias.labels,
      datasets: [{{ label: "Días promedio", data: d.dias.valores,
        backgroundColor: d.dias.valores.map(colorDias),
        borderRadius: 6 }}] }},
    options: {{ plugins: {{ legend: {{ display: false }}, datalabels: etiquetaArriba }},
      scales: {{ y: {{ beginAtZero: true, grace: "10%",
                       title: {{ display: true, text: "días" }} }} }} }}
  }});
}});

document.querySelectorAll(".tab-btn").forEach(b => b.addEventListener("click", () => {{
  document.querySelectorAll(".tab-btn").forEach(x => x.classList.remove("activo"));
  document.querySelectorAll(".panel-tab").forEach(x => x.classList.remove("activo"));
  b.classList.add("activo");
  document.getElementById("tab-" + b.dataset.tab).classList.add("activo");
  // las gráficas creadas en una pestaña oculta necesitan re-dimensionarse
  requestAnimationFrame(() =>
      Object.values(Chart.instances).forEach(c => c.resize()));
}}));

// ---------------------------------------------------------------------------
// Filtro por COA para las gráficas
// ---------------------------------------------------------------------------
function aplicarCoaGraficas(coa) {{
  const d = D.por_coa[coa];
  chCobertura.data.datasets[0].data = Object.values(d.cobertura);
  chProgreso.data.labels = Object.keys(d.progreso);
  chProgreso.data.datasets[0].data = Object.values(d.progreso);
  chRiesgo.data.labels = Object.keys(d.riesgos);
  chRiesgo.data.datasets[0].data = Object.values(d.riesgos);
  chRiesgo.data.datasets[0].backgroundColor =
      Object.keys(d.riesgos).map(k => colorRiesgo[k] || C.gris);
  chMes.data.labels = Object.keys(d.por_mes);
  chMes.data.datasets[0].data = Object.values(d.por_mes);
  // en las gráficas "por COA" se resalta el COA elegido y se atenúa el resto
  const atenuar = c => c + "38";
  const cols = base => D.coa.labels.map(l =>
      (coa === "Todos" || l === coa) ? base : atenuar(base));
  chBarras.data.datasets[0].backgroundColor =
      D.riesgo_prom.valores.map((v, i) => {{
        const base = colorPromedio(v);
        return (coa === "Todos" || D.coa.labels[i] === coa) ? base : atenuar(base);
      }});
  chCoa.data.datasets[0].backgroundColor = cols(C.rojo);
  chCoa.data.datasets[1].backgroundColor = cols(C.gris);
  chCoaInv.data.datasets[0].backgroundColor = cols(C.azul_claro);
  chCoaInv.data.datasets[1].backgroundColor = cols(C.rojo);
  chDias.data.datasets[0].backgroundColor =
      D.dias_prom.valores.map((v, i) => {{
        const base = colorDias(v);
        return (coa === "Todos" || D.coa.labels[i] === coa) ? base : atenuar(base);
      }});
  [chBarras, chCobertura, chProgreso, chRiesgo, chMes,
   chCoa, chCoaInv, chDias].forEach(c => c.update());
}}

const barraG = document.createElement("div");
barraG.className = "chips chips-graficas";
barraG.innerHTML = '<span class="etiq-chips">COA en gráficas:</span>';
Object.keys(D.por_coa).forEach(coa => {{
  const b = document.createElement("button");
  b.textContent = coa;
  if (coa === "Todos") b.classList.add("activo");
  b.addEventListener("click", () => {{
    barraG.querySelectorAll("button").forEach(x => x.classList.remove("activo"));
    b.classList.add("activo");
    aplicarCoaGraficas(coa);
  }});
  barraG.appendChild(b);
}});
document.querySelector(".graficos").before(barraG);

// ---------------------------------------------------------------------------
// Tablas interactivas: orden por columna, filtro por columna y chips de COA
// ---------------------------------------------------------------------------
const normalizar = s => s.toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "");

document.querySelectorAll("details.seccion").forEach(sec => {{
  const table = sec.querySelector("table");
  if (!table || !table.tHead || !table.tBodies[0]) return;
  const filas = [...table.tBodies[0].rows].filter(r => !r.querySelector("td.vacio"));
  if (!filas.length) return;
  const ths = [...table.tHead.rows[0].cells];

  // --- barra de chips de COA + contador de registros ---
  const barra = document.createElement("div");
  barra.className = "chips";
  const contador = document.createElement("span");
  contador.className = "contador";
  let coaActivo = "";
  const iCoa = ths.findIndex(t => t.textContent.trim().replace(/[ ↕▲▼]+$/,"") === "COA");
  if (iCoa >= 0) {{
    const etiq = document.createElement("span");
    etiq.className = "etiq-chips"; etiq.textContent = "COA:";
    barra.appendChild(etiq);
    const valores = [...new Set(filas.map(r => r.cells[iCoa].textContent.trim() || "(sin COA)"))]
                    .sort((a, b) => a.localeCompare(b, "es"));
    ["Todos", ...valores].forEach(v => {{
      const b = document.createElement("button");
      b.textContent = v;
      if (v === "Todos") b.classList.add("activo");
      b.addEventListener("click", () => {{
        coaActivo = v === "Todos" ? "" : v;
        barra.querySelectorAll("button").forEach(x => x.classList.remove("activo"));
        b.classList.add("activo");
        aplicar();
      }});
      barra.appendChild(b);
    }});
  }}
  barra.appendChild(contador);
  sec.querySelector(".tabla-wrap").before(barra);

  // --- fila de filtros por columna ---
  const trF = table.tHead.insertRow();
  trF.className = "fila-filtros";
  ths.forEach(() => {{
    const th = document.createElement("th");
    const inp = document.createElement("input");
    inp.type = "search";
    inp.placeholder = "Filtrar…";
    inp.addEventListener("input", () => aplicar());
    th.appendChild(inp);
    trF.appendChild(th);
  }});

  function aplicar() {{
    const filtros = [...trF.cells].map(c => normalizar(c.firstChild.value || ""));
    let visibles = 0;
    filas.forEach(r => {{
      let ok = true;
      if (coaActivo) {{
        const v = r.cells[iCoa].textContent.trim() || "(sin COA)";
        if (v !== coaActivo) ok = false;
      }}
      if (ok) filtros.forEach((f, i) => {{
        if (f && !normalizar(r.cells[i].textContent).includes(f)) ok = false;
      }});
      r.style.display = ok ? "" : "none";
      if (ok) visibles++;
    }});
    contador.textContent = visibles === filas.length
        ? filas.length + " registros"
        : visibles + " de " + filas.length + " registros";
  }}
  aplicar();

  // --- orden al hacer clic en el encabezado ---
  ths.forEach((th, i) => {{
    th.classList.add("ordenable");
    th.addEventListener("click", () => {{
      const asc = !th.classList.contains("asc");
      ths.forEach(t => t.classList.remove("asc", "desc"));
      th.classList.add(asc ? "asc" : "desc");
      const clave = r => {{
        const td = r.cells[i];
        return (td.dataset.orden !== undefined ? td.dataset.orden
                                               : td.textContent.trim());
      }};
      filas.sort((a, b) => {{
        const va = clave(a), vb = clave(b);
        if (va === "" && vb !== "") return 1;    // vacíos siempre al final
        if (vb === "" && va !== "") return -1;
        const na = parseFloat(va.replace(",", ".")),
              nb = parseFloat(vb.replace(",", "."));
        let c;
        if (!isNaN(na) && !isNaN(nb) && /^[-\\d.,%]/.test(va) && /^[-\\d.,%]/.test(vb))
          c = na - nb;
        else
          c = normalizar(va).localeCompare(normalizar(vb), "es");
        return asc ? c : -c;
      }});
      filas.forEach(r => table.tBodies[0].appendChild(r));
    }});
  }});
}});
</script>
</body>
</html>"""

    Path(salida).write_text(html, encoding="utf-8")
    return salida


# ---------------------------------------------------------------------------
# DATOS DE EJEMPLO (--demo)
# ---------------------------------------------------------------------------

def generar_demo():
    import random
    random.seed(7)
    estados = ["OPEN"] * 6 + ["CLOSED"] * 3 + ["IN REVIEW"]
    invs = ["", "", "MOR", "Investigation", "Quick Review", ""]
    filas_b, filas_t = [], []
    for i in range(1, 41):
        ev = f"O{12500 + i}-26"
        fecha = date(HOY.year, 1, 1) + timedelta(days=random.randint(0, (HOY - date(HOY.year, 1, 1)).days))
        filas_b.append({
            "Occurrence Number": ev,
            "Occurrence Date": fecha.strftime("%d/%m/%Y"),
            "Occurrence Type": "FLT",
            "State": random.choice(estados),
            "Investigation Type": random.choice(invs),
            "Title": f"Evento de vuelo {ev} — descripción de ejemplo",
        })
        if random.random() < 0.7:   # 70% tienen tarea
            venc = fecha + timedelta(days=random.randint(10, 90))
            prog = random.choice(["No iniciada", "En curso", "Completada"])
            filas_t.append({
                "Nombre de la tarea": f"{ev} Análisis del evento",
                "Nombre del depósito": random.choice(["Por revisar", "En investigación", "Cerrados"]),
                "Progreso": prog,
                "Fecha de vencimiento": venc.strftime("%d/%m/%Y"),
                "Fecha de finalización": (venc.strftime("%d/%m/%Y") if prog == "Completada" else ""),
                "Asignado a": random.choice(["A. Gómez", "L. Pérez", "M. Ruiz"]),
            })
    b = CARPETA / "bitacora_demo.xlsx"
    t = CARPETA / "planner_demo.xlsx"
    pd.DataFrame(filas_b).to_excel(b, index=False)
    # La exportación real de Planner trae 2 filas de metadatos: se simulan
    with pd.ExcelWriter(t) as w:
        meta = pd.DataFrame([["Nombre del plan", "AVA FLT Seguimiento"],
                             ["Exportado", HOY.strftime("%d/%m/%Y")]])
        meta.to_excel(w, index=False, header=False, startrow=0)
        pd.DataFrame(filas_t).to_excel(w, index=False, startrow=3)
    print(f"Datos de ejemplo creados: {b.name}, {t.name}")
    return b, t


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Compara Bitácora AQD vs Planner Tasks")
    ap.add_argument("--bitacora", default=None, help="Archivo exportado de Bitácora (.xlsx/.csv)")
    ap.add_argument("--planner", default=None, help="Archivo exportado de Planner (.xlsx/.csv)")
    ap.add_argument("--salida", default=str(ARCHIVO_SALIDA), help="Archivo HTML de salida")
    ap.add_argument("--demo", action="store_true", help="Genera datos de ejemplo y el reporte")
    args = ap.parse_args()

    if args.demo:
        rb, rt = generar_demo()
    else:
        rb = Path(args.bitacora) if args.bitacora else ARCHIVO_BITACORA
        rt = Path(args.planner) if args.planner else ARCHIVO_PLANNER
        if not rb.exists() and rb.with_suffix(".csv").exists():
            rb = rb.with_suffix(".csv")
        if not rt.exists() and rt.with_suffix(".csv").exists():
            rt = rt.with_suffix(".csv")

    bit = cargar_bitacora(rb)
    pla = cargar_planner(rt)
    # Pestañas adicionales por tipo (opcionales): siempre Year to Date
    extras = {}
    for codigo, archivo in (("CBN", "bitacora_cbn.csv"),
                            ("FRM", "bitacora_frm.csv")):
        ruta_x = CARPETA / archivo
        if ruta_x.exists():
            extras[codigo] = cargar_bitacora(ruta_x)
            print(f"{codigo}:      {len(extras[codigo])} ocurrencias activas (YTD)")
    # Enriquecer las tareas con el título y estado del evento en Bitácora
    pla = pla.merge(
        bit[["Evento", "Descripción", "Estado", "COA"]].rename(
            columns={"Descripción": "Título (Bitácora)",
                     "Estado": "Estado Bitácora"}),
        on="Evento", how="left")
    pla["COA"] = pla["COA"].fillna("")
    print(f"Bitácora: {len(bit)} ocurrencias ({int(bit['Abierto'].sum())} OPEN)")
    activas = int((~pla["Completada"]).sum())
    print(f"Planner:  {len(pla)} tareas, {activas} activas "
          f"({int(pla['Vencida'].sum())} vencidas)")

    r = comparar(bit, pla)
    for clave, df in r.items():
        print(f"  {clave}: {len(df)}")

    salida = generar_html(bit, pla, r, args.salida, extras)
    print(f"\n✔ Reporte generado: {salida}")


if __name__ == "__main__":
    main()
