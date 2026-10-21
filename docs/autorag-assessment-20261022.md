# Проверка AutoRAG — 22 октября 2026

Проверены первичные источники; установка и проверка выполнения не проводились.
Это первоначальная оценка до эксперимента. Последующий успешный запуск Legacy
описан в [отчёте пилота](autorag-pilot-20261022.md); текущий поиск после него
переведён на [Qwen embeddings](qwen-embedding-migration-20261022.md).

## Два разных назначения

Текущий репозиторий содержит AutoRAG Agent 2.0 — персонального библиотекаря для поиска по файлам. Прежний Python-инструмент автоматического выбора RAG-конвейера перемещён в `legacy/`. Для него заявлен режим сопровождения: исправления, обновления зависимостей и публикации PyPI продолжаются; новые возможности разрабатываются для Agent 2.0. [README](https://github.com/Marker-Inc-Korea/AutoRAG), [legacy README](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/legacy/README.md).

Legacy предназначен для сравнения вариантов на собственных данных; нужны корпус и набор вопросов с эталонами в Parquet. Для AgentAI это кандидат на отдельный эксперимент по выбору параметров, с последующим переносом подтверждённых улучшений в существующий конвейер. Это рекомендация, а не проверенная интеграция. [Требования к данным](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/legacy/README.md#data-creation).

## Сопровождение и окружение

Страница релизов на момент проверки показывает v2.10.6 от 20 октября. Это релиз Agent 2.0, не свидетельство нового выпуска Python-пакета. Страница коммитов возвращалась с устаревшим снимком, поэтому точная дата последнего коммита не установлена. [Релизы](https://github.com/Marker-Inc-Korea/AutoRAG/releases).

Legacy ограничивает Python версиями `>=3.10,<3.13`; зависимости включают многочисленные библиотеки моделей, векторных хранилищ и интерфейсов. Разумно проверять его в отдельном окружении. Поддержка Windows в legacy README явно не обещана; работоспособность выбранных необязательных компонентов требует проверки. [pyproject.toml](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/legacy/pyproject.toml).

Agent 2.0 опубликован как `@autorag/librarian`; package.json требует Node.js >=24. README описывает Windows, macOS и Linux; для русского корпуса доступен тег `ru`, язык по умолчанию — корейский и английский. [package.json](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/package.json), [настройка](https://github.com/Marker-Inc-Korea/AutoRAG#installation--setup).

## Лицензия

Текущие LICENSE обеих частей — Apache-2.0. Она допускает коммерческое использование и изменения. При распространении нужно выполнить условия сохранения лицензии, уведомлений и обозначения изменений; права на товарные знаки не предоставляются. У зависимостей и внешних компонентов собственные условия. [Основная лицензия](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/LICENSE), [legacy LICENSE](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/legacy/LICENSE), [NOTICE](https://github.com/Marker-Inc-Korea/AutoRAG/blob/main/NOTICE).
