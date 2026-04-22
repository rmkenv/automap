"""
agol_search.py — ArcGIS Online / REST layer discovery
Searches for layers and extracts usable service URLs.
"""

import requests
import json
from typing import Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut


# ─── Coordinate transformation ───────────────────────────────────────────────

def _transform_bbox(bbox: list, target_wkid: int) -> tuple[str, str]:
    """
    Transform a WGS84 bbox to the target WKID.
    Returns (geometry_string, inSR_string) ready for the ESRI REST query.
    """
    minx, miny, maxx, maxy = bbox

    if target_wkid in (4326, 4269):
        # Already WGS84 / NAD83 — pass through
        return f"{minx},{miny},{maxx},{maxy}", "4326"

    try:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:4326", f"EPSG:{target_wkid}", always_xy=True)
        x1, y1 = t.transform(minx, miny)
        x2, y2 = t.transform(maxx, maxy)
        return f"{x1},{y1},{x2},{y2}", str(target_wkid)
    except Exception:
        # pyproj failed — fall back to sending WGS84 with inSR=4326
        # and let the server reproject (works for most ESRI services)
        return f"{minx},{miny},{maxx},{maxy}", "4326"


def _get_service_wkid(query_url: str) -> int:
    """Fetch the service's native spatial reference WKID."""
    base_url = query_url.replace("/query", "")
    try:
        resp = requests.get(base_url, params={"f": "json"}, timeout=10)
        data = resp.json()
        sr = data.get("spatialReference", {})
        return int(sr.get("latestWkid") or sr.get("wkid") or 4326)
    except Exception:
        return 4326


# ─── Known-good authoritative layer catalog ───────────────────────────────────
# Each entry: (title, query_url, spatial_reference)
# spatial_reference: "4326" or "102100" (Web Mercator)
# query_url: the /query endpoint directly

KNOWN_LAYERS = {
    "census tracts": [
        # Census TIGERweb — official Census Bureau REST service, always up
        (
            "Census Tracts 2020 (TIGERweb)",
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/0/query",
            "102100",
        ),
    ],
    "flood zones": [
        (
            "FEMA NFHL Flood Hazard Areas",
            "https://hazards.fema.gov/arcgis/services/public/NFHL/MapServer/WFSServer",
            "wfs:NFHL:S_Fld_Haz_Ar",
        ),
    ],
    "fema flood zones": [
        (
            "FEMA NFHL Flood Hazard Areas",
            "https://hazards.fema.gov/arcgis/services/public/NFHL/MapServer/WFSServer",
            "wfs:NFHL:S_Fld_Haz_Ar",
        ),
    ],
    "fema flood": [
        (
            "FEMA NFHL Flood Hazard Areas",
            "https://hazards.fema.gov/arcgis/services/public/NFHL/MapServer/WFSServer",
            "wfs:NFHL:S_Fld_Haz_Ar",
        ),
    ],
    "hospitals": [
        (
            "USA Hospitals",
            "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Hospitals_1/FeatureServer/0/query",
            "4326",
        ),
    ],
    "schools": [
        (
            "USA Public Schools",
            "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Public_Schools_in_United_States/FeatureServer/0/query",
            "4326",
        ),
    ],
    "parks": [
        (
            "USA Parks",
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Parks/FeatureServer/0/query",
            "4326",
        ),
    ],
    "superfund sites": [
        (
            "EPA Superfund NPL Sites",
            "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Superfund_NPL_Sites/FeatureServer/0/query",
            "4326",
        ),
    ],
    "wetlands": [
        (
            "National Wetlands Inventory (FWS)",
            "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/1/query",
            "102100",
        ),
    ],
    "counties": [
        (
            "USA Counties (TIGERweb)",
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query",
            "102100",
        ),
    ],
    "zip codes": [
        (
            "USA ZIP Code Areas (TIGERweb)",
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/2/query",
            "102100",
        ),
    ],
    "transit stops": [
        (
            "USA Transit Stops",
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Transit_Stops/FeatureServer/0/query",
            "4326",
        ),
    ],
    "power plants": [
        (
            "EIA Power Plants",
            "https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services/Power_Plants_in_United_States/FeatureServer/0/query",
            "4326",
        ),
    ],
    "low income": [
        (
            "HUD Low-to-Moderate Income Areas",
            "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Low_to_Moderate_Income_Population_by_Tract/FeatureServer/0/query",
            "4326",
        ),
    ],
    "farmland": [
        (
            "USA Cropland (USDA/NASS)",
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_cropland/FeatureServer/0/query",
            "4326",
        ),
    ],
    "block groups": [
        (
            "Census Block Groups (TIGERweb)",
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/1/query",
            "102100",
        ),
    ],
}


def _normalize_layer_key(layer_name: str) -> Optional[str]:
    """Fuzzy match a layer name to a known catalog key."""
    name = layer_name.lower().strip()
    if name in KNOWN_LAYERS:
        return name
    for key in KNOWN_LAYERS:
        if key in name or name in key:
            return key
    name_words = set(name.split())
    best_key, best_score = None, 0
    for key in KNOWN_LAYERS:
        overlap = len(name_words & set(key.split()))
        if overlap > best_score:
            best_score, best_key = overlap, key
    return best_key if best_score >= 1 else None


# ─── Geocoding ────────────────────────────────────────────────────────────────

def geocode_place(place_name: str) -> Optional[dict]:
    """Geocode a place name to a bounding box using Nominatim."""
    geolocator = Nominatim(user_agent="map_genie_app/1.0")
    try:
        location = geolocator.geocode(place_name, timeout=10)
        if not location:
            return None

        raw = location.raw
        bbox = raw.get("boundingbox")  # [south, north, west, east]
        if bbox:
            minx = float(bbox[2])  # west
            miny = float(bbox[0])  # south
            maxx = float(bbox[3])  # east
            maxy = float(bbox[1])  # north
        else:
            lat, lon = location.latitude, location.longitude
            minx, miny, maxx, maxy = lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5

        return {
            "bbox": [minx, miny, maxx, maxy],
            "lat": location.latitude,
            "lon": location.longitude,
            "display_name": location.address,
        }
    except GeocoderTimedOut:
        return None


# ─── AGOL Search (fallback) ───────────────────────────────────────────────────

AGOL_SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"
AUTHORITATIVE_OWNERS = ["fema", "census", "epa", "usgs", "noaa", "hud", "esri", "esri_dm", "esri_livingatlas"]


def search_agol_layers(query: str, max_results: int = 5) -> list[dict]:
    """Search ArcGIS Online for Feature Service layers. Returns list of candidate dicts."""
    full_query = f'{query} type:"Feature Service"'
    params = {
        "q": full_query,
        "f": "json",
        "num": max(max_results * 4, 20),
        "sortField": "numViews",
        "sortOrder": "desc",
    }
    try:
        resp = requests.get(AGOL_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for item in data.get("results", []):
        url = item.get("url", "").rstrip("/")
        if not url:
            continue
        if "FeatureServer" in url or "MapServer" in url:
            if not url.split("/")[-1].isdigit():
                url = f"{url}/0"
        query_url = f"{url}/query"
        owner = item.get("owner", "")
        auth_score = sum(1 for a in AUTHORITATIVE_OWNERS if a in owner.lower())
        results.append({
            "title": item.get("title", "Untitled"),
            "query_url": query_url,
            "item_url": f"https://www.arcgis.com/home/item.html?id={item.get('id', '')}",
            "owner": owner,
            "views": item.get("numViews", 0),
            "auth_score": auth_score,
            "sr": "4326",
            "source": "agol_search",
        })

    results.sort(key=lambda x: (x["auth_score"], x["views"]), reverse=True)
    return results[:max_results]


def get_known_layer_candidates(layer_name: str) -> list[dict]:
    """Return known-good candidates for a layer concept."""
    key = _normalize_layer_key(layer_name)
    if not key:
        return []
    candidates = []
    for title, url, sr_or_typename in KNOWN_LAYERS[key]:
        if sr_or_typename.startswith("wfs:"):
            typename = sr_or_typename[4:]  # strip "wfs:" prefix
            candidates.append({
                "title": title,
                "query_url": url,
                "wfs_typename": typename,
                "item_url": "",
                "owner": "authoritative",
                "views": 9999999,
                "auth_score": 10,
                "sr": "4326",
                "source": "known_catalog",
                "source_type": "wfs",
            })
        else:
            candidates.append({
                "title": title,
                "query_url": url,
                "item_url": "",
                "owner": "authoritative",
                "views": 9999999,
                "auth_score": 10,
                "sr": sr_or_typename,
                "source": "known_catalog",
                "source_type": "esri",
            })
    return candidates


# ─── GeoJSON fetch ────────────────────────────────────────────────────────────

def fetch_geojson(query_url: str, bbox: Optional[list], sr: str = "4326") -> Optional[dict]:
    """
    Query a REST service /query endpoint and return GeoJSON.
    Auto-detects the service's native SR and reprojects the bbox accordingly.

    Pass 1: bbox reprojected to service native SR
    Pass 2: bbox sent as WGS84 with inSR=4326 (server reprojects)
    Pass 3: no spatial filter (full layer)
    """
    base_params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "outSR": "4326",   # Always return WGS84 for Folium
        "resultRecordCount": 500,
    }

    def _try_fetch(params: dict) -> Optional[dict]:
        try:
            resp = requests.get(query_url, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
            if "error" not in data and data.get("features"):
                return data
        except Exception:
            pass
        return None

    if bbox:
        # Pass 1: detect service SR and reproject bbox to it
        service_wkid = _get_service_wkid(query_url)
        geom_str, in_sr = _transform_bbox(bbox, service_wkid)
        result = _try_fetch({
            **base_params,
            "geometry": geom_str,
            "geometryType": "esriGeometryEnvelope",
            "inSR": in_sr,
            "spatialRel": "esriSpatialRelIntersects",
        })
        if result:
            return result

        # Pass 2: send bbox as WGS84, let server reproject
        if in_sr != "4326":
            minx, miny, maxx, maxy = bbox
            result = _try_fetch({
                **base_params,
                "geometry": f"{minx},{miny},{maxx},{maxy}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            })
            if result:
                return result

    # Pass 3: no spatial filter — return full layer
    return _try_fetch(base_params)


def get_layer_info(query_url: str) -> Optional[dict]:
    """Fetch layer metadata from service endpoint (strip /query to get base URL)."""
    base_url = query_url.replace("/query", "")
    try:
        resp = requests.get(base_url, params={"f": "json"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "geometry_type": data.get("geometryType", "esriGeometryPolygon"),
            "fields": [f["name"] for f in data.get("fields", [])],
            "name": data.get("name", ""),
        }
    except Exception:
        return None



# ─── WFS client ───────────────────────────────────────────────────────────────

def fetch_wfs_geojson(wfs_url: str, bbox: Optional[list], typename: str) -> Optional[dict]:
    """
    Query an OGC WFS endpoint and return GeoJSON.
    Tries WFS 2.0.0 then 1.1.0. bbox must be [minx, miny, maxx, maxy] in WGS84.

    Args:
        wfs_url:  Base WFS URL (ending in WFSServer or similar)
        bbox:     [minx, miny, maxx, maxy] WGS84
        typename: WFS layer name e.g. 'NFHL:S_Fld_Haz_Ar'
    """
    import xml.etree.ElementTree as ET

    def _geojson_from_features(features: list) -> Optional[dict]:
        if not features:
            return None
        return {"type": "FeatureCollection", "features": features}

    def _parse_wfs_geojson_response(resp_text: str) -> Optional[dict]:
        """Try to parse WFS response as GeoJSON directly."""
        try:
            data = json.loads(resp_text)
            if data.get("features") is not None:
                return data if data.get("features") else None
        except Exception:
            pass
        return None

    def _try_wfs(version: str) -> Optional[dict]:
        if bbox:
            minx, miny, maxx, maxy = bbox
            # WFS 2.0: bbox param is miny,minx,maxy,maxx with CRS appended
            if version == "2.0.0":
                bbox_str = f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326"
            else:
                bbox_str = f"{minx},{miny},{maxx},{maxy},EPSG:4326"
        else:
            bbox_str = None

        params = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            "typeName": typename,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "count" if version == "2.0.0" else "maxFeatures": 500,
        }
        if bbox_str:
            params["bbox"] = bbox_str

        try:
            resp = requests.get(wfs_url, params=params, timeout=30)
            if resp.status_code != 200:
                return None
            return _parse_wfs_geojson_response(resp.text)
        except Exception:
            return None

    # Try WFS 2.0.0 first, fall back to 1.1.0
    result = _try_wfs("2.0.0")
    if result:
        return result
    result = _try_wfs("1.1.0")
    if result:
        return result

    # Last resort: no bbox
    if bbox:
        params_nofilter = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": typename,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "count": 500,
        }
        try:
            resp = requests.get(wfs_url, params=params_nofilter, timeout=30)
            return _parse_wfs_geojson_response(resp.text)
        except Exception:
            pass

    return None


def get_wfs_typenames(wfs_url: str) -> list[str]:
    """Fetch available layer names from a WFS GetCapabilities response."""
    import xml.etree.ElementTree as ET
    params = {"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"}
    try:
        resp = requests.get(wfs_url, params=params, timeout=15)
        root = ET.fromstring(resp.text)
        ns = {"wfs": "http://www.opengis.net/wfs/2.0"}
        names = [el.text for el in root.findall(".//wfs:Name", ns)]
        if not names:
            # Try without namespace
            names = [el.text for el in root.iter() if el.tag.endswith("Name") and el.text and ":" in el.text]
        return names
    except Exception:
        return []

# ─── User-supplied URL resolver ───────────────────────────────────────────────

def resolve_user_url(raw_url: str) -> Optional[dict]:
    """
    Accept any ArcGIS REST or WFS URL a user pastes and return a normalised candidate dict.
    Handles:
      - WFSServer URLs → WFS source_type, prompts for typename
      - .../FeatureServer[/N][/query]  → ESRI REST
      - .../MapServer[/N][/query]      → ESRI REST
      - URLs with ?f=pjson etc.        → strips params first
    """
    url = raw_url.strip().split("?")[0].rstrip("/")

    # ── WFS endpoint ──────────────────────────────────────────────────────────
    if "WFSServer" in url:
        # Try to get available typenames
        typenames = get_wfs_typenames(url)
        # Default to first typename containing "Fld_Haz" or just first one
        typename = next((t for t in typenames if "Fld_Haz" in t or "flood" in t.lower()), None)
        if not typename and typenames:
            typename = typenames[0]
        if not typename:
            typename = "NFHL:S_Fld_Haz_Ar"  # sensible default for FEMA

        parts = url.split("/")
        title = " / ".join(p for p in parts[-3:] if p)
        return {
            "title": f"WFS: {typename}",
            "query_url": url,
            "wfs_typename": typename,
            "item_url": "",
            "owner": "user-supplied",
            "views": 0,
            "auth_score": 0,
            "sr": "4326",
            "source": "user_url",
            "source_type": "wfs",
            "geometry_type": "esriGeometryPolygon",
            "fields": [],
            "snippet": f"WFS layer {typename} from {url}",
        }

    # ── ESRI REST endpoint ────────────────────────────────────────────────────
    if url.endswith("/query"):
        query_url = url
    else:
        parts = url.split("/")
        last = parts[-1]
        if last.isdigit():
            query_url = url + "/query"
        elif any(svc in url for svc in ("FeatureServer", "MapServer")):
            query_url = url + "/0/query"
        else:
            return None

    info = get_layer_info(query_url)
    if info is None:
        return None

    parts = query_url.replace("/query", "").split("/")
    title = info.get("name") or "/".join(parts[-3:])

    return {
        "title": title or "Custom Layer",
        "query_url": query_url,
        "item_url": "",
        "owner": "user-supplied",
        "views": 0,
        "auth_score": 0,
        "sr": "4326",
        "source": "user_url",
        "source_type": "esri",
        "geometry_type": info.get("geometry_type", "esriGeometryPolygon"),
        "fields": info.get("fields", []),
        "snippet": f"User-supplied layer: {query_url}",
    }
