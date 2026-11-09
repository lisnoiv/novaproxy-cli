// Package config manages ~/.novaproxy/config.json: API key, model, mode,
// MCP servers, permissions, theme. Permissions default to "ask".
package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

const KeyPrefix = "px_live_"

type MCPServer struct {
	Command string            `json:"command,omitempty"`
	Args    []string          `json:"args,omitempty"`
	Env     map[string]string `json:"env,omitempty"`
	URL     string            `json:"url,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
	Timeout float64           `json:"timeout,omitempty"`
}

type Config struct {
	APIKey        string              `json:"api_key"`
	Model         string              `json:"model"`
	Mode          string              `json:"mode"` // code | chat | reasoning
	SystemPrompt  string              `json:"system_prompt,omitempty"`
	MCPServers    map[string]MCPServer `json:"mcp_servers,omitempty"`
	Theme         string              `json:"theme"`
	Permissions   map[string]string   `json:"permissions,omitempty"` // bash|write -> ask|allow|deny
	DisabledTools []string            `json:"disabled_tools,omitempty"`
	Autocompact   bool                `json:"autocompact"`
}

func Dir() string {
	if v := os.Getenv("NOVAPROXY_HOME"); v != "" {
		return v
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".novaproxy"
	}
	return filepath.Join(home, ".novaproxy")
}

func Path() string { return filepath.Join(Dir(), "config.json") }

func Defaults() *Config {
	return &Config{
		Model:       "qwen3-max",
		Mode:        "code",
		Theme:       "opencode",
		Permissions: map[string]string{"bash": "ask", "write": "ask"},
		Autocompact: true,
	}
}

func Load() *Config {
	cfg := Defaults()
	data, err := os.ReadFile(Path())
	if err != nil {
		return cfg
	}
	var raw Config
	if json.Unmarshal(data, &raw) != nil {
		return cfg
	}
	if raw.APIKey != "" {
		cfg.APIKey = raw.APIKey
	}
	if raw.Model != "" {
		cfg.Model = raw.Model
	}
	if raw.Mode != "" {
		cfg.Mode = raw.Mode
	}
	if raw.SystemPrompt != "" {
		cfg.SystemPrompt = raw.SystemPrompt
	}
	if raw.MCPServers != nil {
		cfg.MCPServers = raw.MCPServers
	}
	if raw.Theme != "" {
		cfg.Theme = raw.Theme
	}
	if raw.Permissions != nil {
		for k, v := range raw.Permissions {
			cfg.Permissions[k] = v
		}
	}
	cfg.DisabledTools = raw.DisabledTools
	cfg.Autocompact = raw.Autocompact
	return cfg
}

func (c *Config) Save() error {
	if err := os.MkdirAll(Dir(), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := Path() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, Path())
}

func KeyFormatOK(key string) bool {
	return strings.HasPrefix(key, KeyPrefix) && len(key) > len(KeyPrefix)+4
}
