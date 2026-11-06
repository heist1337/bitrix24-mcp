# Bitrix24 MCP

[![Check](https://github.com/heist1337/bitrix24-mcp/actions/workflows/check.yml/badge.svg)](https://github.com/heist1337/bitrix24-mcp/actions/workflows/check.yml)
[![MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

Задачи, календарь, Диск, сотрудники и корпоративные разделы Bitrix через
[FastMCP](https://gofastmcp.com/). Самостоятельный stdio-сервер: Python, uv и
приватный вебхук. Windmill, PostgreSQL и общий шлюз не нужны.

```bash
git clone https://github.com/heist1337/bitrix24-mcp.git
cd bitrix24-mcp
cp .env.example .env
# Указать BITRIX_PORTAL_URL и свой BITRIX_WEBHOOK_URL в .env.
uv run --locked --env-file .env bitrix-mcp
```

| Инструмент | Назначение |
| --- | --- |
| `bitrix_read` | Задачи, комментарии, каталог методов и разрешённые корпоративные разделы |
| `bitrix_disk` | Хранилища, папки, метаданные и содержимое выбранных файлов |
| `bitrix_write` | Планирование и выполнение явно порученных изменений |

Начать с `operation="catalog"`. Чтение возвращает одну страницу и признаки
продолжения. Для записи сначала проверить план с `apply=false`; `apply=true`
выполняет один запрос. При неизвестном результате сверить объект перед повтором.
Отправка сообщений требует отдельного поручения с адресатом и текстом.
Удаление, публичные ссылки, изменение ACL, подмена автора и произвольный REST
не опубликованы. Вебхук и загрузки привязаны к HTTPS-порталу из `BITRIX_PORTAL_URL`;
права провайдера проверяются самим Bitrix.

Работает с облачным Bitrix24 и собственным HTTPS-порталом. Например:

```dotenv
BITRIX_PORTAL_URL=https://your-team.bitrix24.ru
BITRIX_WEBHOOK_URL=https://your-team.bitrix24.ru/rest/USER_ID/WEBHOOK_KEY/
```

Портал указывается без `/rest/`, пароля, query и fragment. Каждый коллега
использует собственный вебхук; сообщения и рабочие данные не входят в репозиторий.
На Windows вместо `cp` использовать `Copy-Item .env.example .env`.

Настройка клиента — [mcp.example.json](mcp.example.json). Вебхук хранить в
приватном env-файле вне Git. Для переноса нужны checkout и этот файл; данные
Bitrix остаются на портале. Синтетические тесты не обращаются к корпоративной сети.

```bash
uv sync --locked
uv run ruff check src tests
uv run python -m unittest discover -b -s tests
uv build
```

[Передача коллегам и проверка](SHARING.md) · [MIT](LICENSE)
