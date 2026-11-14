# Воспроизвести прогнозы формул

1. Установить Python 3.14 и Tesseract с языками `rus`, `eng`. Для macOS выполнить `brew install tesseract tesseract-lang`.
2. Выполнить команды из корня репозитория.

```sh
cd test-core
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python prepare_model.py
python run_predictions.py examples/sets.json --reference examples/reference.json
```

3. Открыть `results/latest/report.html`. Найти формулы, ссылки на поля и сводку метрик. Для сырых данных открыть `predictions.json` и `metrics.json` в той же папке.

Ожидать 21 верный прогноз из 22, precision 100%, recall 95,45%, F1 97,67%.

## Запустить свои наборы

Скопировать [пример JSON](examples/sets.json). Указать `report` и список `sources`. Задать `targets` для отдельных полей. Считать относительные пути от файла JSON. Для оценки добавить `--reference` и путь к [эталону](examples/reference.json).

```sh
python run_predictions.py my-sets.json
```
