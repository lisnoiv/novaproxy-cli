// Package tools: built-in local tools (function calling), @file expansion,
// git helpers.
package tools

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	MaxFileBytes   = 200_000
	MaxToolOutput  = 12_000
	DefaultTimeout = 60
)

func truncate(s string, limit int) string {
	if len(s) <= limit {
		return s
	}
	return s[:limit] + fmt.Sprintf("\n... [truncated, %d chars omitted]", len(s)-limit)
}

func ReadFile(path string) string {
	p := expandHome(path)
	info, err := os.Stat(p)
	if err != nil {
		return "error: no such file: " + path
	}
	if info.IsDir() {
		entries, _ := os.ReadDir(p)
		names := []string{}
		for _, e := range entries {
			n := e.Name()
			if e.IsDir() {
				n += "/"
			}
			names = append(names, n)
		}
		sort.Strings(names)
		return truncate(strings.Join(names, "\n"), MaxToolOutput)
	}
	f, err := os.Open(p)
	if err != nil {
		return "error: " + err.Error()
	}
	defer f.Close()
	data, _ := io.ReadAll(io.LimitReader(f, MaxFileBytes))
	return string(data)
}

func WriteFile(path, content string) string {
	p := expandHome(path)
	if dir := filepath.Dir(p); dir != "" {
		os.MkdirAll(dir, 0o755)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		return "error: " + err.Error()
	}
	return fmt.Sprintf("ok: wrote %d bytes to %s", len(content), path)
}

func EditFile(path, old, new string) string {
	p := expandHome(path)
	data, err := os.ReadFile(p)
	if err != nil {
		return "error: no such file: " + path
	}
	text := string(data)
	if !strings.Contains(text, old) {
		return "error: old_string not found in file"
	}
	text = strings.Replace(text, old, new, 1)
	if err := os.WriteFile(p, []byte(text), 0o644); err != nil {
		return "error: " + err.Error()
	}
	return "ok: edited " + path
}

func Bash(command string, timeoutSec int) string {
	if timeoutSec <= 0 {
		timeoutSec = DefaultTimeout
	}
	shell, flag := shellCmd()
	cmd := exec.Command(shell, flag, command)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	done := make(chan error, 1)
	go func() { done <- cmd.Run() }()
	select {
	case <-time.After(time.Duration(timeoutSec) * time.Second):
		cmd.Process.Kill()
		return fmt.Sprintf("error: command timed out after %ds", timeoutSec)
	case <-done:
	}
	return truncate(strings.TrimSpace(out.String()), MaxToolOutput)
}

func shellCmd() (string, string) {
	if _, err := os.Stat("C:\\Windows"); err == nil {
		return "cmd", "/C"
	}
	if sh, err := exec.LookPath("sh"); err == nil {
		return sh, "-c"
	}
	return "sh", "-c"
}

func Search(pattern, path string) string {
	if path == "" {
		path = "."
	}
	shell, flag := shellCmd()
	r := exec.Command(shell, flag, fmt.Sprintf("grep -rn -- %s %s 2>/dev/null", shellQuote(pattern), shellQuote(path)))
	var out bytes.Buffer
	r.Stdout = &out
	r.Run()
	res := strings.TrimSpace(out.String())
	if res == "" {
		return "(no matches)"
	}
	return truncate(res, MaxToolOutput)
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

func WebSearch(query string) string {
	client := &http.Client{Timeout: 15 * time.Second}
	req, _ := http.NewRequest("GET", "https://duckduckgo.com/html/?q="+urlQueryEscape(query), nil)
	req.Header.Set("User-Agent", "Mozilla/5.0")
	resp, err := client.Do(req)
	if err != nil {
		return "error: " + err.Error()
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 200_000))
	re := regexp.MustCompile(`class="result__a"[^>]*>(.*?)</a>`)
	tagRe := regexp.MustCompile(`<[^>]+>`)
	matches := re.FindAllStringSubmatch(string(body), 5)
	if len(matches) == 0 {
		return "(no results)"
	}
	lines := []string{}
	for i, m := range matches {
		lines = append(lines, fmt.Sprintf("%d. %s", i+1, tagRe.ReplaceAllString(m[1], "")))
	}
	return strings.Join(lines, "\n")
}

func urlQueryEscape(s string) string {
	r := strings.NewReplacer(" ", "+", "&", "%26", "=", "%3D", "#", "%23", "?", "%3F", "+", "%2B")
	return r.Replace(s)
}

func expandHome(path string) string {
	if strings.HasPrefix(path, "~/") {
		home, _ := os.UserHomeDir()
		return filepath.Join(home, path[2:])
	}
	return path
}

// OpenAI tool schemas -------------------------------------------------------

func Schemas() []map[string]interface{} {
	str := func(d string) map[string]interface{} { return map[string]interface{}{"type": "string", "description": d} }
	return []map[string]interface{}{
		tool("read_file", "Read a file's contents (or list a directory).", map[string]interface{}{"path": str("file or directory path")}, []string{"path"}),
		tool("write_file", "Write text content to a file (creates parents).", map[string]interface{}{"path": str("target path"), "content": str("content")}, []string{"path", "content"}),
		tool("edit_file", "Replace the first occurrence of old with new in a file.", map[string]interface{}{"path": str("path"), "old": str("old string"), "new": str("new string")}, []string{"path", "old", "new"}),
		tool("bash", "Run a shell command and return its output.", map[string]interface{}{"command": str("shell command"), "timeout": map[string]interface{}{"type": "integer"}}, []string{"command"}),
		tool("search", "Search files recursively for a substring.", map[string]interface{}{"pattern": str("substring"), "path": str("root path")}, []string{"pattern"}),
		tool("web_search", "Search the web (DuckDuckGo).", map[string]interface{}{"query": str("query")}, []string{"query"}),
	}
}

func tool(name, desc string, params map[string]interface{}, req []string) map[string]interface{} {
	return map[string]interface{}{
		"type": "function",
		"function": map[string]interface{}{
			"name":        name,
			"description": desc,
			"parameters":  map[string]interface{}{"type": "object", "properties": params, "required": req},
		},
	}
}

// Dispatch runs a built-in tool by name. Returns (output, riskClass).
func Dispatch(name, argsJSON string) (string, string) {
	var args map[string]interface{}
	json.Unmarshal([]byte(argsJSON), &args)
	str := func(k string) string {
		if v, ok := args[k].(string); ok {
			return v
		}
		return ""
	}
	switch name {
	case "read_file":
		return ReadFile(str("path")), "read"
	case "write_file":
		return WriteFile(str("path"), str("content")), "write"
	case "edit_file":
		return EditFile(str("path"), str("old"), str("new")), "write"
	case "bash":
		to := DefaultTimeout
		if v, ok := args["timeout"].(float64); ok {
			to = int(v)
		}
		return Bash(str("command"), to), "bash"
	case "search":
		return Search(str("pattern"), str("path")), "read"
	case "web_search":
		return WebSearch(str("query")), "read"
	}
	return "error: unknown tool " + name, "read"
}

// ExpandAtRefs replaces @path tokens with file contents.
func ExpandAtRefs(text string) (string, [][2]string) {
	toks := strings.Fields(text)
	var out []string
	var attached [][2]string
	for _, tok := range toks {
		if strings.HasPrefix(tok, "@") && len(tok) > 1 {
			path := tok[1:]
			p := expandHome(path)
			info, err := os.Stat(p)
			if err == nil {
				var body string
				var lines int
				if info.IsDir() {
					entries, _ := os.ReadDir(p)
					names := []string{}
					for _, e := range entries {
						n := e.Name()
						if e.IsDir() {
							n += "/"
						}
						names = append(names, n)
					}
					sort.Strings(names)
					body = strings.Join(names, "\n")
					lines = len(names)
				} else {
					f, err := os.Open(p)
					if err == nil {
						data, _ := io.ReadAll(io.LimitReader(f, MaxFileBytes))
						f.Close()
						body = string(data)
						lines = strings.Count(body, "\n") + 1
					}
				}
				if body != "" {
					attached = append(attached, [2]string{path, fmt.Sprintf("%d", lines)})
					out = append(out, "\n--- "+path+" ---\n"+body+"\n--- end "+path+" ---\n")
					continue
				}
			}
		}
		out = append(out, tok)
	}
	return strings.Join(out, " "), attached
}

func GitDiffStat() string {
	shell, flag := shellCmd()
	r := exec.Command(shell, flag, "git diff --stat")
	var out bytes.Buffer
	r.Stdout = &out
	r.Stderr = &out
	r.Run()
	res := strings.TrimSpace(out.String())
	if res == "" {
		return "нет изменений"
	}
	return res
}
