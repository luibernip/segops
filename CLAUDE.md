# AQD Dashboard

Dashboard de Safety (Avianca) que compara las ocurrencias del **AQD Portal**
(`bitacora.avianca.com/AQDPortal/safety.aspx` — tipos FLT/CBN/FRM/GRH, State: All Active,
Year to date) contra las tareas de **Microsoft Planner**, y publica un reporte HTML.

Ver `README.md` para el detalle de cada archivo.

- **Origen:** repo `luibernip/segops`, rama `main`, commit `f215b68`. Esta carpeta es una copia de archivos, **sin `.git`**.
- **Publicado:** https://luibernip.github.io/segops/ + espejo en Cloudflare Pages (`aqd-7512913f`).
- **Proyecto hermano:** `../TOD-Turbulence` (dashboard estático, sin relación de código).

## Reglas del proyecto

- **`index.html` es generado, no fuente.** Lo produce `comparar_bitacora_planner.py` como
  `reporte_bitacora_vs_tasks.html` y el workflow lo copia encima. Editarlo a mano se pierde en la
  siguiente corrida horaria — los cambios de presentación van en el script que lo genera.
- **Los `bitacora_*.csv` y `planner_raw.txt` también son generados** por los extractores. No
  editarlos a mano.
- **El pipeline tiene orden:** `extraer_cloud.py` → `extraer_planner_cloud.py` →
  `preparar_datos.py` → `comparar_bitacora_planner.py`.
- **El espejo de Cloudflare reemplaza el sitio completo** en cada despliegue, por eso el workflow
  sube también `tod.html`, que pertenece al proyecto hermano. Al tocar ese paso, no romper eso.
- **Credenciales:** nunca en el código. Van como secretos de GitHub (`AQD_USUARIO`, `AQD_CLAVE`,
  `MS_SESION`/`_2`/`_3`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`).
- **La sesión de Planner expira.** Cuando pasa, el extractor no falla: conserva el `planner_raw.txt`
  anterior y el workflow abre un issue. Se renueva en local con `renovar_planner.py`.

## Dependencias

`pandas`, `playwright` (+ Chromium). Python 3.12.
