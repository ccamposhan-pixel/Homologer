from __future__ import annotations

import cgi
import html
import json
import os
import shutil
import threading
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from clinical_homologation.enrichment import read_table
from clinical_homologation.pipeline import DEFAULT_MAPPING, export_workbook, process_products, resolve_mapping


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT)).resolve()
UPLOADS = DATA_DIR / "uploads"
OUTPUTS = DATA_DIR / "outputs"
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
JOBS: dict[str, dict[str, object]] = {}
JOBS_LOCK = threading.Lock()


APP_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f7f8fa;color:#18212f}
header{background:#0f513f;color:#fff;padding:22px 32px}
main{max-width:1180px;margin:0 auto;padding:26px 24px 60px}
section{background:#fff;border:1px solid #dfe5e8;border-radius:8px;padding:22px;margin-bottom:18px}
h1{font-size:24px;margin:0} h2{font-size:19px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
label{font-weight:600;font-size:13px;display:block;margin-bottom:5px}
input,select,button{font:inherit}
input[type=file],select{width:100%;box-sizing:border-box;border:1px solid #bcc8cf;border-radius:6px;padding:9px;background:#fff}
button,.button{background:#0f766e;color:white;border:0;border-radius:6px;padding:10px 14px;text-decoration:none;display:inline-block;cursor:pointer}
.secondary{background:#475569}.muted{color:#586574}.warn{color:#8a4b00}
.progress-wrap{border:1px solid #c8d3d8;background:#eef3f2;border-radius:8px;height:28px;overflow:hidden;margin-top:10px}
.progress-bar{height:100%;width:0;background:#0f766e;color:#fff;text-align:center;line-height:28px;font-weight:700;transition:width .25s ease}
.status-line{font-size:15px;margin-top:12px}
.error-box{border:1px solid #d98b8b;background:#fff1f1;color:#7f1d1d;border-radius:8px;padding:12px;white-space:pre-wrap}
.live-grid{display:grid;grid-template-columns:1fr;gap:18px}
.table-scroll{max-height:330px;overflow:auto;border:1px solid #e5e9ec;border-radius:8px}
.small{font-size:12px}.hidden{display:none}.done{color:#0f513f;font-weight:700}
table{border-collapse:collapse;width:100%;font-size:13px} th,td{border-bottom:1px solid #e5e9ec;padding:8px;text-align:left;vertical-align:top}
th{background:#eef3f2}.pill{display:inline-block;border-radius:999px;background:#e6f4ef;padding:4px 9px;margin:2px}
"""


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{APP_CSS}</style></head>
<body><header><h1>{html.escape(title)}</h1><p>Homologacion conservadora con trazabilidad para Chile</p></header><main>{body}</main></body></html>""".encode("utf-8")


def safe_name(filename: str) -> str:
    cleaned = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in "._- ")
    return cleaned or f"archivo_{datetime.now().timestamp():.0f}"


def save_upload(field) -> Path | None:
    if field is None or not getattr(field, "filename", ""):
        return None
    UPLOADS.mkdir(exist_ok=True)
    target = UPLOADS / f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_name(field.filename)}"
    with target.open("wb") as fh:
        shutil.copyfileobj(field.file, fh)
    return target


def save_uploads(field_or_fields) -> list[Path]:
    if field_or_fields is None:
        return []
    fields = field_or_fields if isinstance(field_or_fields, list) else [field_or_fields]
    paths = []
    for field in fields:
        path = save_upload(field)
        if path is not None:
            paths.append(path)
    return paths


def options(columns: list[str], selected: str | None) -> str:
    markup = ['<option value="">-- No usar --</option>']
    for column in columns:
        attr = " selected" if column == selected else ""
        markup.append(f'<option value="{html.escape(column)}"{attr}>{html.escape(column)}</option>')
    return "".join(markup)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self.json_response({"ok": True})
        elif not self.is_authorized(parsed):
            self.show_login(parsed.path)
        elif parsed.path == "/":
            self.respond(page("Agente de homologacion clinica", upload_form()))
        elif parsed.path == "/inspect":
            self.respond(page("Cargar archivos", "<section><p class='warn'>Esta pantalla se abre despues de subir archivos. Vuelve al inicio y carga la base para continuar.</p><a class='button' href='/'>Volver a cargar archivos</a></section>"))
        elif parsed.path == "/download":
            query = urllib.parse.parse_qs(parsed.query)
            self.download(Path(query.get("file", [""])[0]))
        elif parsed.path == "/progress":
            query = urllib.parse.parse_qs(parsed.query)
            self.progress(query.get("job", [""])[0])
        elif parsed.path == "/watch":
            query = urllib.parse.parse_qs(parsed.query)
            self.watch(query.get("job", [""])[0])
        elif parsed.path == "/result":
            query = urllib.parse.parse_qs(parsed.query)
            self.result(query.get("job", [""])[0])
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/login":
            self.login()
        elif not self.is_authorized(parsed):
            self.show_login(parsed.path, status=401)
        elif self.path == "/inspect":
            self.inspect_uploads()
        elif self.path == "/process":
            self.process()
        else:
            self.send_error(404)

    def parse_form(self):
        return cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")})

    def is_authorized(self, parsed) -> bool:
        if not APP_PASSWORD:
            return True
        cookie = self.headers.get("Cookie", "")
        if f"homologacion_auth={APP_PASSWORD}" in cookie:
            return True
        query = urllib.parse.parse_qs(parsed.query)
        return query.get("token", [""])[0] == APP_PASSWORD

    def show_login(self, next_path: str = "/", status: int = 200) -> None:
        self.respond(
            page(
                "Acceso protegido",
                f"""
<section>
  <h2>Ingresar</h2>
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{html.escape(next_path or '/')}">
    <label>Clave de acceso</label>
    <input type="password" name="password" required>
    <p><button type="submit">Entrar</button></p>
  </form>
</section>
""",
            ),
            status=status,
        )

    def login(self) -> None:
        form = self.parse_form()
        password = str(form.getfirst("password", ""))
        next_path = str(form.getfirst("next", "/")) or "/"
        if APP_PASSWORD and password != APP_PASSWORD:
            self.show_login(next_path, status=401)
            return
        self.send_response(303)
        self.send_header("Location", next_path)
        self.send_header("Set-Cookie", f"homologacion_auth={password}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()

    def inspect_uploads(self) -> None:
        try:
            form = self.parse_form()
            products = save_upload(form["productos"]) if "productos" in form else None
            vademecum = save_upload(form["vademecum"]) if "vademecum" in form else None
            historicos = save_uploads(form["historicos"]) if "historicos" in form else []
            if products is None:
                self.respond(page("Archivo requerido", "<section><p class='warn'>Carga una base de productos Excel o CSV.</p><a class='button secondary' href='/'>Volver</a></section>"), status=400)
                return
            product_columns = list(read_table(products).columns)
            detected = resolve_mapping(product_columns)
            vademecum_columns = list(read_table(vademecum).columns) if vademecum is not None else []
            self.respond(page("Configurar columnas", mapping_form(products, vademecum, historicos, product_columns, detected, vademecum_columns)))
        except Exception:
            self.respond(
                page(
                    "Error leyendo archivos",
                    f"<section><p class='warn'>No pude leer el archivo cargado. Revisa que sea Excel o CSV valido.</p><div class='error-box'>{html.escape(traceback.format_exc())}</div><p><a class='button secondary' href='/'>Volver</a></p></section>",
                ),
                status=500,
            )

    def process(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        product_path = Path(data.get("product_path", [""])[0])
        vademecum_value = data.get("vademecum_path", [""])[0]
        vademecum_path = Path(vademecum_value) if vademecum_value else None
        historical_paths = [Path(value) for value in data.get("historical_path", []) if value]
        mapping = {key.removeprefix("map_"): values[0] for key, values in data.items() if key.startswith("map_") and values and values[0]}
        vad_mapping = {key.removeprefix("vad_"): values[0] for key, values in data.items() if key.startswith("vad_") and values and values[0]}
        job_id = uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "state": "running",
                "percent": 0,
                "message": "Preparando procesamiento",
                "result_html": "",
                "error": "",
                "normalized": [],
                "proposals": [],
                "download_url": "",
            }
        thread = threading.Thread(target=run_job, args=(job_id, product_path, vademecum_path, historical_paths, mapping, vad_mapping), daemon=True)
        thread.start()
        self.redirect(f"/watch?job={urllib.parse.quote(job_id)}")

    def watch(self, job_id: str) -> None:
        with JOBS_LOCK:
            exists = job_id in JOBS
        if not exists:
            self.respond(page("Procesamiento no encontrado", "<section><p class='warn'>No encontre ese procesamiento activo. Si el servidor fue reiniciado, vuelve a cargar la base.</p><a class='button' href='/'>Volver al inicio</a></section>"), status=404)
            return
        self.respond(page("Procesando homologacion", progress_page(job_id)))

    def progress(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = dict(JOBS.get(job_id, {"state": "missing", "percent": 0, "message": "Job no encontrado", "error": ""}))
        payload = {
            "state": job.get("state"),
            "percent": job.get("percent", 0),
            "message": job.get("message", ""),
            "error": job.get("error", ""),
            "normalized": job.get("normalized", []),
            "proposals": job.get("proposals", []),
            "download_url": job.get("download_url", ""),
            "result_url": f"/result?job={urllib.parse.quote(job_id)}" if job.get("state") == "done" else "",
        }
        content = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def result(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = dict(JOBS.get(job_id, {}))
        if not job:
            self.respond(page("Resultado no encontrado", "<section><p class='warn'>No se encontro el procesamiento solicitado.</p><a class='button secondary' href='/'>Volver</a></section>"), status=404)
            return
        if job.get("state") == "failed":
            self.respond(page("Error de procesamiento", f"<section><div class='error-box'>{html.escape(str(job.get('error', 'Error desconocido')))}</div><p><a class='button secondary' href='/'>Volver</a></p></section>"), status=500)
            return
        self.respond(page("Resultado de homologacion", str(job.get("result_html", ""))))

    def download(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.exists() or OUTPUTS.resolve() not in resolved.parents:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{resolved.name}"')
        self.end_headers()
        with resolved.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    def respond(self, content: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def json_response(self, payload: dict[str, object], status: int = 200) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()


def upload_form() -> str:
    return """
<section id="resumePanel" class="hidden">
  <h2>Procesamiento en curso</h2>
  <p id="resumeText" class="muted"></p>
  <p><a id="resumeLink" class="button" href="#">Retomar revision</a></p>
</section>
<section>
  <h2>Cargar archivos</h2>
  <form method="post" action="/inspect" enctype="multipart/form-data">
    <div class="grid">
      <div><label>Base de productos</label><input type="file" name="productos" accept=".xlsx,.xls,.csv" required></div>
      <div><label>Vademecum local o institucional</label><input type="file" name="vademecum" accept=".xlsx,.xls,.csv"></div>
      <div><label>Homologaciones historicas</label><input type="file" name="historicos" accept=".xlsx,.xls,.csv" multiple></div>
    </div>
    <p class="muted">El vademecum local se usa como fuente prioritaria. ISP/ANAMED queda registrado como fuente primaria de validacion externa cuando exista registro sanitario o revision posterior.</p>
    <button type="submit">Continuar</button>
  </form>
  <div id="uploadProgress" class="hidden">
    <p class="muted">Subiendo archivo y leyendo columnas para el mapeo...</p>
    <div class="progress-wrap"><div class="progress-bar" id="uploadBar">0%</div></div>
  </div>
</section>
<section>
  <h2>Criterio operativo</h2>
  <p>No homologa por parecido textual. Si no puede explicar equivalencia tecnica por componente, concentracion, forma, via, presentacion y unidad/factor, deriva a revision.</p>
</section>
<script>
const firstForm = document.querySelector("form[action='/inspect']");
const uploadProgress = document.getElementById("uploadProgress");
const uploadBar = document.getElementById("uploadBar");
if (firstForm) {
  firstForm.addEventListener("submit", () => {
    uploadProgress.classList.remove("hidden");
    firstForm.querySelector("button").disabled = true;
    let value = 0;
    window.setInterval(() => {
      value = Math.min(92, value + 7);
      uploadBar.style.width = `${value}%`;
      uploadBar.textContent = `${value}%`;
    }, 250);
  });
}
const previousJob = window.localStorage.getItem("homologacionCurrentJob");
if (previousJob) {
  fetch(`/progress?job=${encodeURIComponent(previousJob)}`, {cache: "no-store"})
    .then((response) => response.json())
    .then((data) => {
      if (data.state === "running" || data.state === "done") {
        document.getElementById("resumePanel").classList.remove("hidden");
        document.getElementById("resumeText").textContent = data.state === "done"
          ? "Hay un procesamiento terminado disponible para revisar o descargar."
          : `Hay un procesamiento activo en ${Number(data.percent || 0)}%.`;
        document.getElementById("resumeLink").href = `/watch?job=${encodeURIComponent(previousJob)}`;
      }
    })
    .catch(() => {});
}
</script>
"""


def run_job(job_id: str, product_path: Path, vademecum_path: Path | None, historical_paths: list[Path], mapping: dict[str, str], vad_mapping: dict[str, str]) -> None:
    def update(percent: int, message: str, extra: dict[str, object] | None = None) -> None:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].update({"percent": max(0, min(99, percent)), "message": message})
                if extra and "normalized" in extra:
                    rows = JOBS[job_id].setdefault("normalized", [])
                    if isinstance(rows, list):
                        rows.append(extra["normalized"])
                        del rows[:-80]
                if extra and "proposal" in extra:
                    rows = JOBS[job_id].setdefault("proposals", [])
                    if isinstance(rows, list):
                        rows.append(extra["proposal"])
                        del rows[:-120]

    try:
        frames = process_products(product_path, vademecum_path, historical_paths, mapping, vad_mapping, progress=update)
        update(92, "Generando archivo Excel final")
        OUTPUTS.mkdir(exist_ok=True)
        output = OUTPUTS / f"homologacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_workbook(frames, output)
        with JOBS_LOCK:
            JOBS[job_id].update({
                "state": "done",
                "percent": 100,
                "message": "Procesamiento terminado",
                "result_html": result_page(frames, output),
                "download_url": f"/download?file={urllib.parse.quote(str(output))}",
            })
    except Exception:
        with JOBS_LOCK:
            JOBS[job_id].update({
                "state": "failed",
                "percent": 100,
                "message": "Error de procesamiento",
                "error": traceback.format_exc(),
            })


def progress_page(job_id: str) -> str:
    safe_job = html.escape(job_id)
    return f"""
<section>
  <h2>Procesando base</h2>
  <div class="progress-wrap"><div class="progress-bar" id="bar">0%</div></div>
  <div class="status-line" id="status">Preparando procesamiento</div>
  <p id="doneActions" class="hidden"><span class="done">Listo.</span> <a id="downloadLink" class="button" href="#">Descargar Excel</a> <a id="resultLink" class="button secondary" href="#">Ver resumen final</a></p>
  <div id="error" class="error-box" style="display:none"></div>
</section>
<section class="live-grid">
  <div>
    <h2>Productos normalizados en curso</h2>
    <p class="muted small" id="normalizedCount">0 productos vistos</p>
    <div class="table-scroll">
      <table><thead><tr><th>Codigo</th><th>Descripcion</th><th>Familia</th><th>Activo/componente</th><th>Conc.</th><th>Forma</th><th>Via</th><th>Observacion</th></tr></thead><tbody id="normalizedRows"></tbody></table>
    </div>
  </div>
  <div>
    <h2>Propuestas generadas para revision</h2>
    <p class="muted small" id="proposalCount">0 propuestas generadas</p>
    <div class="table-scroll">
      <table><thead><tr><th>Codigo</th><th>Descripcion</th><th>Sugerido</th><th>Estado</th><th>Score</th><th>Motivo</th></tr></thead><tbody id="proposalRows"></tbody></table>
    </div>
  </div>
</section>
<script>
const jobId = "{safe_job}";
window.localStorage.setItem("homologacionCurrentJob", jobId);
function escapeHtml(value) {{
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[char]));
}}
function drawRows(targetId, rows, columns) {{
  const target = document.getElementById(targetId);
  target.innerHTML = rows.map((row) => `<tr>${{columns.map((col) => `<td>${{escapeHtml(row[col])}}</td>`).join("")}}</tr>`).join("");
}}
async function pollProgress() {{
  const response = await fetch(`/progress?job=${{encodeURIComponent(jobId)}}`, {{cache: "no-store"}});
  const data = await response.json();
  const percent = Math.max(0, Math.min(100, Number(data.percent || 0)));
  const bar = document.getElementById("bar");
  bar.style.width = `${{percent}}%`;
  bar.textContent = `${{percent}}%`;
  document.getElementById("status").textContent = data.message || "";
  drawRows("normalizedRows", data.normalized || [], ["codigo", "descripcion", "familia", "activo", "concentracion", "forma", "via", "observaciones"]);
  drawRows("proposalRows", data.proposals || [], ["codigo", "descripcion", "sugerido", "estado", "score", "motivo"]);
  document.getElementById("normalizedCount").textContent = `${{(data.normalized || []).length}} productos vistos`;
  document.getElementById("proposalCount").textContent = `${{(data.proposals || []).length}} propuestas generadas`;
  if (data.state === "done") {{
    const actions = document.getElementById("doneActions");
    actions.classList.remove("hidden");
    document.getElementById("downloadLink").href = data.download_url || "#";
    document.getElementById("resultLink").href = data.result_url || `/result?job=${{encodeURIComponent(jobId)}}`;
    return;
  }}
  if (data.state === "failed") {{
    const error = document.getElementById("error");
    error.style.display = "block";
    error.textContent = data.error || "Error de procesamiento";
    return;
  }}
  window.setTimeout(pollProgress, 600);
}}
pollProgress();
</script>
"""


def mapping_form(products: Path, vademecum: Path | None, historicos: list[Path], product_columns: list[str], detected: dict[str, str | None], vademecum_columns: list[str]) -> str:
    product_fields = [
        ("codigo_origen", "Codigo producto"),
        ("descripcion_origen", "Descripcion producto"),
        ("marca", "Marca"),
        ("laboratorio_titular", "Laboratorio / titular"),
        ("unidad_compra", "Unidad de compra"),
        ("unidad_consumo", "Unidad de consumo"),
        ("factor_conversion", "Factor conversion"),
        ("familia", "Familia"),
        ("categoria", "Categoria"),
        ("registro_sanitario", "Registro sanitario"),
    ]
    vademecum_fields = [
        ("marca", "Marca"),
        ("principio_activo", "Principio activo"),
        ("concentracion", "Concentracion"),
        ("forma", "Forma"),
        ("laboratorio", "Laboratorio"),
        ("registro_sanitario", "Registro sanitario"),
    ]
    rows = "".join(f"<div><label>{label}</label><select name='map_{key}'>{options(product_columns, detected.get(key))}</select></div>" for key, label in product_fields)
    vad_rows = "".join(f"<div><label>{label}</label><select name='vad_{key}'>{options(vademecum_columns, None)}</select></div>" for key, label in vademecum_fields)
    vad_section = f"<section><h2>Mapeo vademecum</h2><div class='grid'>{vad_rows}</div></section>" if vademecum else ""
    return f"""
<form method="post" action="/process">
  <input type="hidden" name="product_path" value="{html.escape(str(products))}">
  <input type="hidden" name="vademecum_path" value="{html.escape(str(vademecum or ''))}">
  {''.join(f'<input type="hidden" name="historical_path" value="{html.escape(str(path))}">' for path in historicos)}
  <section><h2>Mapeo base de productos</h2><div class="grid">{rows}</div></section>
  {vad_section}
  <button type="submit">Procesar y generar Excel</button>
</form>
"""


def result_page(frames, output: Path) -> str:
    summary = frames["RESUMEN"].head(10)
    review = frames["HOMOLOGACION_MULTICLINICA"]
    review = review[review["estado_homologacion"] != "HOMOLOGADO EXACTO"].head(50)
    metrics = "".join(f"<span class='pill'>{html.escape(str(row.indicador))}: {html.escape(str(row.valor))}</span>" for _, row in summary.iterrows())
    rows = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in ["codigo_madre", "clinica", "codigo_local", "descripcion_local", "estado_homologacion", "motivo_homologacion"])
        + "</tr>"
        for _, row in review.iterrows()
    )
    table = "<p>No hay casos pendientes en la vista previa.</p>" if not rows else f"<table><thead><tr><th>Codigo madre</th><th>Clinica</th><th>Codigo local</th><th>Descripcion</th><th>Estado</th><th>Motivo</th></tr></thead><tbody>{rows}</tbody></table>"
    return f"""
<section>
  <h2>Excel generado</h2>
  <p><a class="button" href="/download?file={urllib.parse.quote(str(output))}">Descargar workbook trazable</a></p>
  <p class="muted">El archivo incluye codigos madre, homologacion multiclinica, capa de insumos, variantes logisticas, patrones historicos aprendidos, diccionarios, conflictos, alertas y resumen.</p>
</section>
<section><h2>Resumen</h2>{metrics}</section>
<section><h2>Revision manual pendiente</h2>{table}</section>
"""


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8501"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"App disponible en http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
