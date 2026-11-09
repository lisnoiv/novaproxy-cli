# NovaProxy CLI

Официальный кроссплатформенный терминальный клиент для API-шлюза **NovaProxy**.
Интерактивный HUD-интерфейс, потоковый вывод, вызов инструментов (function calling),
поддержка MCP-серверов, работа с файлами через `@`, сессии и многое другое.

*The official cross-platform terminal client for the **NovaProxy** API gateway —
interactive HUD, streaming, tool calling, MCP servers, `@file` context, sessions.*

---

## Быстрый старт (одна команда)

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

Установщик сам найдёт или поставит Python 3.8+ и git, скачает клиент и добавит
команду `novaproxy` в PATH.

---

## Первый запуск и активация

1. Запустите:

   ```sh
   novaproxy
   ```

2. При первом запуске появится приглашение:

   ```
   Введите ваш API-ключ (формат px_live_...):
   ```

3. Вставьте ваш ключ `px_live_...` и нажмите Enter. Клиент проверит ключ на
   сервере и сохранит его в `~/.novaproxy/config.json` (доступ только владельцу).
   При следующих запусках вводить ключ не нужно.

Сменить ключ позже: команда `/key` в чате или `novaproxy --auth` /
`novaproxy --key px_live_...`.

---

## Использование

### Интерактивный HUD-режим

```sh
novaproxy
```

Просто пишите запросы. Enter на пустой строке или `/` открывает меню команд.

### Одиночный запрос (one-shot)

```sh
novaproxy "объясни, что делает этот код" < main.py
cat error.log | novaproxy "найди причину ошибки"
novaproxy -m gpt-6-astra "напиши парсер CSV"
```

Ответ стримится в stdout, служебная строка (модель, TTFT, tok/s, токены) — в stderr.

### Файлы в контексте через @

```
❯ найди баг в @src/main.py и исправь
❯ что в этой папке? @src/
```

Содержимое файла (или листинг директории) автоматически прикрепляется к запросу.

### Вызов инструментов

Если модель поддерживает function calling, клиент даёт ей локальные инструменты:
`read_file`, `write_file`, `edit_file`, `bash`, `search`, `web_search` —
плюс все инструменты подключённых MCP-серверов. Отключить: `/tools`.

### MCP-серверы

Подключение прямо из чата:

```
/mcp add fs npx -y @modelcontextprotocol/server-filesystem /sdcard
/mcp          # статус и список
/mcp tools    # инструменты серверов
/mcp del fs   # удалить
```

Или через конфиг `~/.novaproxy/config.json`:

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
| `/help` | справка по всем командам |
| `/new` | новая сессия (текущая сохраняется) |
| `/clear`, `/reset` | сброс контекста диалога |
| `/compact` | сжать контекст в сводку |
| `/model` | меню выбора модели / `/model <id>` |
| `/models` | список моделей прокси |
| `/mode` | режим системного промпта: code / chat / reasoning |
| `/system` | просмотр или замена системного промпта (`/system reset` — сброс) |
| `/mcp` | управление MCP-серверами |
| `/tools` | вкл/выкл вызов инструментов |
| `/key` | сменить API-ключ |
| `/balance` | баланс и данные ключа |
| `/cost` | токены сессии, TTFT, скорость |
| `/status` | информация о сессии |
| `/history` | все запросы сессии |
| `/sessions` | сохранённые сессии |
| `/resume` | вернуться в сессию |
| `/sidebar` | боковая панель вкл/выкл |
| `/theme` | сменить цветовую схему |
| `/copy` | последний ответ в буфер обмена |
| `/copycode` | последний блок кода в буфер |
| `/diff` | `git diff --stat` |
| `/init` | создать NOVA.md |
| `/doctor` | диагностика окружения и ключа |
| `/export [файл]` | экспорт чата в Markdown |
| `!cmd` | выполнить shell-команду |
| `/exit`, `/quit` | выход |

### Флаги командной строки

```
novaproxy --model gpt-6-astra     # модель на этот запуск
novaproxy --mode chat             # системный промпт: code/chat/reasoning
novaproxy --system "..."          # свой системный промпт
novaproxy --reasoning high        # reasoning_effort для gpt-6-astra
novaproxy --key px_live_...       # сохранить ключ и выйти
novaproxy --auth                  # сменить ключ интерактивно
novaproxy --no-boot               # без заставки
novaproxy --quiet                 # one-shot без мета-строки
```

---

## Troubleshooting

**`novaproxy: command not found` после установки**
Перезапустите терминал или выполните `export PATH="$HOME/.local/bin:$PATH"`
(на Windows — закройте и откройте PowerShell заново).

**Python не найден (Windows)**
Установщик использует winget. Если его нет — поставьте Python 3.8+ с
python.org, отметив галочку «Add Python to PATH», и повторите установку.

**Ключ отклоняется**
Проверьте формат: ключ должен начинаться с `px_live_`. Проверить ключ можно
командой `/doctor` внутри клиента.

**Крякозябры в Windows Terminal / CMD**
Выполните `chcp 65001` перед запуском или используйте Windows Terminal с
шрифтом, поддерживающим Unicode (например, Cascadia Mono).

**Ключ не сохраняется / ошибка прав**
Убедитесь, что домашняя директория доступна для записи. Конфиг лежит в
`~/.novaproxy/config.json` (`%USERPROFILE%\.novaproxy\config.json` на Windows).

**Буфер обмена не работает**
Termux: `pkg install termux-api` + приложение Termux:API.
Linux: установите `xclip` или `wl-clipboard`. Windows/macOS работают из коробки.

---

## Безопасность

Клиент хранит ключ локально с правами `0600`, никуда его не отправляет, кроме
сервера NovaProxy (`novaproxy.duckdns.org`), и не содержит сторонних
трекеров и телеметрии. Весь код открыт — проверяйте.

## License

MIT
