package knowledge

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"testing"

	"github.com/go-chi/chi/v5"
	model "github.com/ongridio/ongrid/internal/manager/model/knowledge"
	"github.com/ongridio/ongrid/internal/pkg/errs"
)

type repoUpdateRecorder struct {
	Service
	id     uint64
	branch string
}

func (s *repoUpdateRecorder) UpdateRepo(_ context.Context, id uint64, branch, description string) (*model.Repository, error) {
	s.id, s.branch = id, branch
	return &model.Repository{ID: id, URL: "https://example/repo.git", Branch: branch, Description: description}, nil
}

func TestRepositoryConfigHTTP(t *testing.T) {
	svc := &repoUpdateRecorder{}
	router := chi.NewRouter()
	NewHandler(svc).Register(router)
	rec := jsonReq(t, router, http.MethodPatch, "/v1/knowledge/repos/7", map[string]any{"branch": "v2", "description": "docs"})
	if rec.Code != http.StatusOK || svc.id != 7 || svc.branch != "v2" {
		t.Fatalf("PATCH: %d %s", rec.Code, rec.Body.String())
	}
	svc.branch = ""
	rec = jsonReq(t, router, http.MethodPatch, "/v1/knowledge/repos/7", map[string]any{"url": "https://other/repo.git", "branch": "v3"})
	if rec.Code != http.StatusBadRequest || svc.branch != "" {
		t.Fatalf("URL update accepted: %d", rec.Code)
	}
	actual, _ := newE2E(t)
	rec = jsonReq(t, actual, http.MethodPost, "/v1/knowledge/repos", map[string]any{"url": "file:///etc", "branch": "main"})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid create: %d %s", rec.Code, rec.Body.String())
	}
}

type refsRecorder struct {
	Service
	url      string
	branches []string
	tags     []string
	err      error
}

func (s *refsRecorder) ListRefs(_ context.Context, url string) ([]string, []string, error) {
	s.url = url
	return s.branches, s.tags, s.err
}

func TestRepositoryRefsHTTP(t *testing.T) {
	svc := &refsRecorder{branches: []string{"main", "dev"}, tags: []string{"v1.0.0"}}
	router := chi.NewRouter()
	NewHandler(svc).Register(router)
	rec := req(t, router, http.MethodGet, "/v1/knowledge/repos/refs?url="+url.QueryEscape("https://example/repo.git"), "", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("GET refs: %d %s", rec.Code, rec.Body.String())
	}
	if svc.url != "https://example/repo.git" {
		t.Fatalf("url not forwarded to service: %q", svc.url)
	}
	var body struct {
		Branches []string `json:"branches"`
		Tags     []string `json:"tags"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(body.Branches, []string{"main", "dev"}) || !reflect.DeepEqual(body.Tags, []string{"v1.0.0"}) {
		t.Fatalf("wrong refs body: %+v", body)
	}
}

func TestRepositoryRefsHTTPError(t *testing.T) {
	svc := &refsRecorder{err: errors.Join(errs.ErrInvalid, errors.New("listing repository refs failed: fatal: repo not found"))}
	router := chi.NewRouter()
	NewHandler(svc).Register(router)
	rec := req(t, router, http.MethodGet, "/v1/knowledge/repos/refs?url="+url.QueryEscape("https://example/missing.git"), "", nil)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("refs error not mapped to 400: %d %s", rec.Code, rec.Body.String())
	}
}
