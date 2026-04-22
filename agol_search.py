"""
agol_search.py — ArcGIS Online / REST layer discovery
Searches for layers and extracts usable service URLs.
"""

import math
import requests
from typing import Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut


# ─── WGS84 ↔ Web Mercator conversion ─────────────────────────────────────────
# Needed because FEMA NFHL and Census TIGERweb use SR 102100 (Web Mercator)

def wgs84_to_webmercator(lon: float, lat: float) -> tuple[float, float]:
    """Convert WGS84 lon/lat to Web Mercator x/y (EPSG:3857)."""
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
    y = y * 20037508.34 / 180
    return x, y


def bbox_wgs84_to_webmercator(bbox: list) -> str:
    """Convert [minx, miny, maxx, maxy] WGS84 bbox to Web Mercator string."""
    minx, miny, maxx, maxy = bbox
    x1, y1 = wgs84_to_webmercator(minx, miny)
    x2, y2 = wgs84_to_webmercator(maxx, maxy)
    return f"{x1},{y1},{x2},{y2}"


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
        # FEMA NFHL — correct arcgis path, layer 28 = Flood Hazard Zones
        (
            "FEMA National Flood Hazard Layer",
            "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
            "102100",
        ),
    ],
    "fema flood zones": [
        (
            "FEMA National Flood Hazard Layer",
            "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
            "102100",
        ),
    ],
    "fema flood": [
        (
            "FEMA National Flood Hazard Layer",
            "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
            "102100",
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
    return [
        {
            "title": title,
            "query_url": query_url,
            "item_url": "",
            "owner": "authoritative",
            "views": 9999999,
            "auth_score": 10,
            "sr": sr,
            "source": "known_catalog",
        }
        for title, query_url, sr in KNOWN_LAYERS[key]
    ]


# ─── GeoJSON fetch ────────────────────────────────────────────────────────────

def fetch_geojson(query_url: str, bbox: Optional[list], sr: str = "4326") -> Optional[dict]:
    """
    Query a REST service /query endpoint and return GeoJSON.
    Handles both WGS84 and Web Mercator services correctly.

    Args:
        query_url: Full /query endpoint URL
        bbox: [minx, miny, maxx, maxy] in WGS84
        sr: spatial reference of the service ("4326" or "102100")
    """
    base_params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "outSR": "4326",          # Always return WGS84 for Folium
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

    # Pass 1: with bbox in correct SR
    if bbox:
        if sr == "102100":
            geom_str = bbox_wgs84_to_webmercator(bbox)
            geom_sr = "102100"
        else:
            minx, miny, maxx, maxy = bbox
            geom_str = f"{minx},{miny},{maxx},{maxy}"
            geom_sr = "4326"

        spatial_params = {
            **base_params,
            "geometry": geom_str,
            "geometryType": "esriGeometryEnvelope",
            "inSR": geom_sr,
            "spatialRel": "esriSpatialRelIntersects",
        }
        result = _try_fetch(spatial_params)
        if result:
            return result

    # Pass 2: no spatial filter (full layer, let Folium clip visually)
    result = _try_fetch(base_params)
    return result


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
