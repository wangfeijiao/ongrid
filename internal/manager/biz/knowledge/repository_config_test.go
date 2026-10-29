package knowledge

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/ongridio/ongrid/internal/pkg/errs"
)

func TestRepositoryURLValidation(t *testing.T) {
	for _, raw := range []string{"https://example.com/a.git", "http://git.internal/a.git", "git@git.internal:team/a.git", "ssh://git@git.internal:2222/team/a.git"} {
		if err := validateRepositoryURL(raw); err != nil {
			t.Fatalf("valid URL rejected: %s %v", raw, err)
		}
	}
	for _, raw := range []string{"", "/etc", "file:///etc", "ext::sh -c anything", "https://token@example.com/a.git", "ssh://git:password@example.com/a.git", "https://example.com/a.git?token=secret", "git@-host:a.git", "git@host:", "https://example.com/a.git\n"} {
		if err := validateRepositoryURL(raw); err == nil {
			t.Fatalf("invalid URL accepted: %q", raw)
		}
	}
}

func TestMissingRefWithSSHWarning(t *testing.T) {
	dir := t.TempDir()
	script := "#!/bin/sh\nif [ \"$1\" = check-ref-format ]; then exit 0; fi\nprintf '%s\\n' \"Warning: Permanently added host to known hosts.\" >&2\nexit 2\n"
	if err := os.WriteFile(filepath.Join(dir, "git"), []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	u := &Usecase{}
	err := u.validateRepository(context.Background(), "https://example/repo.git", "missing")
	if err == nil || !strings.Contains(err.Error(), "branch or tag") || !strings.Contains(err.Error(), "not found") || strings.Contains(err.Error(), "known hosts") {
		t.Fatalf("wrong missing-ref error: %v", err)
	}
}

func TestParseRemoteRefs(t *testing.T) {
	out := strings.Join([]string{
		"abc123\trefs/heads/main",
		"def456\trefs/heads/dev",
		"111111\trefs/tags/v1.0.0",
		"222222\trefs/tags/v1.0.0^{}",
		"Warning: Permanently added 'example' to the list of known hosts.",
		"333333\trefs/tags/v1.0.0",
	}, "\n")
	branches, tags := parseRemoteRefs(out)
	if want := []string{"main", "dev"}; !reflect.DeepEqual(branches, want) {
		t.Fatalf("branches = %v, want %v", branches, want)
	}
	if want := []string{"v1.0.0"}; !reflect.DeepEqual(tags, want) {
		t.Fatalf("tags = %v, want %v", tags, want)
	}
}

func TestListRefsInvalidURL(t *testing.T) {
	u := &Usecase{}
	if _, _, err := u.ListRefs(context.Background(), "file:///etc"); err == nil || !strings.Contains(err.Error(), "HTTP(S) or SSH") {
		t.Fatalf("invalid URL not rejected: %v", err)
	}
}

func TestListRefsGitFailure(t *testing.T) {
	dir := t.TempDir()
	script := "#!/bin/sh\nprintf '%s\\n' \"fatal: could not read from remote repository.\" >&2\nexit 128\n"
	if err := os.WriteFile(filepath.Join(dir, "git"), []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	u := &Usecase{}
	if _, _, err := u.ListRefs(context.Background(), "https://example/repo.git"); err == nil || !strings.Contains(err.Error(), "listing repository refs failed") {
		t.Fatalf("wrong ls-remote failure error: %v", err)
	}
}

func TestListRefsTimeout(t *testing.T) {
	dir := t.TempDir()
	// Busy-wait instead of `sleep` so CommandContext kills the direct child
	// (the fake git) and its output pipes close immediately — no orphaned
	// grandchild holds the pipe open and drags the test out.
	script := "#!/bin/sh\nwhile :; do :; done\n"
	if err := os.WriteFile(filepath.Join(dir, "git"), []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	u := &Usecase{}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	_, _, err := u.ListRefs(ctx, "https://example/repo.git")
	if err == nil || !strings.Contains(err.Error(), "timed out") || !strings.Contains(err.Error(), errs.ErrInvalid.Error()) {
		t.Fatalf("wrong timeout error: %v", err)
	}
}
