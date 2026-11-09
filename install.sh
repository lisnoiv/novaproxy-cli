#!/bin/sh
# NovaProxy CLI — one-line installer (Termux / Linux / macOS)
set -e

REPO="https://github.com/lisnoiv/novaproxy-cli.git"
DIR="$HOME/novaproxy-cli"
BIN_NAME="novaproxy"

say() { printf '%s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

install_go() {
    if have go; then return 0; fi
    say "==> Go toolchain not found, installing..."
    if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
        pkg install -y golang
    elif have apt-get; then
        sudo apt-get update -y && sudo apt-get install -y golang-go || sudo apt-get install -y golang
    elif have dnf; then
        sudo dnf install -y golang
    elif have pacman; then
        sudo pacman -Sy --noconfirm go
    elif have brew; then
        brew install go
    elif have apk; then
        sudo apk add --no-cache go
    else
        say "ERROR: could not auto-install Go. Install Go 1.21+ from https://go.dev/dl/ and re-run."
        exit 1
    fi
}

install_git() {
    if have git; then return 0; fi
    say "==> git not found, installing..."
    if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
        pkg install -y git
    elif have apt-get; then
        sudo apt-get install -y git
    elif have dnf; then
        sudo dnf install -y git
    elif have pacman; then
        sudo pacman -Sy --noconfirm git
    elif have brew; then
        brew install git
    elif have apk; then
        sudo apk add --no-cache git
    else
        say "ERROR: could not auto-install git."
        exit 1
    fi
}

say "==> NovaProxy CLI installer"
install_git
install_go

if [ -d "$DIR/.git" ]; then
    say "==> Updating existing checkout in $DIR"
    git -C "$DIR" pull --ff-only || git -C "$DIR" pull --rebase
else
    say "==> Cloning $REPO"
    git clone --depth 1 "$REPO" "$DIR"
fi

cd "$DIR"
say "==> Building (vendored deps, no downloads)..."
GOFLAGS=-mod=vendor go build -trimpath -ldflags "-s -w" -o "$BIN_NAME" .

BINDIR="$HOME/.local/bin"
mkdir -p "$BINDIR"
cp "$BIN_NAME" "$BINDIR/$BIN_NAME"
chmod +x "$BINDIR/$BIN_NAME"

# ensure ~/.local/bin on PATH
case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *)
        PROFILE=""
        if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
            PROFILE="$HOME/.bashrc"
        elif [ -f "$HOME/.zshrc" ]; then
            PROFILE="$HOME/.zshrc"
        elif [ -f "$HOME/.bashrc" ]; then
            PROFILE="$HOME/.bashrc"
        elif [ -f "$HOME/.profile" ]; then
            PROFILE="$HOME/.profile"
        fi
        if [ -n "$PROFILE" ]; then
            echo "export PATH=\"$BINDIR:\$PATH\"" >> "$PROFILE"
            say "==> Added $BINDIR to PATH in $PROFILE (restart shell or: export PATH=\"$BINDIR:\$PATH\")"
        fi
        export PATH="$BINDIR:$PATH"
        ;;
esac

say ""
say "==> Installed: $BINDIR/$BIN_NAME"
"$BINDIR/$BIN_NAME" --version || true
say ""
say "Запусти:  novaproxy"
say "При первом запуске введи свой ключ вида px_live_..."
