// Package api implements the NovaProxy gateway client: /v1/models,
// /v1/chat/completions (SSE streaming + tool calls), /api/key-info.
package api

import (
	"bufio"
	"os"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

var (
	BaseURL    = envOr("NOVAPROXY_BASE_URL", "https://novaproxy.duckdns.org/v1")
	KeyInfoURL = envOr("NOVAPROXY_KEYINFO_URL", "https://novaproxy.duckdns.org/api/key-info")
)

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

type Client struct {
	Key  string
	HTTP *http.Client
}

func New(key string) *Client {
	return &Client{Key: key, HTTP: &http.Client{Timeout: 180 * time.Second}}
}

type Error struct {
	Status int
	Msg    string
}

func (e *Error) Error() string { return e.Msg }

func parseErr(resp *http.Response) error {
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	var payload struct {
		Error interface{} `json:"error"`
	}
	if json.Unmarshal(body, &payload) == nil && payload.Error != nil {
		switch v := payload.Error.(type) {
		case string:
			return &Error{resp.StatusCode, v}
		case map[string]interface{}:
			if m, ok := v["message"].(string); ok {
				return &Error{resp.StatusCode, m}
			}
		}
	}
	msg := strings.TrimSpace(string(body))
	if msg == "" {
		msg = fmt.Sprintf("HTTP %d", resp.StatusCode)
	}
	if len(msg) > 300 {
		msg = msg[:300]
	}
	return &Error{resp.StatusCode, msg}
}

func (c *Client) ListModels() ([]string, error) {
	req, _ := http.NewRequest("GET", BaseURL+"/models", nil)
	req.Header.Set("Authorization", "Bearer "+c.Key)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, &Error{0, "сеть недоступна: " + err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, parseErr(resp)
	}
	var out struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, &Error{0, "некорректный ответ /v1/models"}
	}
	models := make([]string, 0, len(out.Data))
	seen := map[string]bool{}
	for _, m := range out.Data {
		if m.ID != "" && !seen[m.ID] {
			models = append(models, m.ID)
			seen[m.ID] = true
		}
	}
	return models, nil
}

func (c *Client) ValidateKey() (bool, string) {
	body, _ := json.Marshal(map[string]string{"api_key": c.Key})
	req, _ := http.NewRequest("POST", KeyInfoURL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return false, "сервер недоступен (" + err.Error() + ")"
	}
	defer resp.Body.Close()
	if resp.StatusCode == 200 {
		return true, ""
	}
	var out struct {
		Error string `json:"error"`
	}
	json.NewDecoder(resp.Body).Decode(&out)
	if out.Error == "" {
		out.Error = fmt.Sprintf("HTTP %d", resp.StatusCode)
	}
	return false, out.Error
}

type KeyInfo struct {
	Name          string   `json:"name"`
	Active        bool     `json:"active"`
	Expired       bool     `json:"expired"`
	Balance       float64  `json:"balance"`
	Currency      string   `json:"currency"`
	RequestCount  int      `json:"request_count"`
	ExpiresAt     string   `json:"expires_at"`
	AllowedModels []string `json:"allowed_models"`
}

func (c *Client) GetKeyInfo() (*KeyInfo, error) {
	body, _ := json.Marshal(map[string]string{"api_key": c.Key})
	req, _ := http.NewRequest("POST", KeyInfoURL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, &Error{0, "сеть недоступна: " + err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, parseErr(resp)
	}
	var info KeyInfo
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return nil, &Error{0, "некорректный ответ /api/key-info"}
	}
	return &info, nil
}

// ---- chat -----------------------------------------------------------------

type Message struct {
	Role       string     `json:"role"`
	Content    *string    `json:"content"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
}

type ToolCall struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

type Tool struct {
	Type     string `json:"type"`
	Function struct {
		Name        string                 `json:"name"`
		Description string                 `json:"description"`
		Parameters  map[string]interface{} `json:"parameters"`
	} `json:"function"`
}

type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// StreamEvent is one decoded SSE frame.
type StreamEvent struct {
	Content      string
	ToolCalls    []ToolCallDelta
	FinishReason string
	Usage        *Usage
	Done         bool
}

type ToolCallDelta struct {
	Index     int
	ID        string
	Name      string
	Arguments string
}

type rawChunk struct {
	Choices []struct {
		Delta struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Index    int    `json:"index"`
				ID       string `json:"id"`
				Type     string `json:"type"`
				Function struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"delta"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Usage *Usage `json:"usage"`
}

// ChatStream posts the request and invokes onEvent for every SSE frame.
// The returned cancel func aborts the HTTP request.
func (c *Client) ChatStream(messages []Message, model string, tools []Tool, reasoningEffort string, onEvent func(StreamEvent)) (cancel func(), err error) {
	body := map[string]interface{}{
		"model":    model,
		"messages": messages,
		"stream":   true,
	}
	if len(tools) > 0 {
		body["tools"] = tools
		body["tool_choice"] = "auto"
	}
	if reasoningEffort != "" {
		body["reasoning_effort"] = reasoningEffort
	}
	data, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", BaseURL+"/chat/completions", bytes.NewReader(data))
	req.Header.Set("Authorization", "Bearer "+c.Key)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, &Error{0, "сеть недоступна: " + err.Error()}
	}
	if resp.StatusCode != 200 {
		defer resp.Body.Close()
		return nil, parseErr(resp)
	}

	// Synchronous read loop: ChatStream returns after the stream ends.
	// The returned cancel func aborts the HTTP request early.
	canceled := false
	defer resp.Body.Close()
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for scanner.Scan() {
		if canceled {
			break
		}
		line := strings.TrimRight(scanner.Text(), "\r")
		if line == "" || strings.HasPrefix(line, ":") || !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(line[5:])
		if payload == "[DONE]" {
			onEvent(StreamEvent{Done: true})
			break
		}
		var chunk rawChunk
		if json.Unmarshal([]byte(payload), &chunk) != nil {
			continue
		}
		ev := StreamEvent{Usage: chunk.Usage}
		if len(chunk.Choices) > 0 {
			ch := chunk.Choices[0]
			ev.Content = ch.Delta.Content
			ev.FinishReason = ch.FinishReason
			for _, tc := range ch.Delta.ToolCalls {
				ev.ToolCalls = append(ev.ToolCalls, ToolCallDelta{
					Index: tc.Index, ID: tc.ID,
					Name: tc.Function.Name, Arguments: tc.Function.Arguments,
				})
			}
		}
		onEvent(ev)
	}
	onEvent(StreamEvent{Done: true})
	return func() { canceled = true; resp.Body.Close() }, nil
}
