package main

import (
	"crypto/sha256"
	"debug/elf"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func fixture(t *testing.T, stripped bool) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, "leaf"), 0700); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{
		"go.mod":       "module example.invalid/inlineprobe\n\ngo 1.26.0\n",
		"leaf/leaf.go": "package leaf\nfunc AlwaysInline(x int) int { return x+1 }\n",
		"main.go":      "package main\nimport(\"fmt\";\"os\";\"example.invalid/inlineprobe/leaf\")\nfunc main(){fmt.Println(leaf.AlwaysInline(len(os.Args)))}\n",
	}
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	args := []string{"build", "-o", "fixture"}
	if stripped {
		args = append(args, "-ldflags=-s -w")
	}
	args = append(args, ".")
	cmd := exec.Command("go", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GOOS=linux", "GOARCH=amd64", "CGO_ENABLED=0", "GOTOOLCHAIN=local", "GOPROXY=off", "GOENV=off")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("fixture build: %v: %s", err, out)
	}
	return filepath.Join(dir, "fixture")
}

func fileHash(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%x", sha256.Sum256(b))
}

func TestFullyInlinedNameSurvivesStripping(t *testing.T) {
	for _, stripped := range []bool{false, true} {
		t.Run(fmt.Sprint(stripped), func(t *testing.T) {
			path := fixture(t, stripped)
			r, err := inspect(path, fileHash(t, path), "example.invalid/inlineprobe/leaf.AlwaysInline")
			if err != nil {
				t.Fatal(err)
			}
			if len(r["matching_names"].([]string)) != 1 || len(r["matching_names_without_concrete_entry"].([]string)) != 1 {
				t.Fatal("fully inlined positive was missed", r)
			}
			if r["symtab_present"].(bool) == stripped {
				t.Fatal("fixture stripping did not match request")
			}
			negative, err := inspect(path, fileHash(t, path), "golang.org/x/crypto/openpgp")
			if err != nil || len(negative["matching_names"].([]string)) != 0 {
				t.Fatal("unexpected OpenPGP match", err)
			}
		})
	}
}

func TestMismatchAndDamagedMetadataFailClosed(t *testing.T) {
	path := fixture(t, true)
	if _, err := inspect(path, strings.Repeat("0", 64), "probe"); err == nil {
		t.Fatal("hash mismatch accepted")
	}
	e, err := elf.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	off := e.Section(".gopclntab").Offset
	e.Close()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	b[off] = 0
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := inspect(path, fileHash(t, path), "probe"); err == nil {
		t.Fatal("unsupported metadata accepted")
	}
}
