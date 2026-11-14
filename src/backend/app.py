import csv
import html
import io
import json
import os
from subprocess import SubprocessError
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from .engine import STATUS_LABELS, calculate, validate_change
from .extraction import SUPPORTED
from .ingest import is_reference_name, now, prepare_demo, process_files
from .store import RevisionConflict, Store
from .original import workbook_layout
from .numeric_regions import numeric_regions
from . import document_structure as structure
from structure_edits import EditError


BASE = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(os.environ.get("AUDIT_INPUT_DIR", BASE.parent / "вводные"))
store = Store()
store.recover_interrupted_jobs()
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="document-import")
app = FastAPI(title="Проверка отчетности", version="1.0.0")


class Change(BaseModel):
    revision: int
    kind: str
    target: str
    values: dict = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=2000)
    reset: bool = False


class CaseName(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    revision: int


class FormulaPreview(BaseModel):
    target: str
    rule: dict
    variables: list[dict] = Field(default_factory=list)


class FormulaPrediction(BaseModel):
    target: str = Field(min_length=1, max_length=200)
    revision: int
    document_ids: list[str] | None = Field(default=None, max_length=150)


class StructureEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0, strict=True)
    page: int = Field(ge=1, strict=True)
    node_id: str = Field(min_length=1, max_length=200)


class StructureTextEdit(StructureEdit):
    text: str = Field(min_length=1, max_length=20000)


class StructureSliceEdit(StructureEdit):
    action: Literal["slice"]
    x_cuts: list[float] = Field(default_factory=list, max_length=8)
    y_cuts: list[float] = Field(default_factory=list, max_length=8)
    parent_index: int = Field(ge=0, lt=36, strict=True)


def get_case(identifier):
    try:
        return store.get_case(identifier)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def get_document(case_id, document_id):
    case = get_case(case_id)
    document = next((item for item in case["documents"] if item["id"] == document_id), None)
    if document is None:
        raise HTTPException(404, "Документ не найден")
    path = store.files_dir(case_id) / document["filename"]
    if not path.is_file():
        raise HTTPException(404, "Исходный файл отсутствует")
    return document, path


def hydrate_pdf(case, identifier):
    document = next((document for document in case["documents"] if document["id"] == identifier), None)
    if not document or document.get("format") != "pdf" or "pdf_fields" in document:
        return
    from .edited_files import pdf_fields
    from .extraction import add_fact
    from .money import decimal_value
    path = store.files_dir(case["id"]) / document["filename"]
    document["pdf_fields"] = pdf_fields(path)
    for index, field in enumerate(document["pdf_fields"]):
        try: value = decimal_value(field["value"])
        except ValueError: continue
        add_fact(document,f"pdf_form_{index}",field["name"],format(value,'f'),{"page":field["page"],"rect":field["rect"],"text":field["value"],"locator":field["name"],"pdf_field":field["name"],"method":"pdf-form","confidence":100})


@app.get("/api/health")
def health():
    import shutil
    return {
        "status": "ok",
        "application": "report-verification",
        "document_structure": True,
        "structure_slice": True,
        "ocr": bool(shutil.which("tesseract")),
        "demo_available": SOURCE_DIR.is_dir(),
        "formula_prediction": True,
    }


@app.get("/api/cases")
def list_cases():
    return [{"id": case["id"], "name": case["name"], "created_at": case["created_at"], "revision": case["revision"],
             "reports": [{key: document[key] for key in ("id", "name", "format")} for document in case["documents"] if document["role"] == "report"],
             "sources": [{key: document[key] for key in ("id", "name", "format")} for document in case["documents"] if document["role"] == "source"]}
            for case in store.list_cases()]


@app.post("/api/cases/demo", status_code=202)
def import_demo():
    try:
        job, report_name = prepare_demo(store, SOURCE_DIR)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    executor.submit(process_files, store, job["id"], job["case_id"], "КФЗ · 3 этап · 2026", report_name)
    return job


@app.post("/api/cases", status_code=202)
async def upload_case(report: UploadFile = File(...), sources: list[UploadFile] = File(default=[]),
                      name: str = Form("Новая проверка"), settings: str = Form("{}"), reports: list[UploadFile] = File(default=[])):
    try:
        context = json.loads(settings)
        if not isinstance(context, dict) or set(context) - {"contract", "start", "end", "target_year", "tolerance"}:
            raise ValueError("Некорректные настройки проверки")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    uploads = [report, *reports, *sources]
    if len(uploads) > 150:
        raise HTTPException(400, "За один раз можно загрузить до 150 файлов")
    case_id, job_id = uuid4().hex, uuid4().hex
    directory = store.files_dir(case_id)
    directory.mkdir(parents=True)
    total_size, report_name, report_names = 0, None, []
    try:
        for index, upload in enumerate(uploads):
            filename = Path((upload.filename or f"document-{index}").replace("\\", "/")).name
            if Path(filename).suffix.lower() not in SUPPORTED:
                raise ValueError(f"Неподдерживаемый формат файла {filename}")
            if is_reference_name(filename):
                if index <= len(reports):
                    raise ValueError("Эталон ответов нельзя использовать в качестве отчета")
                continue
            if (directory / filename).exists():
                filename = f"{index}_{filename}"
            content = await upload.read(50 * 1024 * 1024 + 1)
            total_size += len(content)
            if len(content) > 50 * 1024 * 1024 or total_size > 300 * 1024 * 1024:
                raise ValueError("Максимум 50 МБ на файл и 300 МБ на проверку")
            (directory / filename).write_bytes(content)
            if index == 0:
                report_name = filename
            elif index <= len(reports):
                report_names.append(filename)
    except (ValueError, OSError) as exc:
        import shutil
        shutil.rmtree(directory.parent, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc
    finally:
        for upload in uploads:
            await upload.close()
    job = {"id": job_id, "status": "queued", "progress": 0, "case_id": case_id, "created_at": now()}
    store.save_job(job)
    executor.submit(process_files, store, job_id, case_id, name[:200], report_name, context, report_names)
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    try:
        return store.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/cases/{case_id}")
def case_result(case_id: str):
    return calculate(get_case(case_id))


@app.patch("/api/cases/{case_id}")
def rename_case(case_id: str, change: CaseName):
    try:
        case = store.rename_case(case_id, change.name, change.revision)
    except KeyError as exc:
        raise HTTPException(404, "Проверка не найдена") from exc
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return calculate(case)


@app.post("/api/cases/{case_id}/changes")
def change_case(case_id: str, change: Change):
    case = get_case(case_id)
    if change.kind == "pdf_field":
        hydrate_pdf(case, change.target.partition(":")[0])
    for variable in (change.values.get("variables", []) if isinstance(change.values.get("variables", []), list) else []):
        if isinstance(variable, dict) and isinstance(variable.get("evidence"), dict) and variable["evidence"].get("pdf_field") and isinstance(variable.get("document_id"), str):
            hydrate_pdf(case, variable["document_id"])
    if case["revision"] != change.revision:
        raise HTTPException(409, "Проверка уже изменилась. Обновите страницу и повторите действие.")
    key = f"{change.kind}:{change.target}"
    current = calculate(case)
    previous = case.get("overrides", {}).get(key)
    if change.kind == "fact":
        fact = next((fact for document in current["documents"] for fact in document["facts"].values() if fact["id"] == change.target), None)
        previous = {"value": fact["value"]} if fact else previous
    elif change.kind == "reported" and change.target in current["nodes"]:
        previous = {"value": current["nodes"][change.target]["reported"]}
    elif change.kind == "context":
        previous = {field: current["context"].get(field) for field in change.values}
    elif change.kind == "document":
        document = next((document for document in current["documents"] if document["id"] == change.target), {})
        previous = {field: document.get("included" if field == "include" else field) for field in change.values}
    elif change.kind == "check" and change.target in current["nodes"]:
        previous = {"rule": current["nodes"][change.target]["rule"]}
    elif change.kind in ("cell", "text", "pdf_field"):
        identifier, _, locator = change.target.partition(":")
        document = next((document for document in current["documents"] if document["id"] == identifier), {})
        if change.kind == "cell":
            previous = {"value":document.get("cells", {}).get(locator, {}).get("raw", "")}
        elif change.kind == "pdf_field":
            previous = {"value":next((field["value"] for field in document.get("pdf_fields", []) if field["name"] == locator), "")}
        else:
            base, separator, cell = locator.rpartition("#")
            text = next((block["text"] for block in document.get("blocks", []) if block.get("locator") == (base if separator else locator)), "")
            previous = {"value":text.split(" | ")[int(cell)] if separator and cell.isdigit() and int(cell)<len(text.split(" | ")) else text}
    try:
        if change.reset:
            if key not in case["overrides"]:
                raise ValueError("Для этого поля нет ручного исправления")
            candidate = case
            candidate["overrides"].pop(key)
            calculate(candidate)
        else:
            candidate = validate_change(case, change.kind, change.target, change.values)
        candidate["revision"] += 1
        candidate["audit"].append({"id": uuid4().hex, "at": now(), "kind": change.kind, "target": change.target,
                                   "before": previous, "after": None if change.reset else candidate["overrides"][key],
                                   "reason": change.reason, "revision": candidate["revision"]})
        store.save_case(candidate, expected_revision=change.revision)
        return calculate(candidate)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/cases/{case_id}/formula-preview")
def formula_preview(case_id: str, draft: FormulaPreview):
    try:
        case = get_case(case_id)
        for variable in draft.variables:
            if variable.get("evidence", {}).get("pdf_field"):hydrate_pdf(case,variable["document_id"])
        candidate = validate_change(case, "check", draft.target, {"rule": draft.rule, "variables": draft.variables})
        node = calculate(candidate)["nodes"][draft.target]
        return {key: node.get(key) for key in ("expected", "reported", "delta", "status", "inputs", "blocked_by")}
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/cases/{case_id}/formula-prediction")
def formula_prediction(case_id: str, request: FormulaPrediction):
    from .prediction_service import suggest
    case = get_case(case_id)
    if case["revision"] != request.revision:
        raise HTTPException(409, "Проект изменился. Обновите его перед предсказанием.")
    try:
        return suggest(case, store, request.target, request.document_ids)
    except (ValueError, ImportError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/cases/{case_id}/documents/{document_id}/file")
def download_document(case_id: str, document_id: str):
    document, path = get_document(case_id, document_id)
    return FileResponse(path, filename=document["name"])


@app.get("/api/cases/{case_id}/documents/{document_id}/preview")
def preview_document(case_id: str, document_id: str, page: int = 1, rect: str | None = None):
    document, path = get_document(case_id, document_id)
    if page < 1 or page > document["page_count"]:
        raise HTTPException(400, "Страница отсутствует")
    region = None
    if rect:
        try:
            region = [float(value) for value in rect.split(",")]
            if len(region) != 4 or not all(0 <= value <= 1 for value in region) or region[0] >= region[2] or region[1] >= region[3]:
                raise ValueError("Некорректные координаты")
        except ValueError as exc:
            raise HTTPException(400, "Некорректные координаты фрагмента") from exc
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as pdf:
            source_page = pdf[page - 1]
            clip = None
            if region:
                clip = pymupdf.Rect(region[0] * source_page.rect.width, region[1] * source_page.rect.height,
                                    region[2] * source_page.rect.width, region[3] * source_page.rect.height)
                clip += (-3, -3, 3, 3)
                clip &= source_page.rect
            pixmap = source_page.get_pixmap(dpi=300 if region else 170, clip=clip)
            data = pixmap.tobytes("png")
    elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            if region:
                image = image.crop((max(0, region[0] * image.width - 12), max(0, region[1] * image.height - 12),
                                    min(image.width, region[2] * image.width + 12), min(image.height, region[3] * image.height + 12)))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
    else:
        raise HTTPException(400, "Для этого формата доступен табличный или текстовый просмотр")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/cases/{case_id}/documents/{document_id}/original-sheet")
def original_sheet(case_id: str, document_id: str, sheet: str | None = None, row: int = 1, column: int = 1):
    document, path = get_document(case_id, document_id)
    if path.suffix.lower() != ".xlsx" or row < 1 or column < 1:
        raise HTTPException(400, "Неверный формат или диапазон листа")
    try:
        effective = next(item for item in calculate(get_case(case_id))["documents"] if item["id"] == document_id)
        return workbook_layout(path, sheet, row, column, effective["cells"], effective["report_fields"])
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/cases/{case_id}/documents/{document_id}/workbook")
def spreadsheet_workbook(case_id: str, document_id: str):
    original, path = get_document(case_id, document_id)
    if original["format"] != "xlsx":
        raise HTTPException(400, "Редактор доступен для XLSX")
    from .spreadsheet_editor import workbook_snapshot
    document = next(item for item in calculate(get_case(case_id))["documents"] if item["id"] == document_id)
    return workbook_snapshot(path, document)


@app.get("/api/cases/{case_id}/documents/{document_id}/rich-document")
def rich_document(case_id: str, document_id: str):
    original, path = get_document(case_id, document_id)
    if original["format"] != "docx":
        raise HTTPException(400, "Редактор доступен для DOCX")
    from .rich_document import snapshot_from_docx
    document = next(item for item in calculate(get_case(case_id))["documents"] if item["id"] == document_id)
    try:
        return snapshot_from_docx(path, document)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/cases/{case_id}/documents/{document_id}/rich-document/export/{format}")
def export_rich_document(case_id: str, document_id: str, format: str):
    if format not in ("pdf", "html"):
        raise HTTPException(400, "Доступен экспорт PDF или HTML")
    from .rich_export import html_document, pdf_bytes
    snapshot = rich_document(case_id, document_id)
    try:
        data = pdf_bytes(snapshot) if format == "pdf" else html_document(snapshot).encode("utf-8")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    mime = "application/pdf" if format == "pdf" else "text/html; charset=utf-8"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="document-{document_id}.{format}"'})


@app.get("/api/cases/{case_id}/documents/{document_id}/numeric-regions")
def document_numeric_regions(case_id: str, document_id: str):
    document, path = get_document(case_id, document_id)
    if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return []
    return numeric_regions(document, path)


@app.get("/api/cases/{case_id}/documents/{document_id}/fields")
def document_fields(case_id: str, document_id: str):
    case = get_case(case_id)
    hydrate_pdf(case, document_id)
    return next((document.get("pdf_fields", []) for document in calculate(case)["documents"] if document["id"] == document_id), [])


def document_structure(case_id, document_id, change=None):
    document, path = get_document(case_id, document_id)
    if path.suffix.lower() not in structure.SUPPORTED:
        raise HTTPException(400, "Карта полей доступна для PDF и изображений")
    try:
        if change is None:
            return structure.get_structure(store, case_id, document, path)
        return structure.update_structure(store, case_id, document, path, change)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, SubprocessError) as exc:
        raise HTTPException(503, "Не удалось распознать поля. Для сканов нужен Tesseract с языками rus и eng.") from exc
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Не удалось прочитать структуру документа.") from exc


@app.get("/api/cases/{case_id}/documents/{document_id}/structure")
def get_document_structure(case_id: str, document_id: str):
    return document_structure(case_id, document_id)


@app.patch("/api/cases/{case_id}/documents/{document_id}/structure")
def patch_document_structure(case_id: str, document_id: str, change: StructureTextEdit | StructureSliceEdit):
    return document_structure(case_id, document_id, change)


@app.get("/api/cases/{case_id}/documents/{document_id}/edited-file")
def download_edited_document(case_id: str, document_id: str):
    original, path = get_document(case_id, document_id)
    case = get_case(case_id)
    hydrate_pdf(case, document_id)
    document = next(item for item in calculate(case)["documents"] if item["id"] == document_id)
    from .edited_files import edited_file
    try:
        data, mime, extension = edited_file(path, document, original)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(data, media_type=mime, headers={"Content-Disposition":f'attachment; filename="edited-{document_id}.{extension}"'})


@app.get("/api/cases/{case_id}/export/{format}")
def export_case(case_id: str, format: str):
    result = calculate(get_case(case_id))
    headers = {"Content-Disposition": f'attachment; filename="verification-{case_id[:8]}.{format}"'}
    if format == "json":
        return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["Строка", "Код", "Показатель", "В отчете", "По источникам", "Разница", "Статус", "Сверка с первичкой", "Формула", "Сомнения в зависимостях"])
        for check in result["checks"]:
            writer.writerow([check["row_title"], check["code"], check["title"], check["reported"], check["expected"], check["delta"],
                             STATUS_LABELS[check["status"]], STATUS_LABELS[check["direct_status"]],
                             check["rule"]["expression"] if check["rule"] else "", ", ".join(check["blocked_by"])])
        return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
    if format == "html":
        return HTMLResponse(render_conclusion(result), headers=headers)
    raise HTTPException(404, "Доступны json, csv и html")


def render_conclusion(result):
    escape = lambda value: html.escape(str(value if value is not None else "—"))
    rows = []
    for check in result["checks"]:
        evidence = []
        for item in check["inputs"]:
            source = result["nodes"][item["ref"]]
            location = source.get("evidence", source.get("reported_evidence", {}))
            document = next((document for document in result["documents"] if document["id"] == location.get("document_id")), None)
            evidence.append(f"{item['name']} = {item['value']} · {document['name'] if document else source.get('title', '')} · {location.get('locator', '')}")
        formula = check["rule"]["expression"] if check["rule"] else "Ожидаемое значение не установлено"
        substitution = check.get("substitution")
        proof = f"{substitution} = {check['expected']}" if substitution else ""
        explanation = "<br>".join(escape(message) for message in check.get("explanation", []))
        rows.append(f"<tr><td>{escape(check['row_title'])}<br>{escape(check['code'])}</td><td>{escape(check['title'])}</td>"
                    f"<td>{escape(check['reported'])}</td><td>{escape(check['expected'])}</td><td>{escape(check['delta'])}</td>"
                    f"<td>{escape(STATUS_LABELS[check['status']])}</td></tr>"
                    f"<tr><td colspan='6'><b>{escape(formula)}</b><br>{escape(proof)}<br>{explanation}<br>{'<br>'.join(escape(item) for item in evidence)}"
                    f"<br>{escape(check['note'])}<br>Сомнения в зависимостях · {escape(', '.join(check['blocked_by']) or 'нет')}</td></tr>")
    audit = "".join(f"<li>{escape(item['at'])} · {escape(item['target'])} · {escape(item['reason'])} · {escape(json.dumps(item['after'], ensure_ascii=False))}</li>" for item in result["audit"])
    return f"""<!doctype html><html lang='ru'><meta charset='utf-8'><title>Заключение о проверке</title>
    <style>body{{font:14px system-ui;margin:40px;color:#202b3b}}h1{{font-size:26px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;text-align:left;border:1px solid #dbe0e7}}tr:nth-child(even){{background:#f5f7fa;font-size:12px}}@media print{{body{{margin:12px}}tr{{break-inside:avoid}}}}</style>
    <h1>Заключение о проверке отчетности</h1><p>{escape(result['name'])} · ревизия {result['revision']}</p>
    <p>Договор {escape(result['context']['contract'])} · период {escape(result['context']['start'])} — {escape(result['context']['end'])}</p>
    <p>{escape(result['summary']['conclusion'])}</p><table><thead><tr><th>Строка</th><th>Показатель</th><th>Отчет</th><th>Источники</th><th>Разница</th><th>Статус</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    <h2>Ручные изменения</h2><ul>{audit or '<li>Изменений нет</li>'}</ul></html>"""


FRONTEND = BASE / "frontend" / "dist"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="interface")
else:
    @app.get("/")
    def no_frontend():
        return HTMLResponse("<p>Соберите интерфейс командой npm run build в src/frontend или запустите python3 src/run.py.</p>", status_code=503)
