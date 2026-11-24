package edge

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"

	"github.com/ongridio/ongrid/internal/pkg/autoapm"
)

type AutoAPMOptions struct {
	Environments []string `json:"environments"`
	Namespaces   []string `json:"namespaces"`
	// ServiceNames counts how many saved host configs already use each service
	// name. A target is scoped to one process on one host, so the same name on
	// two hosts collapses into one APM service even when the processes differ.
	// The editor needs this to warn before a name like "java-8080" is reused by
	// an unrelated application.
	ServiceNames map[string]int `json:"service_names,omitempty"`
}

// AutoAPMOptions derives reusable values from persisted settings, including
// offline devices. No separate catalogue or browser-local history is needed.
func (uc *PluginConfigUC) AutoAPMOptions(ctx context.Context) (*AutoAPMOptions, error) {
	specs, err := uc.repo.ListAutoAPMSpecs(ctx)
	if err != nil {
		return nil, err
	}
	if uc.kubernetesAutoAPMSpecs != nil {
		clusterSpecs, err := uc.kubernetesAutoAPMSpecs(ctx)
		if err != nil {
			return nil, err
		}
		specs = append(specs, clusterSpecs...)
	}
	environments, namespaces := map[string]bool{}, map[string]bool{}
	// Host configs are per device, so one entry per spec means one device.
	serviceHosts := map[string]map[int]bool{}
	for index, raw := range specs {
		var spec autoapm.Spec
		if err := json.Unmarshal([]byte(raw), &spec); err != nil {
			return nil, fmt.Errorf("read saved auto APM settings: %w", err)
		}
		environments[spec.Environment] = true
		if spec.Kubernetes != nil {
			// Kubernetes uses its own namespace inventory, not host service suggestions.
			continue
		}
		for _, target := range spec.Targets {
			environments[target.Environment] = true
			namespaces[target.ServiceNamespace] = true
			if target.ServiceName == "" {
				continue
			}
			if serviceHosts[target.ServiceName] == nil {
				serviceHosts[target.ServiceName] = map[int]bool{}
			}
			serviceHosts[target.ServiceName][index] = true
		}
	}
	values := func(set map[string]bool) []string {
		out := make([]string, 0, len(set))
		for value := range set {
			if value != "" {
				out = append(out, value)
			}
		}
		sort.Strings(out)
		return out
	}
	serviceNames := make(map[string]int, len(serviceHosts))
	for name, hosts := range serviceHosts {
		serviceNames[name] = len(hosts)
	}
	return &AutoAPMOptions{Environments: values(environments), Namespaces: values(namespaces), ServiceNames: serviceNames}, nil
}
