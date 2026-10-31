package device

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/device"
	"github.com/ongridio/ongrid/internal/pkg/errs"
	"github.com/ongridio/ongrid/internal/pkg/tunnel"
)

type fakeNetworkDiscoveryRepo struct {
	rows       []*model.NetworkDiscoveryCandidate
	profiles   map[uint64]*model.DeviceNetwork
	interfaces map[uint64][]*model.NetworkInterface
}

type fakeNetworkSNMPCaller struct {
	response []byte
	calls    int
}

func (f *fakeNetworkSNMPCaller) Call(context.Context, uint64, string, []byte) ([]byte, error) {
	f.calls++
	return f.response, nil
}

func (f *fakeNetworkDiscoveryRepo) UpsertCandidates(_ context.Context, rows []*model.NetworkDiscoveryCandidate) error {
	f.rows = append(f.rows, rows...)
	return nil
}

func (f *fakeNetworkDiscoveryRepo) ListCandidates(context.Context, NetworkCandidateFilter) ([]*model.NetworkDiscoveryCandidate, int64, error) {
	return f.rows, int64(len(f.rows)), nil
}

func (f *fakeNetworkDiscoveryRepo) UpdateCandidate(_ context.Context, candidate *model.NetworkDiscoveryCandidate) error {
	for i, row := range f.rows {
		if row.ID == candidate.ID {
			f.rows[i] = candidate
			return nil
		}
	}
	return errs.ErrNotFound
}

func (f *fakeNetworkDiscoveryRepo) GetCandidate(_ context.Context, id uint64) (*model.NetworkDiscoveryCandidate, error) {
	for _, row := range f.rows {
		if row.ID == id {
			return row, nil
		}
	}
	return nil, errs.ErrNotFound
}

func (f *fakeNetworkDiscoveryRepo) MarkCandidatePromoted(_ context.Context, id, deviceID uint64) error {
	for _, row := range f.rows {
		if row.ID == id {
			row.Status = NetworkCandidateStatusPromoted
			row.PromotedDeviceID = &deviceID
			return nil
		}
	}
	return errs.ErrNotFound
}

func (f *fakeNetworkDiscoveryRepo) UpsertDeviceNetwork(_ context.Context, profile *model.DeviceNetwork) error {
	if f.profiles == nil {
		f.profiles = map[uint64]*model.DeviceNetwork{}
	}
	f.profiles[profile.DeviceID] = profile
	return nil
}

func (f *fakeNetworkDiscoveryRepo) GetNetworkDeviceDetail(_ context.Context, deviceID uint64) (*NetworkDeviceDetail, error) {
	for _, row := range f.rows {
		if row.PromotedDeviceID != nil && *row.PromotedDeviceID == deviceID {
			profile := f.profiles[deviceID]
			if profile == nil {
				profile = &model.DeviceNetwork{DeviceID: deviceID, DeviceKind: "network"}
			}
			return &NetworkDeviceDetail{
				Profile:   profile,
				Candidate: row,
			}, nil
		}
	}
	return nil, errs.ErrNotFound
}

func (f *fakeNetworkDiscoveryRepo) ListDueNetworkPolls(context.Context, time.Time, int) ([]*NetworkDeviceDetail, error) {
	return nil, nil
}
func (f *fakeNetworkDiscoveryRepo) ReplaceNetworkInterfaces(_ context.Context, deviceID uint64, rows []*model.NetworkInterface) error {
	if f.interfaces == nil {
		f.interfaces = map[uint64][]*model.NetworkInterface{}
	}
	f.interfaces[deviceID] = rows
	return nil
}

type fakeNetworkCredentialResolver struct {
	fields map[string]string
	err    error
}

func (f fakeNetworkCredentialResolver) ResolveFields(context.Context, string) (map[string]string, error) {
	return f.fields, f.err
}

func TestNetworkDiscoveryUsecaseKeepsARPAsCandidate(t *testing.T) {
	repo := &fakeNetworkDiscoveryRepo{}
	uc := NewNetworkDiscoveryUsecase(repo)

	accepted, err := uc.IngestNetworkDiscovery(context.Background(), 7, tunnel.NetworkDiscoveryRequest{
		Candidates: []tunnel.NetworkDiscoveryCandidateReport{{
			IPAddress: "192.0.2.1", MAC: "AA-BB-CC-DD-EE-FF", Source: "arp",
		}},
	})
	if err != nil || accepted != 1 || len(repo.rows) != 1 {
		t.Fatalf("accepted=%d rows=%d err=%v", accepted, len(repo.rows), err)
	}
	if repo.rows[0].Status != NetworkCandidateStatusUnknown {
		t.Fatalf("ARP candidate status=%q", repo.rows[0].Status)
	}
	if repo.rows[0].MAC != "aa:bb:cc:dd:ee:ff" {
		t.Fatalf("normalized MAC=%q", repo.rows[0].MAC)
	}
}

func TestNetworkDiscoveryUsecaseRespectsPlatformGate(t *testing.T) {
	repo := &fakeNetworkDiscoveryRepo{}
	uc := NewNetworkDiscoveryUsecase(repo)
	uc.SetEnabledProvider(func(context.Context) bool { return false })

	accepted, err := uc.IngestNetworkDiscovery(context.Background(), 7, tunnel.NetworkDiscoveryRequest{
		Candidates: []tunnel.NetworkDiscoveryCandidateReport{{
			IPAddress: "192.0.2.1", Source: "gateway",
		}},
	})
	if err != nil || accepted != 0 || len(repo.rows) != 0 {
		t.Fatalf("accepted=%d rows=%d err=%v", accepted, len(repo.rows), err)
	}
}

func TestNetworkDiscoveryUsecaseDeduplicatesBatch(t *testing.T) {
	repo := &fakeNetworkDiscoveryRepo{}
	uc := NewNetworkDiscoveryUsecase(repo)
	report := tunnel.NetworkDiscoveryCandidateReport{IPAddress: "192.0.2.10", InterfaceName: "eth0", Source: "gateway"}
	accepted, err := uc.IngestNetworkDiscovery(context.Background(), 7, tunnel.NetworkDiscoveryRequest{
		Candidates: []tunnel.NetworkDiscoveryCandidateReport{report, report},
	})
	if err != nil || accepted != 1 || len(repo.rows) != 1 {
		t.Fatalf("accepted=%d rows=%d err=%v", accepted, len(repo.rows), err)
	}
}

func TestNetworkDiscoveryUsecaseAcceptsLLDPWithoutIPAddress(t *testing.T) {
	repo := &fakeNetworkDiscoveryRepo{}
	uc := NewNetworkDiscoveryUsecase(repo)
	accepted, err := uc.IngestNetworkDiscovery(context.Background(), 7, tunnel.NetworkDiscoveryRequest{
		Candidates: []tunnel.NetworkDiscoveryCandidateReport{{
			InterfaceName: "eth0", Source: "lldp", LLDPChassisID: "00:11:22:33:44:55",
		}},
	})
	if err != nil || accepted != 1 || len(repo.rows) != 1 {
		t.Fatalf("accepted=%d rows=%d err=%v", accepted, len(repo.rows), err)
	}
	if repo.rows[0].Status != NetworkCandidateStatusIdentified {
		t.Fatalf("LLDP candidate status=%q", repo.rows[0].Status)
	}
}

func TestPollNetworkDevicePersistsInterfaceSnapshot(t *testing.T) {
	deviceID := uint64(42)
	repo := &fakeNetworkDiscoveryRepo{
		rows:     []*model.NetworkDiscoveryCandidate{{ID: 4, ObserverEdgeID: 7, IPAddress: "192.0.2.10", Status: NetworkCandidateStatusPromoted, PromotedDeviceID: &deviceID}},
		profiles: map[uint64]*model.DeviceNetwork{deviceID: {DeviceID: deviceID, ManagementAddress: "192.0.2.10", PollEnabled: true, PollCredentialName: "snmp-lab", PollPort: 161}},
	}
	response, err := json.Marshal(tunnel.ProbeNetworkSNMPResponse{OK: true, IPAddress: "192.0.2.10", Interfaces: []tunnel.NetworkInterfaceReport{{IfIndex: 2, Name: "eth0", InOctets: 123, OutOctets: 456, SpeedBps: 1_000_000_000}}})
	if err != nil {
		t.Fatal(err)
	}
	caller := &fakeNetworkSNMPCaller{response: response}
	uc := NewNetworkDiscoveryUsecase(repo)
	uc.SetPromotionDependencies(repo, nil, nil)
	uc.SetEdgeCaller(caller)
	uc.SetCredentialResolver(fakeNetworkCredentialResolver{fields: map[string]string{"version": "v2c", "community": "test"}})
	now := time.Now().UTC()
	if err := uc.PollNetworkDevice(context.Background(), deviceID, now); err != nil {
		t.Fatalf("poll: %v", err)
	}
	profile := repo.profiles[deviceID]
	if profile.ReachabilityStatus != "reachable" || profile.LastPollAt == nil || profile.LastPollError != "" {
		t.Fatalf("profile=%+v", profile)
	}
	if caller.calls != 1 || len(repo.interfaces[deviceID]) != 1 || repo.interfaces[deviceID][0].InOctets != 123 {
		t.Fatalf("calls=%d interfaces=%+v", caller.calls, repo.interfaces[deviceID])
	}
}

func TestValidateSNMPCredentialFields(t *testing.T) {
	for _, test := range []struct {
		name    string
		fields  map[string]string
		wantErr bool
	}{
		{name: "v2c", fields: map[string]string{"community": "readonly"}},
		{name: "v3", fields: map[string]string{"version": "v3", "username": "operator"}},
		{name: "missing community", fields: map[string]string{}, wantErr: true},
		{name: "unknown version", fields: map[string]string{"version": "v1", "community": "public"}, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			err := validateSNMPCredentialFields(test.fields)
			if (err != nil) != test.wantErr {
				t.Fatalf("err=%v wantErr=%v", err, test.wantErr)
			}
			if err != nil && !errors.Is(err, errs.ErrInvalid) {
				t.Fatalf("err=%v, want invalid argument", err)
			}
		})
	}
}

func TestValidateSNMPProbeRequest(t *testing.T) {
	for _, test := range []struct {
		name    string
		req     tunnel.ProbeNetworkSNMPRequest
		wantErr bool
	}{
		{name: "v2c missing community", req: tunnel.ProbeNetworkSNMPRequest{Version: "v2c"}, wantErr: true},
		{name: "v2c blank community", req: tunnel.ProbeNetworkSNMPRequest{Version: "v2c", Community: "   "}, wantErr: true},
		{name: "v2c ok", req: tunnel.ProbeNetworkSNMPRequest{Version: "v2c", Community: "readonly"}},
		{name: "empty version defaults to v2c", req: tunnel.ProbeNetworkSNMPRequest{Community: "readonly"}},
		{name: "empty request", req: tunnel.ProbeNetworkSNMPRequest{}, wantErr: true},
		{name: "v3 missing username", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3"}, wantErr: true},
		{name: "v3 blank username", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "  "}, wantErr: true},
		{name: "v3 no auth ok", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator"}},
		{name: "v3 auth without secret", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "sha256"}, wantErr: true},
		{name: "v3 auth normalised protocol ok", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "SHA-256", AuthSecret: "authenticate"}},
		{name: "v3 unsupported auth protocol", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "md9", AuthSecret: "authenticate"}, wantErr: true},
		{name: "v3 unsupported privacy protocol", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "sha256", AuthSecret: "authenticate", PrivacyProtocol: "aes512", PrivacySecret: "private"}, wantErr: true},
		{name: "v3 privacy without auth", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", PrivacyProtocol: "aes256", PrivacySecret: "private"}, wantErr: true},
		{name: "v3 privacy without secret", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "sha256", AuthSecret: "authenticate", PrivacyProtocol: "aes256"}, wantErr: true},
		{name: "v3 auth privacy ok", req: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator", AuthProtocol: "sha256", AuthSecret: "authenticate", PrivacyProtocol: "aes256", PrivacySecret: "private"}},
		{name: "unknown version", req: tunnel.ProbeNetworkSNMPRequest{Version: "v1", Community: "public"}, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			err := validateSNMPProbeRequest(test.req)
			if (err != nil) != test.wantErr {
				t.Fatalf("err=%v wantErr=%v", err, test.wantErr)
			}
			if err != nil && !errors.Is(err, errs.ErrInvalid) {
				t.Fatalf("err=%v, want invalid argument", err)
			}
		})
	}
}

func TestScanAndPromoteCandidateRejectsMissingCommunityLocally(t *testing.T) {
	candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
		ID: 21, ObserverEdgeID: 7, IPAddress: "10.7.30.254", Status: NetworkCandidateStatusIdentified,
	}}}
	caller := &fakeNetworkSNMPCaller{response: []byte(`{"ok":true,"ip_address":"10.7.30.254"}`)}
	uc := NewNetworkDiscoveryUsecase(candidateRepo)
	uc.SetPromotionDependencies(candidateRepo, &fakeRepo{byID: map[uint64]*model.Device{}}, &fakeLinks{})
	uc.SetEdgeCaller(caller)

	_, err := uc.ScanAndPromoteCandidate(context.Background(), 21, tunnel.ProbeNetworkSNMPRequest{Version: "v2c"}, "")
	if !errors.Is(err, errs.ErrInvalid) {
		t.Fatalf("err=%v, want invalid argument", err)
	}
	if errors.Is(err, errs.ErrConflict) {
		t.Fatalf("err=%v, must not be reported as a conflict", err)
	}
	if caller.calls != 0 {
		t.Fatalf("edge calls=%d, want 0: the request must be rejected before the tunnel", caller.calls)
	}
	if candidateRepo.rows[0].Status != NetworkCandidateStatusIdentified {
		t.Fatalf("status=%q, candidate must stay untouched", candidateRepo.rows[0].Status)
	}
}

func TestScanAndPromoteCandidateMapsEdgeFailureKind(t *testing.T) {
	for _, test := range []struct {
		name    string
		request tunnel.ProbeNetworkSNMPRequest
		body    string
		want    error
		forbid  error
	}{
		{
			name:    "edge classified parameter error becomes invalid",
			request: tunnel.ProbeNetworkSNMPRequest{Version: "v3", Username: "operator"},
			body:    `{"ok":false,"error":"SNMP username is required for v3","error_kind":"invalid"}`,
			want:    errs.ErrInvalid,
			forbid:  errs.ErrConflict,
		},
		{
			name:    "legacy edge without kind keeps conflict",
			request: tunnel.ProbeNetworkSNMPRequest{Version: "v2c", Community: "readonly"},
			body:    `{"ok":false,"error":"SNMP connect: request timeout (after 3s)"}`,
			want:    errs.ErrConflict,
			forbid:  errs.ErrInvalid,
		},
		{
			name:    "unreachable device stays conflict",
			request: tunnel.ProbeNetworkSNMPRequest{Version: "v2c", Community: "readonly"},
			body:    `{"ok":false,"error":"SNMP get: no such host","error_kind":""}`,
			want:    errs.ErrConflict,
			forbid:  errs.ErrInvalid,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
				ID: 22, ObserverEdgeID: 7, IPAddress: "10.7.30.254", Status: NetworkCandidateStatusIdentified,
			}}}
			caller := &fakeNetworkSNMPCaller{response: []byte(test.body)}
			uc := NewNetworkDiscoveryUsecase(candidateRepo)
			uc.SetPromotionDependencies(candidateRepo, &fakeRepo{byID: map[uint64]*model.Device{}}, &fakeLinks{})
			uc.SetEdgeCaller(caller)

			_, err := uc.ScanAndPromoteCandidate(context.Background(), 22, test.request, "")
			if !errors.Is(err, test.want) {
				t.Fatalf("err=%v, want %v", err, test.want)
			}
			if test.forbid != nil && errors.Is(err, test.forbid) {
				t.Fatalf("err=%v, must not match %v", err, test.forbid)
			}
			if caller.calls != 1 {
				t.Fatalf("edge calls=%d, want 1", caller.calls)
			}
			if candidateRepo.rows[0].Status != NetworkCandidateStatusIdentified {
				t.Fatalf("status=%q, failed verification must not change the candidate", candidateRepo.rows[0].Status)
			}
		})
	}
}

func TestPromoteCandidateIsExplicitAndIdempotent(t *testing.T) {
	candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
		ID: 9, ObserverEdgeID: 7, IPAddress: "192.0.2.10", Status: NetworkCandidateStatusSNMPVerified,
		SourceDataJSON: `{"sys_name":"sw-a","vendor":"acme","model":"x1"}`,
	}}}
	deviceRepo := &fakeRepo{byID: map[uint64]*model.Device{}}
	links := &fakeLinks{}
	uc := NewNetworkDiscoveryUsecase(candidateRepo)
	uc.SetPromotionDependencies(candidateRepo, deviceRepo, links)

	device, err := uc.PromoteCandidate(context.Background(), 9, "edge-switch")
	if err != nil || device == nil || device.Roles != model.RoleBitNetwork {
		t.Fatalf("device=%+v err=%v", device, err)
	}
	if len(links.linked) != 1 || links.linked[0] != [2]uint64{7, device.ID} {
		t.Fatalf("links=%v", links.linked)
	}
	again, err := uc.PromoteCandidate(context.Background(), 9, "ignored-name")
	if err != nil || again.ID != device.ID {
		t.Fatalf("idempotent device=%+v err=%v", again, err)
	}
}

func TestPromoteCandidateRequiresSNMPVerification(t *testing.T) {
	candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
		ID: 11, ObserverEdgeID: 7, IPAddress: "192.0.2.11", Status: NetworkCandidateStatusIdentified,
	}}}
	uc := NewNetworkDiscoveryUsecase(candidateRepo)
	uc.SetPromotionDependencies(candidateRepo, &fakeRepo{byID: map[uint64]*model.Device{}}, &fakeLinks{})
	if _, err := uc.PromoteCandidate(context.Background(), 11, ""); !errors.Is(err, errs.ErrConflict) {
		t.Fatalf("err=%v, want conflict", err)
	}
}

func TestPromoteCandidateRepairsStalePromotedStatus(t *testing.T) {
	deviceID := uint64(42)
	candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
		ID: 10, Status: NetworkCandidateStatusIdentified, PromotedDeviceID: &deviceID,
	}}}
	deviceRepo := &fakeRepo{byID: map[uint64]*model.Device{
		deviceID: {ID: deviceID, Name: "existing-network-device", Roles: model.RoleBitNetwork},
	}}
	uc := NewNetworkDiscoveryUsecase(candidateRepo)
	uc.SetPromotionDependencies(candidateRepo, deviceRepo, &fakeLinks{})

	device, err := uc.PromoteCandidate(context.Background(), 10, "")
	if err != nil || device.ID != deviceID {
		t.Fatalf("device=%+v err=%v", device, err)
	}
	if candidateRepo.rows[0].Status != NetworkCandidateStatusPromoted {
		t.Fatalf("repaired status=%q, want %q", candidateRepo.rows[0].Status, NetworkCandidateStatusPromoted)
	}
}

func TestScanAndPromoteCandidateRefreshesExistingNetworkDevice(t *testing.T) {
	deviceID := uint64(42)
	candidateRepo := &fakeNetworkDiscoveryRepo{rows: []*model.NetworkDiscoveryCandidate{{
		ID: 12, ObserverEdgeID: 7, IPAddress: "192.0.2.12", Source: "lldp",
		Status: NetworkCandidateStatusPromoted, PromotedDeviceID: &deviceID,
	}}}
	deviceRepo := &fakeRepo{byID: map[uint64]*model.Device{
		deviceID: {ID: deviceID, Name: "existing-network-device", Roles: model.RoleBitNetwork},
	}}
	caller := &fakeNetworkSNMPCaller{response: []byte(`{
		"ok":true,"ip_address":"192.0.2.12","sys_name":"switch-a",
		"interfaces":[{"if_index":1,"name":"eth0","oper_status":"up"}]
	}`)}
	uc := NewNetworkDiscoveryUsecase(candidateRepo)
	uc.SetPromotionDependencies(candidateRepo, deviceRepo, &fakeLinks{})
	uc.SetEdgeCaller(caller)

	device, err := uc.ScanAndPromoteCandidate(context.Background(), 12, tunnel.ProbeNetworkSNMPRequest{
		Version: "v2c", Community: "readonly",
	}, "")
	if err != nil || device.ID != deviceID {
		t.Fatalf("device=%+v err=%v", device, err)
	}
	if caller.calls != 1 || candidateRepo.rows[0].Source != "snmp" || candidateRepo.rows[0].InterfacesJSON == "[]" {
		t.Fatalf("calls=%d candidate=%+v", caller.calls, candidateRepo.rows[0])
	}
	if candidateRepo.rows[0].Status != NetworkCandidateStatusPromoted {
		t.Fatalf("status=%q", candidateRepo.rows[0].Status)
	}
}
