# NovaProxy CLI

Кроссплатформенный терминальный AI-клиент для **NovaProxy** с интерфейсом в стиле OpenCode: потоковые ответы, вызов инструментов (файлы, shell, поиск), MCP-серверы, сессии, темы и слэш-команды с автоподсказкой.

Написан на **Go** (bubbletea + lipgloss + glamour), собирается в один бинарник без внешних зависимостей. Работает на **Android (Termux)**, **Windows**, **Linux** и **macOS**.

---

## 1. Установка в 1 команду

### Android (Termux)

```sh
curl -fsSL https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.sh | sh
```

### Linux / macOS

```sh
curl -fsSL https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.ps1 | iex
```

Установщик сам поставит `git` и Go (через `pkg`/`apt`/`dnf`/`pacman`/`brew`/`winget`), склонирует репозиторий, соберёт бинарник из **вендоренных** зависимостей (ничего не скачивается из сети при сборке) и положит его в `~/.local/bin` (Linux/macOS/Termux) или `%USERPROFILE%\bin` (Windows).

> Если после установки пишет `command not found` — перезапусти терминал (PATH обновился).

---

## 2. Первый запуск и активация

```sh
novaproxy
```

При первом запуске CLI попросит ключ:

```
Введите ваш API-ключ (формат px_live_...):
```

Вставь ключ вида `px_live_...` и нажми Enter. CLI проверит его на сервере NovaProxy и сохранит в `~/.novaproxy/config.json` (права `0600`). При следующих запусках ключ подхватывается автоматически.

Сменить ключ:

```sh
novaproxy --auth
# или прямо в чате:
/key px_live_новый_ключ
```

---

## 3. Использование

### Интерактивный режим (HUD)

Просто запусти `novaproxy` без аргументов. Печатай сообщения, Enter — отправить.

- `@file` / `@dir/` — прикрепить файл или каталог к запросу (`покажи баги в @main.go`).
- `!команда` — выполнить shell-команду и показать вывод (`!ls -la`).
- `/` в начале строки — всплывающие подсказки команд; `Tab` — автодополнение, `↑`/`↓` — выбор.
- `Esc` / `Ctrl+C` — прервать генерацию.
- `Alt+Enter` или `\` в конце строки — многострочный ввод.

### Слэш-команды

| Команда | Описание |
|---|---|
| `/help` | список всех команд |
| `/models` | список моделей прокси |
| `/model` | сменить модель на лету |
| `/mode` | режим `code` / `chat` / `reasoning` |
| `/system` | просмотр/смена системного промпта |
| `/new`, `/clear` | новая сессия / сброс контекста |
| `/history` | все запросы сессии |
| `/sessions`, `/resume` | сохранённые сессии, возврат |
| `/init` | создать/обновить `NOVA.md` (память проекта) |
| `/compact` | сжать историю в сводку |
| `/mcp` | статус MCP-серверов |
| `/tools`, `/permissions` | инструменты и разрешения |
| `/key`, `/auth` | сменить API-ключ |
| `/balance`, `/cost`, `/status` | баланс, токены сессии, инфо |
| `/theme` | сменить цветовую тему |
| `/copy`, `/copycode` | ответ / блок кода в буфер обмена |
| `/diff` | `git diff --stat` текущего репозитория |
| `/export` | экспорт чата в markdown |
| `/doctor` | диагностика окружения |
| `/exit` | выход |

### One-shot режим (один запрос без REPL)

```sh
novaproxy "напиши hello world на python"
cat error.log | novaproxy "объясни ошибку"
novaproxy --model kimi-k3 "сравни два подхода"
```

Флаги: `--model`, `--mode code|chat|reasoning`, `--system "..."`, `--reasoning low|medium|high|xhigh|max`, `--quiet`.

### Свои команды (скиллы)

Положи markdown-файл в `~/.novaproxy/commands/`:

```sh
mkdir -p ~/.novaproxy/commands
echo 'Сделай код-ревизию и укажи на баги.' > ~/.novaproxy/commands/review.md
```

Теперь в чате работает `/review @src/main.go`.

### MCP-серверы

Подключение внешних инструментов через Model Context Protocol. В `~/.novaproxy/config.json`:

```json
"mcp_servers": {
  "filesystem": { "type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/sdcard"] },
  "myapi":      { "type": "http",  "url": "http://127.0.0.1:9000/mcp" }
}
```

Инструменты MCP появляются как `mcp__<сервер>__<инструмент>` и доступны модели при function calling. Статус — команда `/mcp`.

### Разрешения на инструменты

По умолчанию: чтение файлов — авто, запись и shell — спрашивает. Настройка в конфиге:

```json
"permissions": { "read": "allow", "write": "ask", "bash": "ask" }
```

или командой `/permissions` в чате. `allow` / `ask` / `deny`.

---

## 4. Решение частых проблем

**`command not found: novaproxy`** — перезапусти терминал или выполни `export PATH="$HOME/.local/bin:$PATH"` (Windows: закрой и открой PowerShell заново).

**`go: command not found` при установке** — установщик не смог поставить Go автоматически. Поставь вручную: Termux `pkg install golang`, Windows — https://go.dev/dl/, затем запусти установщик ещё раз.

**Ключ не принимается** — проверь, что ключ начинается с `px_live_` и не истёк. Проверка вручную: `novaproxy --key px_live_...`.

**Кривые символы/рамки в Windows** — используй Windows Terminal (не старый `conhost`), шрифт с поддержкой Unicode (Cascadia Mono, JetBrains Mono).

**Русский текст отображается `???` в Windows PowerShell** — выполни `[Console]::OutputEncoding = [Text.Encoding]::UTF8` или добавь `$OutputEncoding = [Text.Encoding]::UTF8` в профиль PowerShell.

**Ошибка сети / таймаут** — проверь, что `https://novaproxy.duckdns.org` доступен из твоей сети (некоторые сети блокируют duckdns).

**Где лежит конфиг?** — `~/.novaproxy/config.json` (Windows: `%USERPROFILE%\.novaproxy\config.json`). Удаление файла = полный сброс.

---

## 5. Сборка из исходников вручную

```sh
git clone https://github.com/lisnoiv/novaproxy-cli.git
cd novaproxy-cli
go build -mod=vendor -o novaproxy .
./novaproxy
```

Все зависимости лежат в `vendor/` — сборка полностью офлайн, нужен только Go 1.21+.

---

## Лицензия

MIT
