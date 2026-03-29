#!/usr/bin/env python3
"""Generate static data tiles from ArcGIS MapServer endpoints for offline use.

Queries ArcGIS REST API for features within each z12 tile covering Slovakia,
saves results as GeoJSON files under data/{source}/{z}/{x}/{y}.json.

Usage:
    python3 scripts/generate_tiles.py [--source jprl|lestypy|geo] [--resume]

Requires: Python 3.6+ (stdlib only, no dependencies)
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Slovakia bounding box (slightly padded)
SK_SOUTH, SK_NORTH = 47.73, 49.61
SK_WEST, SK_EAST = 16.83, 22.57

ZOOM = 12
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# Geometry simplification tolerance in degrees (~200m at Slovak latitudes)
MAX_OFFSET = 0.002

# Rate limiting
REQUEST_DELAY = 0.15  # seconds between requests per source
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

SOURCES = {
    'jprl': {
        'url': 'https://gis.nlcsk.org/arcgis/rest/services/MPRV/JPRL_ZBGIS/MapServer/0/query',
        'fields': 'KL,Plocha,OBJECTID',
        'group': 'forest',
    },
    'lestypy': {
        'url': 'https://gis.nlcsk.org/arcgis/rest/services/Inspire/LesneTypy/MapServer/0/query',
        'fields': '*',
        'group': 'forest',
    },
    'geo': {
        'url': 'https://ags.geology.sk/arcgis/rest/services/WebServices/GM50/MapServer/2/query',
        'fields': 'Popis,Útvar,Vek1,Súvrstvie,OBJECTID',
        'group': 'geology',
    },
}


def latlng_to_tile(lat, lng, z):
    n = 2 ** z
    x = int((lng + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)
    return x, y


def tile_bounds(x, y, z):
    """Return (west, south, east, north) for a tile."""
    n = 2 ** z
    west = x / n * 360 - 180
    east = (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


def get_tiles():
    """Get all z12 tiles covering Slovakia."""
    x_min, y_min = latlng_to_tile(SK_NORTH, SK_WEST, ZOOM)  # NW corner
    x_max, y_max = latlng_to_tile(SK_SOUTH, SK_EAST, ZOOM)  # SE corner
    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


def query_features(source_url, bbox, fields, offset=MAX_OFFSET):
    """Query ArcGIS MapServer for features within a bounding box."""
    west, south, east, north = bbox
    params = {
        'where': '1=1',
        'geometry': f'{west},{south},{east},{north}',
        'geometryType': 'esriGeometryEnvelope',
        'spatialRel': 'esriSpatialRelIntersects',
        'outFields': fields,
        'returnGeometry': 'true',
        'outSR': '4326',
        'maxAllowableOffset': str(offset),
        'f': 'json',
    }
    url = source_url + '?' + urllib.parse.urlencode(params)

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'HabitatExplorer-TileGen/1.0',
                'Referer': 'https://mrkva.github.io/slovak-habitat-explorer/',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if 'error' in data:
                print(f"  API error: {data['error'].get('message', data['error'])}")
                return None

            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  Retry {attempt + 1}/{MAX_RETRIES}: {e}")
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print(f"  Failed after {MAX_RETRIES} attempts: {e}")
                return None


def arcgis_to_geojson(data):
    """Convert ArcGIS JSON response to GeoJSON FeatureCollection."""
    features = []
    for feat in data.get('features', []):
        geom = feat.get('geometry', {})
        attrs = feat.get('attributes', {})

        # Convert ArcGIS geometry to GeoJSON
        rings = geom.get('rings')
        if rings:
            # Separate outer rings from holes using the ring orientation rule
            # ArcGIS: clockwise = outer, counter-clockwise = hole
            # GeoJSON: counter-clockwise = outer, clockwise = hole
            # For simplicity, treat first ring as outer, rest as holes per polygon
            # A more robust approach would check ring area/orientation
            geojson_geom = {
                'type': 'Polygon',
                'coordinates': rings,
            }
        else:
            continue  # Skip non-polygon features

        # Clean up null attributes
        clean_attrs = {k: v for k, v in attrs.items()
                       if v is not None and v != 'Null' and v != ''}

        features.append({
            'type': 'Feature',
            'geometry': geojson_geom,
            'properties': clean_attrs,
        })

    return {'type': 'FeatureCollection', 'features': features}


def generate_source(name, source, resume=False):
    """Generate all tiles for a single data source."""
    tiles = get_tiles()
    out_dir = os.path.join(OUTPUT_DIR, name, str(ZOOM))
    total = len(tiles)
    saved = 0
    skipped = 0
    empty = 0

    print(f"\n=== {name} === ({total} tiles)")

    for i, (x, y) in enumerate(tiles):
        tile_dir = os.path.join(out_dir, str(x))
        tile_path = os.path.join(tile_dir, f'{y}.json')

        if resume and os.path.exists(tile_path):
            skipped += 1
            continue

        bbox = tile_bounds(x, y, ZOOM)
        data = query_features(source['url'], bbox, source['fields'])

        if data and data.get('features'):
            geojson = arcgis_to_geojson(data)
            if geojson['features']:
                os.makedirs(tile_dir, exist_ok=True)
                with open(tile_path, 'w') as f:
                    json.dump(geojson, f, separators=(',', ':'))
                saved += 1
            else:
                empty += 1
        else:
            empty += 1

        # Progress
        done = i + 1
        if done % 50 == 0 or done == total:
            print(f"  [{done}/{total}] saved={saved} empty={empty} skipped={skipped}")

        time.sleep(REQUEST_DELAY)

    print(f"  Done: {saved} tiles saved, {empty} empty, {skipped} skipped")
    return saved


def main():
    parser = argparse.ArgumentParser(description='Generate static data tiles')
    parser.add_argument('--source', choices=list(SOURCES.keys()),
                        help='Generate tiles for a specific source only')
    parser.add_argument('--resume', action='store_true',
                        help='Skip tiles that already exist')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show tile count without fetching')
    args = parser.parse_args()

    tiles = get_tiles()
    print(f"Slovakia coverage: {len(tiles)} tiles at z{ZOOM}")
    print(f"Output: {OUTPUT_DIR}")

    if args.dry_run:
        for name in (SOURCES if not args.source else {args.source: SOURCES[args.source]}):
            print(f"  {name}: {len(tiles)} tiles to generate")
        return

    sources = {args.source: SOURCES[args.source]} if args.source else SOURCES
    total_saved = 0

    for name, source in sources.items():
        total_saved += generate_source(name, source, resume=args.resume)

    print(f"\nAll done! {total_saved} tiles generated in {OUTPUT_DIR}/")


if __name__ == '__main__':
    main()
