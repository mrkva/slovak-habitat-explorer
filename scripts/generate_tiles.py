#!/usr/bin/env python3
"""Generate static data tiles from ArcGIS MapServer endpoints for offline use.

Queries ArcGIS REST API for features within each z12 tile covering Slovakia,
saves results as GeoJSON files under data/{source}/{z}/{x}/{y}.json.

Usage:
    python3 scripts/generate_tiles.py [--source jprl|lestypy|geo] [--workers 4]

Requires: Python 3.6+ (stdlib only, no dependencies)
"""

import argparse
import concurrent.futures
import json
import math
import os
import sys
import threading
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

MAX_RETRIES = 4
RETRY_DELAY = 5  # seconds (multiplied by attempt number)

# File that tracks empty tiles so we don't re-query them
EMPTY_MANIFEST = '.empty_tiles'

SOURCES = {
    'jprl': {
        'url': 'https://gis.nlcsk.org/arcgis/rest/services/MPRV/JPRL_ZBGIS/MapServer/1/query',
        'fields': 'KL,Plocha,Vek_porastu,OBJECTID',
        'group': 'forest',
    },
    'lestypy': {
        'url': 'https://gis.nlcsk.org/arcgis/rest/services/Inspire/LesneTypy/MapServer/0/query',
        'fields': 'NLT1,NHSLT,hlLT,KAT,OBJECTID',
        'group': 'forest',
    },
    'geo': {
        'url': 'https://ags.geology.sk/arcgis/rest/services/WebServices/GM50/MapServer/2/query',
        'fields': 'popis,utv,vek1,ksuvrstvie,objectid',
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
    x_min, y_min = latlng_to_tile(SK_NORTH, SK_WEST, ZOOM)
    x_max, y_max = latlng_to_tile(SK_SOUTH, SK_EAST, ZOOM)
    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


def fetch_json(url):
    """Fetch a URL and return parsed JSON, with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'HabitatExplorer-TileGen/1.0',
                'Referer': 'https://mrkva.github.io/slovak-habitat-explorer/',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if 'error' in data:
                err = json.dumps(data['error'], ensure_ascii=False)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                print(f"  API error: {err}")
                return None

            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print(f"  Failed after {MAX_RETRIES} attempts: {e}")
                return None


def query_features(source_url, bbox, fields, simplify=MAX_OFFSET):
    """Query ArcGIS MapServer for features within a bounding box, with pagination."""
    west, south, east, north = bbox
    all_features = []
    result_offset = 0
    first_request = True
    last_data = None

    while True:
        params = {
            'where': '1=1',
            'geometry': f'{west},{south},{east},{north}',
            'geometryType': 'esriGeometryEnvelope',
            'spatialRel': 'esriSpatialRelIntersects',
            'inSR': '4326',
            'outFields': fields,
            'returnGeometry': 'true',
            'outSR': '4326',
            'maxAllowableOffset': str(simplify),
            'f': 'json',
        }
        if not first_request:
            params['resultOffset'] = str(result_offset)

        url = source_url + '?' + urllib.parse.urlencode(params)
        data = fetch_json(url)
        first_request = False

        if not data:
            break

        last_data = data
        features = data.get('features', [])
        all_features.extend(features)

        if data.get('exceededTransferLimit') and features:
            result_offset += len(features)
        else:
            break

    if last_data is not None:
        return {'features': all_features, 'fieldAliases': last_data.get('fieldAliases', {})}
    return None  # actual error (network/API failure)


def arcgis_to_geojson(data):
    """Convert ArcGIS JSON response to GeoJSON FeatureCollection."""
    aliases = data.get('fieldAliases', {})

    features = []
    for feat in data.get('features', []):
        geom = feat.get('geometry', {})
        attrs = feat.get('attributes', {})

        rings = geom.get('rings')
        if not rings:
            continue

        geojson_geom = {
            'type': 'Polygon',
            'coordinates': rings,
        }

        clean_attrs = {}
        for k, v in attrs.items():
            if v is None or v == 'Null' or v == '':
                continue
            alias = aliases.get(k, k)
            clean_attrs[alias] = v

        features.append({
            'type': 'Feature',
            'geometry': geojson_geom,
            'properties': clean_attrs,
        })

    return {'type': 'FeatureCollection', 'features': features}


def load_empty_set(source_dir):
    """Load the set of tiles known to be empty."""
    path = os.path.join(source_dir, EMPTY_MANIFEST)
    if os.path.exists(path):
        with open(path) as f:
            return set(f.read().split())
    return set()


def save_empty_set(source_dir, empty_set):
    """Persist the set of empty tile keys."""
    path = os.path.join(source_dir, EMPTY_MANIFEST)
    os.makedirs(source_dir, exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(sorted(empty_set)))


def generate_source(name, source, num_workers=4, force=False):
    """Generate all tiles for a single data source using parallel workers."""
    tiles = get_tiles()
    out_dir = os.path.join(OUTPUT_DIR, name, str(ZOOM))
    total = len(tiles)

    # Load known empty tiles for resume
    source_dir = os.path.join(OUTPUT_DIR, name)
    empty_set = load_empty_set(source_dir) if not force else set()

    # Filter to tiles that need work
    todo = []
    skipped = 0
    for (x, y) in tiles:
        tile_key = f'{x}/{y}'
        tile_path = os.path.join(out_dir, str(x), f'{y}.json')
        if not force and os.path.exists(tile_path):
            skipped += 1
        elif not force and tile_key in empty_set:
            skipped += 1
        else:
            todo.append((x, y))

    print(f"\n=== {name} === ({total} tiles, {skipped} cached, {len(todo)} to fetch)")

    if not todo:
        print("  Nothing to do!")
        return 0

    saved = 0
    empty = 0
    errors = 0
    lock = threading.Lock()
    rate_lock = threading.Lock()
    last_request_time = [0.0]  # mutable for closure
    min_interval = 0.25  # seconds between requests (4/sec max)
    start_time = time.time()

    def throttled_query(source_url, bbox, fields):
        """Rate-limited query to avoid overwhelming the server."""
        with rate_lock:
            now = time.time()
            wait = min_interval - (now - last_request_time[0])
            if wait > 0:
                time.sleep(wait)
            last_request_time[0] = time.time()
        return query_features(source_url, bbox, fields)

    def process_tile(xy):
        nonlocal saved, empty, errors
        x, y = xy
        tile_dir = os.path.join(out_dir, str(x))
        tile_path = os.path.join(tile_dir, f'{y}.json')

        bbox = tile_bounds(x, y, ZOOM)
        data = throttled_query(source['url'], bbox, source['fields'])

        with lock:
            if data and data.get('features'):
                geojson = arcgis_to_geojson(data)
                if geojson['features']:
                    os.makedirs(tile_dir, exist_ok=True)
                    with open(tile_path, 'w') as f:
                        json.dump(geojson, f, separators=(',', ':'))
                    saved += 1
                    return 'saved'
                else:
                    empty_set.add(f'{x}/{y}')
                    empty += 1
                    return 'empty'
            elif data is not None:
                # Query succeeded but returned no features
                empty_set.add(f'{x}/{y}')
                empty += 1
                return 'empty'
            else:
                errors += 1
                return 'error'

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = {pool.submit(process_tile, xy): xy for xy in todo}
        for future in concurrent.futures.as_completed(futures):
            done += 1
            if done % 25 == 0 or done == len(todo):
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(todo) - done) / rate if rate > 0 else 0
                print(f"  [{done}/{len(todo)}] saved={saved} empty={empty} err={errors}"
                      f"  ({rate:.1f}/s, ETA {int(eta)}s)")

    # Save empty manifest for future resume
    save_empty_set(source_dir, empty_set)

    print(f"  Done: {saved} saved, {empty} empty, {errors} errors, {skipped} skipped")
    return saved


def main():
    parser = argparse.ArgumentParser(description='Generate static data tiles')
    parser.add_argument('--source', choices=list(SOURCES.keys()),
                        help='Generate tiles for a specific source only')
    parser.add_argument('--workers', type=int, default=4,
                        help='Number of parallel workers (default: 4)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show tile count without fetching')
    parser.add_argument('--force', action='store_true',
                        help='Re-download all tiles (ignore existing)')
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
        total_saved += generate_source(name, source, num_workers=args.workers, force=args.force)

    print(f"\nAll done! {total_saved} tiles generated in {OUTPUT_DIR}/")


if __name__ == '__main__':
    main()
