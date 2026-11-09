# NovaProxy CLI

Кроссплатформенный терминальный клиент для API-шлюза **NovaProxy** с интерфейсом
в стиле OpenCode: центрированный баннер, сообщения с цветной границей,
статус-бар с чипами, overlay-диалоги, permission-система для инструментов.

*A cross-platform terminal client for the **NovaProxy** API gateway with an
OpenCode-style TUI: centered banner, bordered messages, status bar chips,
overlay dialogs and a permission system for tool calls.*

---

## Установка (одна команда)

### Android (Termux)

```sh
curl -sSL https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.sh | sh
```

### Linux / macOS

```sh
curl -sSL https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.ps1 | iex
```

Установщик сам найдёт или поставит Python 3.8+ и git, скачает клиент и
добавит команду `novaproxy` в PATH.

---

## Первый запуск

```sh
novaproxy
```

При первом запуске появится приглашение:

```
Введите ваш API-ключ (формат px_live_...):
```

Вставьте ключ `px_live_...` — клиент проверит его на сервере и сохранит в
`~/.novaproxy/config.json` (права 0600). При следующих запусках ключ
подтягивается автоматически и перепроверяется на сервере: если он отозван
или истёк, клиент попросит ввести новый.

Смена ключа: `/key` в чате, или `novaproxy --auth`, или
`novaproxy --key px_live_...`.

---

## Использование

### Интерактивный режим

```sh
novaproxy
```

Пишите запросы в строку ` > `. Внизу — статус-бар: подсказка, счётчик
токенов, имя сессии и текущая модель.

### Одиночный запрос

```sh
novaproxy "объясни этот код" < main.py
cat error.log | novaproxy "найди причину ошибки"
novaproxy -m gpt-6-astra --reasoning high "реши задачу"
```

Ответ стримится в stdout, служебная строка — в stderr.

### Файлы в контексте через @

```
> найди баг в @src/main.py
> что в этой папке? @src/
```

### Инструменты (function calling)

Модели с поддержкой function calling получают локальные инструменты:
`read_file`, `write_file`, `edit_file`, `bash`, `search`, `web_search`
плюс инструменты подключённых MCP-серверов.

Опасные действия (`bash`, запись файлов) спрашивают разрешения:
`Allow (a)` / `Allow for session (s)` / `Deny (d)`. Политика настраивается
через `/permissions`. Управление инструментами: `/tools list`,
`/tools disable bash`, `/tools enable bash`, `/tools` — вкл/выкл всех.

### MCP-серверы

```
/mcp add fs npx -y @modelcontextprotocol/server-filesystem /sdcard
/mcp          # статус
/mcp tools    # инструменты
/mcp del fs   # удалить
```

Или в конфиге `~/.novaproxy/config.json`:

```json
{
  "mcp_servers": {
    "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/sdcard"] },
    "remote": { "url": "http://localhost:3000/mcp" }
  }
}
```

### Слэш-команды

| Команда | Действие |
|---|---|
| `/help` | справка |
| `/init` | анализ проекта и создание/обновление `NOVA.md` |
| `/compact` | сжать сессию в сводку |
| `/new` | новая сессия (текущая сохраняется) |
| `/clear`, `/reset` | сброс контекста |
| `/model` | диалог выбора модели / `/model <id>` |
| `/models` | список моделей |
| `/mode` | code / chat / reasoning |
| `/system` | системный промпт (`/system reset` — сброс) |
| `/mcp` | MCP-серверы |
| `/tools` | управление инструментами |
| `/permissions` | разрешения bash/write |
| `/key` | сменить API-ключ |
| `/balance` | баланс ключа |
| `/cost` | токены сессии |
| `/status` | информация о сессии |
| `/history` | все запросы |
| `/sessions` | сохранённые сессии |
| `/resume` | вернуться в сессию |
| `/theme` | выбор цветовой темы |
| `/copy` | последний ответ в буфер |
| `/copycode` | последний блок кода в буфер |
| `/diff` | `git diff --stat` |
| `/doctor` | диагностика |
| `/export [файл]` | экспорт чата в Markdown |
| `!cmd` | shell-команда |
| `esc` | отменить генерацию |
| `/exit`, `/quit` | выход |

### Флаги

```
novaproxy --model gpt-6-astra     # модель на этот запуск
novaproxy --mode chat             # code/chat/reasoning
novaproxy --system "..."          # свой системный промпт
novaproxy --reasoning high        # reasoning_effort для gpt-6-astra
novaproxy --key px_live_...       # сохранить ключ и выйти
novaproxy --auth                  # сменить ключ
novaproxy --quiet                 # one-shot без мета-строки
```

---

## Troubleshooting

**`novaproxy: command not found`** — перезапустите терминал или
`export PATH="$HOME/.local/bin:$PATH"` (на Windows закройте и откройте
PowerShell).

**Python не найден (Windows)** — установщик использует winget; иначе
поставьте Python 3.8+ с python.org («Add Python to PATH»).

**Ключ отклоняется** — должен начинаться с `px_live_`. Проверка: `/doctor`.

**Крякозябры в Windows** — `chcp 65001` или Windows Terminal с
Unicode-шрифтом (Cascadia Mono).

**Буфер обмена** — Termux: `pkg install termux-api` + приложение Termux:API;
Linux: `xclip` или `wl-clipboard`; Windows/macOS — из коробки.

## Безопасность

Ключ хранится локально с правами 0600 и отправляется только на сервер
NovaProxy (`novaproxy.duckdns.org`). Телеметрии и сторонних запросов нет,
код полностью открыт.

## License

MIT
