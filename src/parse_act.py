"""Распознать фотографию акта и сохранить реквизиты в JSON.

    python3 src/parse_act.py photo.png
    python3 src/parse_act.py photo.jpg -o result.json
    python3 src/parse_act.py ocr.txt --text
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path


NUMBER = r"[A-Za-zА-Яа-яЁё0-9]+(?:[-./][A-Za-zА-Яа-яЁё0-9]+)*"
AMOUNT = r"([0-9]{1,3}(?: [0-9]{3})+|[0-9]+)[,.]([0-9]{2})\s*(?:руб\.?|₽|RUB)"


def parse_act(text):
    # Keep line boundaries but normalize spacing, including nonbreaking spaces.
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    review = []

    def field(name, pattern):
        matches = [
            (i, match[1].strip())
            for i, line in enumerate(lines)
            if (match := re.fullmatch(pattern, line, re.IGNORECASE))
        ]
        if len(matches) == 1 and matches[0][1]:
            return matches[0]
        review.append(name)
        return None, None

    _, act_number = field("act_number", r"АКТ\s*(?:№|N[o°º]|#)\s*(.*)")
    _, raw_date = field("date", r"Дата\s*:\s*(.*)")
    _, contract_number = field("contract_number", r"Договор\s*:\s*(?:№\s*)?(.*)")
    contractor_line, contractor = field("contractor", r"Исполнитель\s*:\s*(.*)")
    amount_line, raw_amount = field("amount", r"Сумма\s*:\s*(.*)")

    def validate_number(name, value):
        if value is not None and not re.fullmatch(NUMBER, value):
            review.append(name)
            return None
        return value

    act_number = validate_number("act_number", act_number)
    contract_number = validate_number("contract_number", contract_number)

    date = None
    if raw_date is not None:
        try:
            date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
        except ValueError:
            review.append("date")

    amount = currency = None
    if raw_amount is not None:
        match = re.fullmatch(AMOUNT, raw_amount, re.IGNORECASE)
        if match:
            whole = match[1].replace(" ", "")
            amount = format(Decimal(f"{whole}.{match[2]}"), ".2f")
            currency = "RUB"
        else:
            review.append("amount")

    description = None
    if contractor_line is not None and amount_line is not None:
        description = " ".join(lines[contractor_line + 1 : amount_line]) or None
    if description is None:
        review.append("description")

    return {
        "document_type": "act",
        "act_number": act_number,
        "date": date,
        "contract_number": contract_number,
        "contractor": contractor,
        "description": description,
        "amount": amount,
        "currency": currency,
        "review_fields": review,
    }


def recognize_image(source):
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "Tesseract не найден. На macOS установите его командой "
            "`brew install tesseract tesseract-lang`."
        )
    result = subprocess.run(
        ["tesseract", str(source.resolve()), "stdout", "-l", "rus+eng", "--psm", "6"],
        check=True, capture_output=True, encoding="utf-8", timeout=60,
    )
    # Tesseract can exit successfully even if only one language was loaded.
    if "Failed loading language" in result.stderr:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def save_json(destination, content):
    """Replace the output only after the complete JSON has been written."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="путь к фотографии (PNG, JPEG, TIFF)")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="путь к JSON (по умолчанию рядом с исходным файлом, с расширением .json)",
    )
    parser.add_argument("--text", action="store_true", help="читать UTF-8 текст вместо фотографии")
    args = parser.parse_args()
    output = args.output if args.output is not None else args.source.with_suffix(".json")

    if not args.source.is_file():
        parser.error(f"Исходный файл не найден или не является файлом: {args.source}")
    if output.resolve() == args.source.resolve():
        parser.error("Путь к результату совпадает с исходным файлом. Укажите другой путь через -o.")

    try:
        if args.text:
            text = args.source.read_text(encoding="utf-8-sig")
        else:
            text = recognize_image(args.source)
        if not text.strip():
            raise RuntimeError("Текст не найден. Проверьте читаемость фотографии или содержимое файла.")
        result = json.dumps(parse_act(text), ensure_ascii=False, indent=2) + "\n"
        save_json(output, result)
    except subprocess.CalledProcessError as exc:
        parser.exit(1, f"Tesseract: {exc.stderr.strip()}\n")
    except subprocess.TimeoutExpired:
        parser.exit(1, "Tesseract не завершил распознавание за 60 секунд.\n")
    except (OSError, UnicodeError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")

    print(result, end="")
    print(f"Сохранено: {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
