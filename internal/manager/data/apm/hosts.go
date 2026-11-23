package apm

import (
	"context"
	"strconv"

	"gorm.io/gorm"

	bizapm "github.com/ongridio/ongrid/internal/manager/biz/apm"
	devicemodel "github.com/ongridio/ongrid/internal/manager/model/device"
)

// HostResolver reads operator-facing host facts for the devices a service was
// observed on. The APM list only needs the display columns, so the query stays a
// single projection over the primary key — no joins, no per-row lookups.
type HostResolver struct {
	db *gorm.DB
}

func NewHostResolver(db *gorm.DB) *HostResolver { return &HostResolver{db: db} }

// ResolveHosts returns the display identity for every device it can find.
// Unknown IDs are omitted: a service can report traffic from a device row that
// was already deleted, and the list still has to render.
func (r *HostResolver) ResolveHosts(ctx context.Context, deviceIDs []uint64) (map[uint64]bizapm.HostRef, error) {
	hosts := make(map[uint64]bizapm.HostRef, len(deviceIDs))
	if r == nil || r.db == nil || len(deviceIDs) == 0 {
		return hosts, nil
	}
	var rows []devicemodel.Device
	if err := r.db.WithContext(ctx).
		Select("id", "name", "hostname", "ip_address").
		Where("id IN ?", deviceIDs).
		Find(&rows).Error; err != nil {
		return nil, err
	}
	for _, row := range rows {
		hosts[row.ID] = bizapm.HostRef{
			DeviceID: strconv.FormatUint(row.ID, 10),
			Name:     row.Name,
			Hostname: row.Hostname,
			IP:       row.IPAddress,
		}
	}
	return hosts, nil
}
