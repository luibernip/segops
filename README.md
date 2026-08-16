# AQD Dashboard — Bitácora AQD vs Planner Tasks

Dashboard de Safety que compara las ocurrencias del **AQD Portal**
(`bitacora.avianca.com/AQDPortal/safety.aspx`) contra las tareas de
**Microsoft Planner**, y publica un reporte HTML interactivo.

**Origen:** [luibernip/segops](https://github.com/luibernip/segops) · rama `main` · commit `f215b68` (2026-08-13 16:45 COT)
**Publicado en:** https://luibernip.github.io/segops/ (espejo en Cloudflare Pages, proyecto `aqd-7512913f`)

## Contenido

### Reporte publicado
| Archivo | Qué es |
|---|---|
| `index.html` | El dashboard generado. Es una copia de `reporte_bitacora_vs_tasks.html` que produce el pipeline. **Se regenera automáticamente: no editar a mano.** |
| `.nojekyll`, `robots.txt` | Configuración de GitHub Pages (sin Jekyll, sin indexación). |

### Pipeline (orden de ejecución)
| Script | Qué hace |
|---|---|
| `extraer_cloud.py` | Inicia sesión headless en el AQD Portal con `AQD_USUARIO` / `AQD_CLAVE` y extrae las ocurrencias activas *Year to Date* de los tipos **FLT, CBN, FRM y GRH** vía el endpoint JSON. Escribe los `bitacora_*_real.csv`. |
| `extraer_planner_cloud.py` | Extrae las tareas de Planner usando la "sesión virtual" de Microsoft guardada en el secreto `MS_SESION` (partido en hasta 3 secretos de 48 KB). Si la sesión expiró **no falla**: conserva el `planner_raw.txt` anterior y deja el aviso. |
| `preparar_datos.py` | Normaliza lo crudo al formato estándar: `bitacora_real.csv` (`;`) → `bitacora.csv`, `planner_raw.txt` (`~`) → `planner.csv`. |
| `comparar_bitacora_planner.py` | Compara ambas fuentes y genera `reporte_bitacora_vs_tasks.html` con la paleta de Avianca. |

### Scripts de apoyo (se corren a mano, en local)
| Script | Qué hace |
|---|---|
| `renovar_planner.py` | Renueva la sesión de Microsoft cuando expira: abre el navegador, espera el login y sube la sesión como secreto de GitHub. Es lo que hay detrás de *"Actualizar Reporte.command"* en el Mac. |
| `diagnostico_planner.py` | Diagnóstico: informa dónde están hoy los datos de las tareas dentro de Planner (Microsoft cambia la estructura del DOM/React). Solo imprime; no sube nada. |

### Datos
- `bitacora_real.csv` (FLT), `bitacora_cbn_real.csv`, `bitacora_frm_real.csv`, `bitacora_grh_real.csv` — extracción cruda.
  Formato: `id;fecha;registrada;estado;investigacion;riesgo;titulo;matricula`
- `bitacora_cbn.csv`, `bitacora_frm.csv`, `bitacora_grh.csv` — versiones procesadas.
- `planner_raw.txt` — volcado crudo de Planner (separador `~`).

### Automatización
`.github/workflows/actualizar.yml` corre cada hora entre las **6:00 y las 19:00 hora Colombia**
(y a demanda desde la pestaña *Actions*). Instala pandas + Playwright/Chromium, corre el pipeline
completo, confirma y publica a GitHub Pages con reintentos, y espeja el sitio en Cloudflare Pages.
Si la sesión de Planner expiró, abre un *issue* de aviso automáticamente y lo cierra solo cuando
la sesión vuelve a estar activa.

**Secretos que necesita:** `AQD_USUARIO`, `AQD_CLAVE`, `MS_SESION` (+ `MS_SESION_2`, `MS_SESION_3`),
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.

## Ojo con el espejo de Cloudflare

El paso de espejo **reemplaza el sitio completo** en cada despliegue, así que sube también
`tod.html` — que en el repo original vive junto a este dashboard. Si separas los dos proyectos
en repos distintos, ese paso del workflow hay que ajustarlo o el dashboard TOD desaparece del
espejo. Ver [../TOD-Turbulence](../TOD-Turbulence).
