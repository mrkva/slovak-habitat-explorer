# Habitat Explorer SK — Project Notes

## Overview

Single-page PWA for exploring Slovak forest and geological data on an interactive map.
Deployed via GitHub Pages from `main` branch.

**Repo:** `mrkva/slovak-habitat-explorer` (GitHub redirects from old name `Forest-explorer`)
**Live:** https://mrkva.github.io/slovak-habitat-explorer/

## Project structure

```
index.html      — Entire app (HTML + CSS + JS, all inline, no build step)
sw.js           — Service worker (offline caching)
manifest.json   — PWA manifest
icon-192.png    — PWA icon
icon-512.png    — PWA icon
README.md       — Public readme
CLAUDE.md       — This file
```

## Development

- **No build step.** Edit `index.html` directly. Everything is vanilla JS with Leaflet.
- **Branch:** Use `main` only. No feature branches needed for this simple project.
- **Git push often fails** through the proxy. Use `mcp__github__push_files` (with `repo: "forest-explorer"`) as a workaround. GitHub redirects to the renamed repo automatically.
- **After MCP pushes**, local git diverges. Run `git fetch origin main && git reset --hard origin/main` to sync.

## Architecture

### Map layers (index.html)

4 WMS overlay layers, grouped into "forest" and "geology":

| ID   | Name              | Group   | WMS Server                                                              | Default |
|------|-------------------|---------|-------------------------------------------------------------------------|---------|
| jprl | Forest stands     | forest  | gis.nlcsk.org/.../JPRL_ZBGIS/MapServer/WMSServer                       | ON      |
| drev | Tree species      | forest  | gis.nlcsk.org/.../DrevinoveZlozenie/MapServer/WmsServer                 | off     |
| ltyp | Forest types      | forest  | gis.nlcsk.org/.../LesneTypy/MapServer/WMSServer                        | off     |
| geo  | Geological map    | geology | ags.geology.sk/.../GM50/MapServer/WMSServer                            | off     |

Base maps: OpenStreetMap (default) and Esri Satellite.

### Identify (tap-to-query)

Tapping the map queries 3 ArcGIS REST identify endpoints (independent of which visual layers are shown):

1. **JPRL** (forest stands) — returns: category (H/O/U = Commercial/Protective/Special), area
2. **LesneTypy** (forest types) — returns: habitat type name, HSLT classification name
3. **GM50** (geology) — returns: Popis (description), Útvar, Vek1, Súvrstvie

Only active groups are queried (if no forest layer is visible, forest identify is skipped).

CORS: requests go direct first, fall back to `corsproxy.io` proxy on failure.

### Popup display

- Forest section: type name (green, large), HSLT name, category tag (colored badge), area in ha
- Geology section: Popis name (brown), age/formation details
- Source attribution links at bottom of each section

### Service worker (sw.js)

Cache names: `habitat-shell-v1`, `habitat-tiles-v1`, `habitat-data-v1`
- **Bump version** in cache name when changing sw.js behavior (old caches get purged on activate)
- Shell (HTML/JSON): network-first
- Libraries (JS/CSS): cache-first
- Tiles (OSM, Esri, WMS): cache-first, transparent 1x1 PNG fallback when offline
- Identify API: network-first, cached response for offline, empty fallback

### Offline save

"Save offline" button caches OSM tiles (zoom -1 to +3) and identify data for visible area.
Uses `caches.open('habitat-tiles-v1')` directly. Max 10k items limit.

## Key patterns

- `wms(url, attribution, extraOptions)` — helper to create WMS tile layers with shared defaults
- `fetchJson(url)` — fetch with automatic CORS proxy fallback
- `findField(attrs, nameVariants)` — extract field by trying multiple possible attribute names (ArcGIS field names vary)
- `identifyCache` — in-memory cache keyed by `source:lat,lng` (rounded to 3 decimals)
- `isGroupActive(group)` — checks if any layer in a group is on the map
- Layer control has injected FOREST/GEOLOGY section headers via DOM manipulation (setTimeout hack)

## Style notes

- Green theme (#1a5c1a) for forest, brown (#8b5e3c) for geology
- System font stack (-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif)
- Default cursor (not Leaflet's grab cursor) — CSS overrides in place
- Mobile-optimized: no user scaling, standalone PWA mode
