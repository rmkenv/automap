"""
agol_search.py — ArcGIS Online layer discovery
Searches AGOL for matching layers and extracts usable service URLs.
"""

import requests
from typing import Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut


# ─── Known-good authoritative layers ─────────────────────────────────────────
# Fallback catalog of well-known national datasets that AGOL search often misses.
# Keyed by normalized layer concept → list of (title, service_url) tuples.
# Layer index is appended at query time.

KNOWN_LAYERS = {
    "census tracts": [
        ("Census Tracts 2020 (TIGER)", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Census_Tract/FeatureServer/0"),
        ("Census Tracts (Living Atlas)", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Census_Tract_2020/FeatureServer/0"),
    ],
    "flood zones": [
        ("FEMA National Flood Hazard Layer", "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28"),
        ("FEMA Flood Zones", "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28"),
    ],
    "fema flood zones": [
        ("FEMA National Flood Hazard Layer", "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28"),
    ],
    "fema flood": [
        ("FEMA National Flood Hazard Layer", "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28"),
    ],
    "hospitals": [
        ("USA Hospitals (Living Atlas)", "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Hospitals_1/FeatureServer/0"),
    ],
    "schools": [
        ("USA Schools (NCES)", "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Public_Schools_in_United_States/FeatureServer/0"),
    ],
    "parks": [
        ("USA Parks", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Parks/FeatureServer/0"),
    ],
    "superfund sites": [
        ("EPA Superfund Sites", "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/Superfund_NPL_Sites/FeatureServer/0"),
    ],
    "wetlands": [
        ("NWI Wetlands (FWS)", "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/1"),
    ],
    "counties": [
        ("USA Counties (TIGER)", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Counties_Generalized_Boundaries/FeatureServer/0"),
    ],
    "zip codes": [
        ("USA ZIP Code Areas", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_ZIP_Code_Boundaries/FeatureServer/0"),
    ],
    "congressional districts": [
        ("Congressional Districts 118th", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Congressional_Districts/FeatureServer/0"),
    ],
    "transit stops": [
        ("USA Transit Stops", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Transit_Stops/FeatureServer/0"),
    ],
    "bike lanes": [
        ("OpenStreetMap Bike Paths", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Bike_Lane_Areas/FeatureServer/0"),
    ],
    "power plants": [
        ("EIA Power Plants", "https://services7.arcgis.com/FGr1D95XCGALKXqM/arcgis/rest/services/Power_Plants_in_United_States/FeatureServer/0"),
    ],
    "low income": [
        ("HUD Low Income Areas", "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Low_to_Moderate_Income_Population_by_Tract/FeatureServer/0"),
    ],
    "farmland": [
        ("USA Cropland (USDA)", "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_cropland/FeatureServer/0"),
    ],
}


def _normalize_layer_key(layer_name: str) -> Optional[str]:
    """Fuzzy match a layer name to a known catalog key."""
    name = layer_name.lower().strip()
    # Exact match
    if name in KNOWN_LAYERS:
        return name
    # Substring match
    for key in KNOWN_LAYERS:
        if key in name or name in key:
            return key
    # Word overlap
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


# ─── AGOL Search ─────────────────────────────────────────────────────────────

AGOL_SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"

AUTHORITATIVE_OWNERS = [
    "fema", "census", "epa", "usgs", "noaa", "hud",
    "esri", "esri_dm", "esri_livingatlas",
]


def search_agol_layers(
    query: str,
    geography: str = "",
    item_type: str = "Feature Service",
    max_results: int = 5,
) -> list[dict]:
    """
    Search ArcGIS Online for layers matching a query.
    Does NOT append geography to the query — national datasets aren't tagged by city.
    """
    # Fix: type filter goes in q, not as a separate filter param
    full_query = f'{query} type:"{item_type}"'

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
        service_url = _resolve_service_url(item)
        if not service_url:
            continue

        owner = item.get("owner", "")
        auth_score = sum(1 for a in AUTHORITATIVE_OWNERS if a in owner.lower())
        views = item.get("numViews", 0)

        results.append({
            "title": item.get("title", "Untitled"),
            "url": service_url,
            "item_url": f"https://www.arcgis.com/home/item.html?id={item.get('id', '')}",
            "owner": owner,
            "snippet": item.get("snippet", ""),
            "views": views,
            "auth_score": auth_score,
            "type": item.get("type", ""),
            "id": item.get("id", ""),
            "source": "agol_search",
        })

    results.sort(key=lambda x: (x["auth_score"], x["views"]), reverse=True)
    return results[:max_results]


def get_known_layer_candidates(layer_name: str) -> list[dict]:
    """Return hardcoded known-good candidates for a layer concept."""
    key = _normalize_layer_key(layer_name)
    if not key:
        return []
    return [
        {
            "title": title,
            "url": url,
            "item_url": "",
            "owner": "authoritative",
            "snippet": f"Known-good {key} layer",
            "views": 9999999,
            "auth_score": 10,
            "source": "known_catalog",
        }
        for title, url in KNOWN_LAYERS[key]
    ]


def _resolve_service_url(item: dict) -> Optional[str]:
    """Extract the best usable service URL from an AGOL item."""
    url = item.get("url", "")
    if not url:
        return None
    if "FeatureServer" in url or "MapServer" in url:
        url = url.rstrip("/")
        if not url.split("/")[-1].isdigit():
            url = f"{url}/0"
    return url


# ─── Layer Metadata ───────────────────────────────────────────────────────────

def get_layer_info(service_url: str) -> Optional[dict]:
    """Fetch metadata for a feature/map service layer."""
    try:
        resp = requests.get(service_url, params={"f": "json"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "geometry_type": data.get("geometryType", "unknown"),
            "fields": [f["name"] for f in data.get("fields", [])],
            "name": data.get("name", ""),
            "description": data.get("description", ""),
        }
    except Exception:
        return None


def fetch_geojson(service_url: str, bbox: Optional[list] = None) -> Optional[dict]:
    """
    Query a Feature/Map Service layer and return GeoJSON.
    Falls back to no spatial filter if bbox returns empty.
    """
    base_params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "resultRecordCount": 500,
    }

    query_url = service_url.rstrip("/") + "/query"

    # First try: with bbox spatial filter
    if bbox:
        minx, miny, maxx, maxy = bbox
        spatial_params = {
            **base_params,
            "geometry": f"{minx},{miny},{maxx},{maxy}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        try:
            resp = requests.get(query_url, params=spatial_params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if "error" not in data and data.get("features"):
                return data
        except Exception:
            pass

    # Second try: no spatial filter (let the app render everything, user can pan)
    try:
        resp = requests.get(query_url, params=base_params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if "error" not in data and data.get("features"):
            return data
    except Exception:
        pass

    return None
