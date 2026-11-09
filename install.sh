#!/bin/sh
# NovaProxy CLI — one-line installer for Linux / macOS / Termux.
#   curl -sSL https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.sh | sh
set -e

REPO="https://github.com/lisnoiv/novaproxy-cli.git"
DEST="$HOME/.novaproxy/app"
BIN="$HOME/.local/bin"

say() { printf '\033[38;5;141m[novaproxy]\033[0m %s\n' "$1"; }
err() { printf '\033[38;5;203m[novaproxy] %s\033[0m\n' "$1" >&2; }

# --- python ---------------------------------------------------------------
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
            PY="$cand"; break
        fi
    fi
done

if [ -z "$PY" ]; then
    say "Python не найден — пытаюсь установить..."
    if [ -n "$PREFIX" ] && echo "$PREFIX" | grep -q "com.termux"; then
        pkg install -y python
        PY=python3
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip
        PY=python3
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-pip
        PY=python3
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm python python-pip
        PY=python3
    elif command -v brew >/dev/null 2>&1; then
        brew install python@3
        PY=python3
    else
        err "Установите Python 3.8+ вручную и перезапустите установку."
        exit 1
    fi
fi
say "Python: $($PY --version 2>&1)"

# --- git ------------------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
    say "git не найден — пытаюсь установить..."
    if [ -n "$PREFIX" ] && echo "$PREFIX" | grep -q "com.termux"; then
        pkg install -y git
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y git
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y git
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm git
    elif command -v brew >/dev/null 2>&1; then
        brew install git
    else
        err "Установите git вручную и перезапустите установку."
        exit 1
    fi
fi

# --- sources ----------------------------------------------------------------
mkdir -p "$HOME/.novaproxy"
if [ -d "$DEST/.git" ]; then
    say "Обновляю существующую установку..."
    git -C "$DEST" pull --ff-only -q || { rm -rf "$DEST"; git clone -q "$REPO" "$DEST"; }
else
    rm -rf "$DEST"
    say "Скачиваю NovaProxy CLI..."
    git clone -q "$REPO" "$DEST"
fi

# --- python deps ------------------------------------------------------------
say "Устанавливаю зависимости..."
"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install --quiet --user -e "$DEST" || "$PY" -m pip install --quiet --user requests rich

# --- launcher ---------------------------------------------------------------
mkdir -p "$BIN"
cat > "$BIN/novaproxy" <<EOF
#!/bin/sh
exec $PY -m novaproxy "\$@"
EOF
chmod +x "$BIN/novaproxy"

case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
        say "Добавляю $BIN в PATH..."
        for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
            if [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ]; then
                grep -q 'novaproxy' "$rc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH" # novaproxy' >> "$rc"
            fi
        done
        export PATH="$BIN:$PATH"
        ;;
esac

say "Готово! Запустите:  novaproxy"
say "При первом запуске введите ключ формата px_live_..."
exec "$BIN/novaproxy" --version >/dev/null 2>&1 && exit 0 || exit 0
