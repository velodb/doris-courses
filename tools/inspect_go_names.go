// Inspect bounded Go runtime function-name metadata, including inline names.
// Supports standard, static linux/amd64 Go 1.26/1.27 executables only. It does
// not execute its input, build a call graph, or decide vulnerability policy.
package main

import (
	"bytes"
	"crypto/sha256"
	"debug/buildinfo"
	"debug/elf"
	"debug/gosym"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

func inspect(path, expected, match string) (map[string]any, error) {
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(expected) || match == "" {
		return nil, fmt.Errorf("require an exact artifact SHA-256 and nonempty match")
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return nil, err
	}
	if hex.EncodeToString(h.Sum(nil)) != expected {
		return nil, fmt.Errorf("artifact hash mismatch")
	}
	bi, err := buildinfo.Read(f)
	if err != nil {
		return nil, err
	}
	if !regexp.MustCompile(`^go1\.(26|27)\.[0-9]+$`).MatchString(bi.GoVersion) {
		return nil, fmt.Errorf("unreviewed compiler version: %s", bi.GoVersion)
	}
	settings := map[string]string{}
	for _, s := range bi.Settings {
		settings[s.Key] = s.Value
	}
	for key, value := range map[string]string{"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0", "-buildmode": "exe"} {
		if settings[key] != value {
			return nil, fmt.Errorf("unsupported build setting %s=%s", key, settings[key])
		}
	}
	e, err := elf.NewFile(f)
	if err != nil {
		return nil, err
	}
	if e.Class != elf.ELFCLASS64 || e.Data != elf.ELFDATA2LSB || e.Machine != elf.EM_X86_64 || e.Type != elf.ET_EXEC {
		return nil, fmt.Errorf("unsupported ELF layout")
	}
	p, text := e.Section(".gopclntab"), e.Section(".text")
	if p == nil || text == nil || p.Size < 72 || p.Size > 512*1024*1024 {
		return nil, fmt.Errorf("missing/oversized metadata")
	}
	data, err := p.Data()
	if err != nil {
		return nil, err
	}
	if binary.LittleEndian.Uint32(data) != 0xfffffff1 || !bytes.Equal(data[4:8], []byte{0, 0, 1, 8}) {
		return nil, fmt.Errorf("unreviewed pclntab header")
	}
	nfunc := binary.LittleEndian.Uint64(data[8:16])
	if nfunc == 0 || nfunc > uint64(len(data))/8 {
		return nil, fmt.Errorf("invalid concrete-function count")
	}
	start, end := binary.LittleEndian.Uint64(data[32:40]), binary.LittleEndian.Uint64(data[40:48])
	if start < 72 || start >= end || end > uint64(len(data)) {
		return nil, fmt.Errorf("invalid function-name bounds")
	}
	previous := end
	for _, off := range []int{48, 56, 64} {
		next := binary.LittleEndian.Uint64(data[off : off+8])
		if next < previous || next > uint64(len(data)) {
			return nil, fmt.Errorf("invalid metadata section order")
		}
		previous = next
	}
	names := map[string]bool{}
	region := data[start:end]
	if region[len(region)-1] != 0 || !utf8.Valid(region) {
		return nil, fmt.Errorf("invalid function-name encoding")
	}
	for _, raw := range bytes.Split(region, []byte{0}) {
		if len(raw) != 0 {
			names[string(raw)] = true
		}
	}
	table, err := gosym.NewTable(nil, gosym.NewLineTable(data, text.Addr))
	if err != nil {
		return nil, err
	}
	if uint64(len(table.Funcs)) != nfunc || nfunc == 0 {
		return nil, fmt.Errorf("incomplete concrete-function decoding")
	}
	concrete := map[string]bool{}
	for _, fn := range table.Funcs {
		if !names[fn.Name] || fn.Name == "" {
			return nil, fmt.Errorf("concrete function absent from bounded names")
		}
		concrete[fn.Name] = true
	}
	all, hits, inlineOnly, crypto := []string{}, []string{}, []string{}, 0
	for name := range names {
		all = append(all, name)
		if strings.Contains(name, "golang.org/x/crypto/") {
			crypto++
		}
		if strings.Contains(name, match) {
			hits = append(hits, name)
			if !concrete[name] {
				inlineOnly = append(inlineOnly, name)
			}
		}
	}
	sort.Strings(all)
	sort.Strings(hits)
	sort.Strings(inlineOnly)
	allHash := sha256.Sum256([]byte(strings.Join(all, "\n") + "\n"))
	regionHash := sha256.Sum256(region)
	return map[string]any{"binary_sha256": expected, "compiler": bi.GoVersion, "settings": settings,
		"pclntab_sha256": fmt.Sprintf("%x", sha256.Sum256(data)), "funcname_region_sha256": fmt.Sprintf("%x", regionHash),
		"funcname_start": start, "funcname_end": end, "unique_function_names": len(names), "concrete_functions": len(table.Funcs),
		"names_without_concrete_entry": len(names) - len(concrete), "unique_concrete_names": len(concrete), "sorted_names_sha256": fmt.Sprintf("%x", allHash),
		"x_crypto_names": crypto, "match": match, "matching_names": hits, "matching_names_without_concrete_entry": inlineOnly,
		"symtab_present": e.Section(".symtab") != nil, "scope": "bounded runtime name table, concrete and inline names; no execution or call graph"}, nil
}

func main() {
	path := flag.String("binary", "", "local static Go executable")
	expected := flag.String("sha256", "", "reviewed artifact SHA-256")
	match := flag.String("match", "golang.org/x/crypto/openpgp", "function-name substring to inspect")
	flag.Parse()
	report, err := inspect(*path, *expected, *match)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err = json.NewEncoder(os.Stdout).Encode(report); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
