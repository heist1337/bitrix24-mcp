# Передача коллегам

Передать ссылку на репозиторий. Коллега клонирует его, устанавливает uv,
копирует `.env.example` в `.env` и заполняет `BITRIX_PORTAL_URL` и
`BITRIX_WEBHOOK_URL` своим HTTPS-порталом и входящим вебхуком.
На Windows: `Copy-Item .env.example .env`.

Портал указывается только как origin, например `https://portal.example.org`.
Вебхук должен принадлежать этому же порталу и иметь вид
`https://portal.example.org/rest/USER_ID/WEBHOOK_KEY/`.
Секреты, signed URLs и данные задач не входят в публичный репозиторий.
В `mcp.example.json` заменить пути к checkout и приватному env-файлу.

Проверка без Bitrix, токенов и сети:

```bash
uv sync --locked
uv run python -m unittest discover -b -s tests
```

Начать с `operation="catalog"` у каждого инструмента. Для живой проверки:
`bitrix_read(operation="profile")`, затем чтение одной страницы задач и
`bitrix_disk(operation="storages")`. `capabilities` проверяет выданные scopes.
Версия Bitrix и права вебхука могут ограничивать отдельные методы.

Для записи сначала построить план с `apply=false`. Выполнение с `apply=true`
требует конкретного поручения с целью и содержимым. После неизвестного результата
сверить объект перед повтором. Не отправлять тестовые сообщения коллегам и не
создавать рабочие задачи ради проверки установки.
