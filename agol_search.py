"""
agol_search.py — ArcGIS Online layer discovery
Searches AGOL for matching layers and extracts usable service URLs.
"""

import requests
from typing import Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut


# ─── Geocoding ───────────────────────────────────────────────────────────────

def geocode_place(place_name: str) -> Optional[dict]:
    """
    Geocode a place name to a bounding box using Nominatim (free, no key).

    Returns:
        dict with keys: bbox (list [minx, miny, maxx, maxy]), lat, lon, display_name
        or None if not found.
    """
    geolocator = Nominatim(user_agent="map_genie_app/1.0")
    try:
        location = geolocator.geocode(place_name, timeout=10)
        if not location:
            return None

        raw = location.raw
        bbox = raw.get("boundingbox")  # [south, north, west, east]
        if bbox:
            # Reorder to [minx, miny, maxx, maxy] (west, south, east, north)
            minx = float(bbox[2])
            miny = float(bbox[0])
            maxx = float(bbox[3])
            maxy = float(bbox[1])
        else:
            # Fall back to a ~0.5 degree buffer around centroid
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

# Prefer these orgs/owners for authoritative data
AUTHORITATIVE_OWNERS = [
    "FEMA", "Census", "EPA", "USGS", "NOAA", "HUD",
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

    Args:
        query: Search terms (e.g. "FEMA flood zones")
        geography: Optional place name to append to query
        item_type: AGOL item type filter
        max_results: Max results to return

    Returns:
        List of dicts: {title, url, item_url, owner, snippet, score, type}
    """
    full_query = query
    if geography:
        full_query = f"{query} {geography}"

    params = {
        "q": full_query,
        "f": "json",
        "num": max(max_results * 3, 15),  # Fetch extra for ranking/filtering
        "sortField": "numViews",
        "sortOrder": "desc",
        "filter": f"type:{item_type}",
    }

    try:
        resp = requests.get(AGOL_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return []

    results = []
    for item in data.get("results", []):
        url = item.get("url", "")
        if not url:
            continue

        # Resolve service URL
        service_url = _resolve_service_url(item)
        if not service_url:
            continue

        # Score for authoritativeness
        owner = item.get("owner", "")
        auth_score = sum(1 for a in AUTHORITATIVE_OWNERS if a.lower() in owner.lower())
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
            "tags": item.get("tags", []),
        })

    # Rank: authoritative first, then by views
    results.sort(key=lambda x: (x["auth_score"], x["views"]), reverse=True)
    return results[:max_results]


def _resolve_service_url(item: dict) -> Optional[str]:
    """Extract the best usable service URL from an AGOL item."""
    url = item.get("url", "")
    if not url:
        return None

    # Feature Services: use layer 0 if no layer specified
    if "FeatureServer" in url:
        # Strip trailing slash and add /0 if needed
        url = url.rstrip("/")
        if not url.split("/")[-1].isdigit():
            url = f"{url}/0"
        return url

    # Map Services
    if "MapServer" in url:
        url = url.rstrip("/")
        if not url.split("/")[-1].isdigit():
            url = f"{url}/0"
        return url

    return url


# ─── Layer Metadata ───────────────────────────────────────────────────────────

def get_layer_info(service_url: str) -> Optional[dict]:
    """
    Fetch metadata for a feature service layer (fields, geometry type, extent).

    Returns:
        dict with fields, geometryType, extent or None on failure
    """
    try:
        resp = requests.get(
            service_url,
            params={"f": "json"},
            timeout=10,
        )
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
    Query a Feature Service layer and return GeoJSON.

    Args:
        service_url: Feature Service URL (ending in /0 or similar)
        bbox: Optional [minx, miny, maxx, maxy] to spatially filter

    Returns:
        GeoJSON FeatureCollection or None on failure
    """
    params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "resultRecordCount": 500,
    }

    if bbox:
        minx, miny, maxx, maxy = bbox
        params["geometry"] = f"{minx},{miny},{maxx},{maxy}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"

    query_url = service_url.rstrip("/") + "/query"

    try:
        resp = requests.get(query_url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return None

        features = data.get("features", [])
        if not features:
            return None

        return data

    except Exception:
        return None
