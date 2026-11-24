package alert

import (
	"context"
	"testing"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
)

// TestRecordFiringRefreshesContentOnRefire verifies that a second firing for
// the same dedupe_key refreshes the incident's summary/value/threshold from the
// latest firing rather than freezing the values captured at first creation.
// This covers both the already-open "bump" path and the resolved "reopen" path.
func TestRecordFiringRefreshesContentOnRefire(t *testing.T) {
	ptr := func(f float64) *float64 { return &f }

	t.Run("open_bump_refreshes_content", func(t *testing.T) {
		repo := newFakeRepo()
		uc := NewUsecase(repo, nil)
		now := time.Date(2026, 6, 1, 12, 0, 0, 0, time.UTC)

		first, err := uc.RecordFiring(context.Background(), FiringInput{
			ScopeType:  model.RuleScopeGlobal,
			Rule:       "device_offline",
			Severity:   "warning",
			Summary:    "device offline (> 90)",
			Value:      ptr(120),
			Threshold:  ptr(90),
			DedupeKey:  "global:device_offline",
			OccurredAt: now,
		})
		if err != nil {
			t.Fatalf("RecordFiring first: %v", err)
		}
		if !first.IsNew {
			t.Fatalf("first firing should create a new incident")
		}

		second, err := uc.RecordFiring(context.Background(), FiringInput{
			ScopeType:  model.RuleScopeGlobal,
			Rule:       "device_offline",
			Severity:   "warning",
			Summary:    "device offline (> 300)",
			Value:      ptr(400),
			Threshold:  ptr(300),
			DedupeKey:  "global:device_offline",
			OccurredAt: now.Add(time.Minute),
		})
		if err != nil {
			t.Fatalf("RecordFiring second: %v", err)
		}
		if second.IsNew || second.IsReopen {
			t.Fatalf("second firing should bump existing incident, got IsNew=%v IsReopen=%v", second.IsNew, second.IsReopen)
		}

		got := repo.incidents[second.Incident.ID]
		if got.Summary != "device offline (> 300)" {
			t.Fatalf("summary = %q, want refreshed to second firing", got.Summary)
		}
		if got.Value == nil || *got.Value != 400 {
			t.Fatalf("value = %v, want 400", got.Value)
		}
		if got.Threshold == nil || *got.Threshold != 300 {
			t.Fatalf("threshold = %v, want 300", got.Threshold)
		}
		// A continuous firing (open -> open bump) must keep the original
		// first_fired_at: it is still the same fault episode.
		if !got.FirstFiredAt.Equal(now) {
			t.Fatalf("first_fired_at = %v, want kept at first firing %v on bump", got.FirstFiredAt, now)
		}
	})

	t.Run("resolved_reopen_refreshes_content", func(t *testing.T) {
		repo := newFakeRepo()
		uc := NewUsecase(repo, nil)
		now := time.Date(2026, 6, 1, 12, 0, 0, 0, time.UTC)

		first, err := uc.RecordFiring(context.Background(), FiringInput{
			ScopeType:  model.RuleScopeGlobal,
			Rule:       "device_offline",
			Severity:   "warning",
			Summary:    "device offline (> 90)",
			Value:      ptr(120),
			Threshold:  ptr(90),
			DedupeKey:  "global:device_offline",
			OccurredAt: now,
		})
		if err != nil {
			t.Fatalf("RecordFiring first: %v", err)
		}

		// Mark the incident resolved so the next firing takes the reopen path.
		repo.incidents[first.Incident.ID].Status = model.IncidentStatusResolved

		second, err := uc.RecordFiring(context.Background(), FiringInput{
			ScopeType:  model.RuleScopeGlobal,
			Rule:       "device_offline",
			Severity:   "warning",
			Summary:    "device offline (> 300)",
			Value:      ptr(400),
			Threshold:  ptr(300),
			DedupeKey:  "global:device_offline",
			OccurredAt: now.Add(time.Minute),
		})
		if err != nil {
			t.Fatalf("RecordFiring second: %v", err)
		}
		if !second.IsReopen {
			t.Fatalf("second firing should reopen resolved incident")
		}

		got := repo.incidents[second.Incident.ID]
		if got.Summary != "device offline (> 300)" {
			t.Fatalf("summary = %q, want refreshed to second firing", got.Summary)
		}
		if got.Value == nil || *got.Value != 400 {
			t.Fatalf("value = %v, want 400", got.Value)
		}
		if got.Threshold == nil || *got.Threshold != 300 {
			t.Fatalf("threshold = %v, want 300", got.Threshold)
		}
		// A reopen starts a new fault episode: first_fired_at and event_count
		// must reset to this firing, not stay frozen at the original creation.
		if !got.FirstFiredAt.Equal(now.Add(time.Minute)) {
			t.Fatalf("first_fired_at = %v, want reset to reopen firing %v", got.FirstFiredAt, now.Add(time.Minute))
		}
		if got.EventCount != 1 {
			t.Fatalf("event_count = %d, want reset to 1 on reopen", got.EventCount)
		}
	})
}
