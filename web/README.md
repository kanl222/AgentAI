# Два отдельных сайта

- `api-console/` — техническая консоль для проверки API, библиотеки, индексации и очереди. Её отдаёт FastAPI на http://localhost:8000/; `/library` сохранён как совместимый адрес.
- `chat/` — пользовательский чат на Node.js 24 с отдельной сборкой и контейнером на http://localhost:3000/. Node отдаёт HTML/CSS/ES modules из `public/` и передаёт `/api/*` в AgentAI. Адрес backend задаётся переменной `AGENTAI_URL`. CORS и встроенный в сборку ключ не нужны. Для запуска без Docker: `cd web/chat`, затем `npm start`; тесты — `npm test`.

Каждый сайт запрашивает личный ключ API и хранит его только в памяти своей страницы. Доступ к истории и документам определяет AgentAI. Ключ не передаётся между сайтами.

Запуск из корня проекта:

```powershell
docker compose up -d --build api chat
```

Если старый Open WebUI ещё занимает порт 3000, сначала остановите его: `docker compose stop webui`. Его том и исходники в `frontend/` сохранены. Для возврата: остановить chat и запустить `docker compose --profile legacy-webui up -d webui`; старый интерфейс будет доступен на порту 3001.
