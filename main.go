package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/lisnoiv/novaproxy-cli/internal/api"
	"github.com/lisnoiv/novaproxy-cli/internal/config"
	"github.com/lisnoiv/novaproxy-cli/internal/tui"
)

func main() {
	var (
		model     = flag.String("model", "", "модель")
		mode      = flag.String("mode", "", "code|chat|reasoning")
		system    = flag.String("system", "", "свой системный промпт")
		reasoning = flag.String("reasoning", "", "reasoning_effort: low|medium|high|xhigh|max")
		key       = flag.String("key", "", "сохранить API-ключ и выйти")
		auth      = flag.Bool("auth", false, "сменить API-ключ")
		quiet     = flag.Bool("quiet", false, "one-shot без мета-строки")
		version   = flag.Bool("version", false, "версия")
	)
	flag.Parse()

	if *version {
		fmt.Println("novaproxy " + tui.Version)
		return
	}

	cfg := config.Load()

	if *key != "" {
		if !config.KeyFormatOK(*key) {
			fmt.Fprintln(os.Stderr, "Ошибка: ключ должен начинаться с px_live_")
			os.Exit(1)
		}
		ok, errStr := api.New(*key).ValidateKey()
		if !ok {
			fmt.Fprintln(os.Stderr, "Ключ отклонён сервером: "+errStr)
			os.Exit(1)
		}
		cfg.APIKey = *key
		cfg.Save()
		fmt.Println("Ключ сохранён в ~/.novaproxy/config.json")
		return
	}

	// first-run / revalidation flow
	if cfg.APIKey == "" || *auth {
		k, err := promptKey()
		if err != nil {
			os.Exit(1)
		}
		cfg.APIKey = k
		cfg.Save()
		if *auth && flag.NArg() == 0 {
			return
		}
	} else {
		if os.Getenv("NOVAPROXY_SKIP_VALIDATE") == "" {
			ok, errStr := api.New(cfg.APIKey).ValidateKey()
			if !ok {
				fmt.Fprintf(os.Stderr, "Сохранённый ключ отклонён (%s). Введите новый.\n", errStr)
				k, err := promptKey()
				if err != nil {
					os.Exit(1)
				}
				cfg.APIKey = k
				cfg.Save()
			}
		}
	}

	if *system != "" {
		cfg.SystemPrompt = *system
	}
	if *mode != "" {
		cfg.Mode = *mode
	}

	client := api.New(cfg.APIKey)
	args := tui.Args{
		Model: *model, Mode: *mode, System: *system,
		ReasoningEffort: *reasoning, Quiet: *quiet,
	}

	// one-shot: positional prompt or piped stdin
	var prompt string
	if flag.NArg() > 0 {
		prompt = strings.Join(flag.Args(), " ")
	}
	if prompt == "" && !isTTY() {
		data, _ := io.ReadAll(os.Stdin)
		if s := strings.TrimSpace(string(data)); s != "" {
			prompt = s
		}
	}
	if prompt != "" {
		os.Exit(tui.RunOneshot(cfg, client, args, prompt))
	}

	m := tui.New(cfg, client, args)
	p := tea.NewProgram(m, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func isTTY() bool {
	fi, err := os.Stdin.Stat()
	if err != nil {
		return true
	}
	return fi.Mode()&os.ModeCharDevice != 0
}

func promptKey() (string, error) {
	r := bufio.NewReader(os.Stdin)
	for {
		fmt.Print("  Введите ваш API-ключ (формат px_live_...): ")
		line, err := r.ReadString('\n')
		if err != nil {
			return "", err
		}
		k := strings.TrimSpace(line)
		if k == "" {
			continue
		}
		if !config.KeyFormatOK(k) {
			fmt.Println("  Ключ должен начинаться с px_live_ — проверьте и повторите.")
			continue
		}
		fmt.Println("  Проверяю ключ на сервере NovaProxy...")
		ok, errStr := api.New(k).ValidateKey()
		if ok {
			fmt.Println("  Ключ принят и сохранён в ~/.novaproxy/config.json")
			return k, nil
		}
		fmt.Println("  Сервер отклонил ключ: " + errStr)
	}
}
