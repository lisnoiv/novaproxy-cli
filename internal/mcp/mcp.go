// Package mcp: Model Context Protocol client over stdio (newline JSON-RPC)
// and HTTP POST.
package mcp

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/lisnoiv/novaproxy-cli/internal/config"
)

const protocolVersion = "2024-11-05"

type Tool struct {
	Name        string                 `json:"name"`
	Description string                 `json:"description"`
	InputSchema map[string]interface{} `json:"inputSchema"`
}

type Server struct {
	Name    string
	Spec    config.MCPServer
	Status  string // off | connecting | ok | error
	Err     string
	Tools   []Tool
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	mu      sync.Mutex
	id      int
	pending map[int]chan rpcResponse
	http    *http.Client
}

type rpcResponse struct {
	Result json.RawMessage
	Err    *rpcError
}
type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type rpcEnvelope struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      *int            `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  interface{}     `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type Manager struct {
	Servers map[string]*Server
	Specs   map[string]config.MCPServer
}

func NewManager(specs map[string]config.MCPServer) *Manager {
	return &Manager{Servers: map[string]*Server{}, Specs: specs}
}

func (m *Manager) ConnectAll(onEvent func(name, status string)) {
	for name, spec := range m.Specs {
		srv := &Server{Name: name, Spec: spec, Status: "connecting", pending: map[int]chan rpcResponse{}, http: &http.Client{Timeout: 60 * time.Second}}
		if err := srv.connect(); err != nil {
			srv.Status = "error"
			srv.Err = err.Error()
			m.Servers[name] = srv
			if onEvent != nil {
				onEvent(name, "error: "+err.Error())
			}
			continue
		}
		srv.Status = "ok"
		m.Servers[name] = srv
		if onEvent != nil {
			onEvent(name, fmt.Sprintf("ok · %d tools", len(srv.Tools)))
		}
	}
}

func (m *Manager) Add(name string, spec config.MCPServer) (bool, string) {
	if old, ok := m.Servers[name]; ok {
		old.Close()
	}
	m.Specs[name] = spec
	srv := &Server{Name: name, Spec: spec, Status: "connecting", pending: map[int]chan rpcResponse{}, http: &http.Client{Timeout: 60 * time.Second}}
	if err := srv.connect(); err != nil {
		srv.Status = "error"
		srv.Err = err.Error()
		m.Servers[name] = srv
		return false, err.Error()
	}
	srv.Status = "ok"
	m.Servers[name] = srv
	return true, fmt.Sprintf("подключено, инструментов: %d", len(srv.Tools))
}

func (m *Manager) Remove(name string) bool {
	srv, ok := m.Servers[name]
	delete(m.Specs, name)
	if ok {
		srv.Close()
		delete(m.Servers, name)
		return true
	}
	return false
}

func (m *Manager) CloseAll() {
	for _, s := range m.Servers {
		s.Close()
	}
}

func (m *Manager) StatusLines() [][2]string {
	out := [][2]string{}
	for name, s := range m.Servers {
		switch s.Status {
		case "ok":
			out = append(out, [2]string{name, fmt.Sprintf("ok · %d tools", len(s.Tools))})
		case "error":
			out = append(out, [2]string{name, "error · " + trunc(s.Err, 40)})
		default:
			out = append(out, [2]string{name, s.Status})
		}
	}
	return out
}

func trunc(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// OpenAITools converts all connected MCP tools to OpenAI function schemas.
func (m *Manager) OpenAITools() []map[string]interface{} {
	out := []map[string]interface{}{}
	for name, s := range m.Servers {
		if s.Status != "ok" {
			continue
		}
		for _, t := range s.Tools {
			desc := t.Description
			if len(desc) > 500 {
				desc = desc[:500]
			}
			params := t.InputSchema
			if params == nil {
				params = map[string]interface{}{"type": "object", "properties": map[string]interface{}{}}
			}
			out = append(out, map[string]interface{}{
				"type": "function",
				"function": map[string]interface{}{
					"name":        "mcp__" + name + "__" + t.Name,
					"description": desc,
					"parameters":  params,
				},
			})
		}
	}
	return out
}

func (m *Manager) Dispatch(qualified, argsJSON string) string {
	parts := strings.SplitN(qualified, "__", 3)
	if len(parts) != 3 {
		return "error: bad tool name " + qualified
	}
	srv, ok := m.Servers[parts[1]]
	if !ok || srv.Status != "ok" {
		return "error: MCP server '" + parts[1] + "' not connected"
	}
	var args map[string]interface{}
	if argsJSON != "" {
		json.Unmarshal([]byte(argsJSON), &args)
	}
	res, err := srv.callTool(parts[2], args)
	if err != nil {
		return "error: " + err.Error()
	}
	return res
}

// ---- Server internals -----------------------------------------------------

func (s *Server) connect() error {
	timeout := s.Spec.Timeout
	if timeout <= 0 {
		timeout = 15
	}
	if s.Spec.URL != "" {
		// HTTP transport: nothing to open; verify with initialize.
	} else if s.Spec.Command != "" {
		cmd := exec.Command(s.Spec.Command, s.Spec.Args...)
		env := os.Environ()
		for k, v := range s.Spec.Env {
			env = append(env, k+"="+v)
		}
		cmd.Env = env
		stdin, err := cmd.StdinPipe()
		if err != nil {
			return err
		}
		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return err
		}
		cmd.Stderr = nil
		if err := cmd.Start(); err != nil {
			return fmt.Errorf("start: %w", err)
		}
		s.cmd = cmd
		s.stdin = stdin
		go s.readLoop(stdout)
	} else {
		return fmt.Errorf("server needs 'command' or 'url'")
	}

	_, err := s.request("initialize", map[string]interface{}{
		"protocolVersion": protocolVersion,
		"capabilities":    map[string]interface{}{},
		"clientInfo":      map[string]interface{}{"name": "novaproxy-cli", "version": "1.0.0"},
	}, timeout)
	if err != nil {
		return err
	}
	s.notify("notifications/initialized", nil)
	res, err := s.request("tools/list", map[string]interface{}{}, 15)
	if err != nil {
		return err
	}
	var list struct {
		Tools []Tool `json:"tools"`
	}
	json.Unmarshal(res, &list)
	s.Tools = list.Tools
	return nil
}

func (s *Server) readLoop(r io.Reader) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var env rpcEnvelope
		if json.Unmarshal([]byte(line), &env) != nil || env.ID == nil {
			continue
		}
		s.mu.Lock()
		ch := s.pending[*env.ID]
		delete(s.pending, *env.ID)
		s.mu.Unlock()
		if ch != nil {
			ch <- rpcResponse{Result: env.Result, Err: env.Error}
		}
	}
}

func (s *Server) request(method string, params interface{}, timeoutSec float64) (json.RawMessage, error) {
	if s.Spec.URL != "" {
		return s.httpRequest(method, params, timeoutSec)
	}
	s.mu.Lock()
	s.id++
	id := s.id
	ch := make(chan rpcResponse, 1)
	s.pending[id] = ch
	s.mu.Unlock()

	env := rpcEnvelope{JSONRPC: "2.0", ID: &id, Method: method, Params: params}
	data, _ := json.Marshal(env)
	if _, err := s.stdin.Write(append(data, '\n')); err != nil {
		return nil, err
	}
	select {
	case resp := <-ch:
		if resp.Err != nil {
			return nil, fmt.Errorf("mcp error: %s", resp.Err.Message)
		}
		return resp.Result, nil
	case <-time.After(time.Duration(timeoutSec * float64(time.Second))):
		return nil, fmt.Errorf("mcp timeout on %s", method)
	}
}

func (s *Server) httpRequest(method string, params interface{}, timeoutSec float64) (json.RawMessage, error) {
	s.mu.Lock()
	s.id++
	id := s.id
	s.mu.Unlock()
	env := rpcEnvelope{JSONRPC: "2.0", ID: &id, Method: method, Params: params}
	data, _ := json.Marshal(env)
	headers := map[string]string{"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
	for k, v := range s.Spec.Headers {
		headers[k] = v
	}
	req, _ := http.NewRequest("POST", s.Spec.URL, bytes.NewReader(data))
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: time.Duration(timeoutSec * float64(time.Second))}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	text := string(body)
	if strings.Contains(text, "data:") && !strings.HasPrefix(strings.TrimSpace(text), "{") {
		for _, line := range strings.Split(text, "\n") {
			if strings.HasPrefix(line, "data:") {
				text = strings.TrimSpace(line[5:])
				break
			}
		}
	}
	var out rpcEnvelope
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		return nil, fmt.Errorf("mcp: invalid JSON reply")
	}
	if out.Error != nil {
		return nil, fmt.Errorf("mcp error: %s", out.Error.Message)
	}
	return out.Result, nil
}

func (s *Server) notify(method string, params interface{}) {
	env := rpcEnvelope{JSONRPC: "2.0", Method: method, Params: params}
	data, _ := json.Marshal(env)
	if s.Spec.URL != "" {
		req, _ := http.NewRequest("POST", s.Spec.URL, bytes.NewReader(data))
		req.Header.Set("Content-Type", "application/json")
		client := &http.Client{Timeout: 10 * time.Second}
		resp, err := client.Do(req)
		if err == nil {
			resp.Body.Close()
		}
		return
	}
	if s.stdin != nil {
		s.stdin.Write(append(data, '\n'))
	}
}

func (s *Server) callTool(name string, args map[string]interface{}) (string, error) {
	res, err := s.request("tools/call", map[string]interface{}{"name": name, "arguments": args}, 60)
	if err != nil {
		return "", err
	}
	var out struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
	}
	if json.Unmarshal(res, &out) != nil {
		return string(res), nil
	}
	parts := []string{}
	for _, c := range out.Content {
		if c.Type == "text" {
			parts = append(parts, c.Text)
		}
	}
	if len(parts) == 0 {
		return "(empty result)", nil
	}
	return strings.Join(parts, "\n"), nil
}

func (s *Server) Close() {
	if s.cmd != nil && s.cmd.Process != nil {
		s.cmd.Process.Kill()
		s.cmd.Wait()
	}
	s.Status = "off"
}
