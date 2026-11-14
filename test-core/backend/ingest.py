import hashlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .extraction import SUPPORTED, docx_blocks, extract_document, report_period

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def default_context(documents, notes):
    contracts = re.findall(r"\d{2}/\d{2}/\d{2}-\d{2}", notes)
    dates = re.findall(r"\d{2}\.\d{2}\.20\d{2}", notes)
    context = {"contract": contracts[0] if contracts else "", "start": "", "end": "", "target_year": "", "tolerance": "0.01"}
    if len(dates) >= 2:
        context["start"], context["end"] = [datetime.strptime(value, "%d.%m.%Y").date().isoformat() for value in dates[:2]]
        context["target_year"] = context["end"][:4]
    report = next((document for document in documents if document["role"] == "report"), None)
    if report and not context["start"] and not context["end"]:
        context.update(report_period(report))
    if report and not context["contract"]:
        context["contract"] = report["facts"].get("contract", {}).get("value", "")
    if report and not context["target_year"]:
        years = sorted({field["row"] for field in report["report_fields"] if re.fullmatch(r"20\d{2}", field["row"])})
        if years:
            context["target_year"] = years[-1]
    return context


def is_reference_name(name):
    return (name.startswith("!") or name.casefold() == "описание_сверки.txt" or
            bool(re.search(r"правильн.*ответ|эталон|ground.?truth", name, re.I)))


def process_files(store, job_id, case_id, name, report_name, settings=None, additional_reports=None):
    job = store.get_job(job_id)
    directory = store.files_dir(case_id)
    documents, notes, ignored = [], "", []
    paths = sorted(directory.iterdir(), key=lambda path: ("(1)" in path.name, "копия" in path.name.lower(), path.name))
    report_hash = None
    try:
        for index, path in enumerate(paths):
            job.update(status="processing", progress=round(index / max(len(paths), 1) * 90), current_file=path.name)
            store.save_job(job)
            if is_reference_name(path.name):
                ignored.append({"name": path.name, "reason": "Эталонные ответы отделены от первичных документов"})
                continue
            if path.name.startswith("00_"):
                if path.suffix.lower() == ".txt":
                    notes += path.read_text(encoding="utf-8-sig") + "\n"
                elif path.suffix.lower() == ".docx":
                    notes += "\n".join(block["text"] for block in docx_blocks(path)) + "\n"
                continue
            if path.suffix.lower() not in SUPPORTED:
                ignored.append({"name": path.name, "reason": "Неподдерживаемый формат"})
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            role = "report" if path.name in {report_name, *(additional_reports or [])} else "source"
            if report_hash and digest == report_hash and role != "report":
                role = "reference"
            document = extract_document(path, role)
            if role == "report":
                report_hash = digest
                fields = document["report_fields"]
                native_pdf = (document["format"] == "pdf" and not document.get("pdf_fields")
                              and any(block["method"] == "pdf-text" for block in document["blocks"])
                              and not any(field["evidence"]["method"] == "ocr-cell" for field in fields))
                if not document["errors"] and (not fields or native_pdf):
                    from .document_structure import SUPPORTED as STRUCTURED, baseline
                    from .prediction_fields import structured_report_fields

                    if path.suffix.lower() in STRUCTURED:
                        try:
                            parsed = structured_report_fields(document, baseline(store, document, path))
                        except (OSError, RuntimeError, ValueError) as exc:
                            logger.warning("Could not extract report structure for %s", path.name, exc_info=True)
                            if not fields:
                                document["errors"].append(str(exc))
                            parsed = []
                        if parsed:
                            document["report_fields"] = parsed
                            document["structured_report"] = True
            document["filename"] = path.name
            documents.append(document)
        reports = [document for document in documents if document["role"] == "report"]
        reports.sort(key=lambda document: document["filename"] != report_name)
        if not reports:
            raise ValueError("Выберите итоговый отчет перед загрузкой")
        if not reports[0]["report_fields"]:
            raise ValueError("В отчете не найдены числовые показатели. " + "; ".join(reports[0]["errors"]))
        for document in documents:
            if document["role"] != "report" and document["hash"] == reports[0]["hash"]:
                document["role"] = "reference"
        context = default_context(documents, notes)
        context.update({key: value for key, value in (settings or {}).items() if value})
        case = {"id": case_id, "name": name, "created_at": now(), "revision": 1,
                "report_id": reports[0]["id"], "context": context, "documents": documents,
                "report_ids": [report["id"] for report in reports],
                "overrides": {}, "audit": [], "ignored_files": ignored}
        from .engine import calculate
        calculate(case)
        store.save_case(case)
        job.update(status="completed", progress=100, case_id=case_id, current_file=None)
    except Exception as exc:
        job.update(status="failed", error=str(exc), current_file=None)
    store.save_job(job)


def prepare_demo(store, source_dir):
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise ValueError("Папка ./вводные не найдена. Загрузите отчет и документы через интерфейс.")
    paths = [path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED]
    candidates = [path for path in paths if re.search(r"карточка|отчет|кфз", path.name, re.I) and "(1)" not in path.name]
    if not candidates:
        raise ValueError("В папке ./вводные не найден итоговый отчет")
    report = sorted(candidates)[0]
    case_id, job_id = uuid4().hex, uuid4().hex
    directory = store.files_dir(case_id)
    directory.mkdir(parents=True)
    for path in paths:
        # Answer keys are never copied into the case's evidence storage.
        if not is_reference_name(path.name):
            shutil.copy2(path, directory / path.name)
    job = {"id": job_id, "status": "queued", "progress": 0, "created_at": now(), "case_id": case_id}
    store.save_job(job)
    return job, report.name
