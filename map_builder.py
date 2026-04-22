"""
map_builder.py — Folium map assembly
Constructs an interactive map from resolved layers and geocoded extent.
"""

import folium
import random
from folium.plugins import Fullscreen, MiniMap
from typing import Optional


# ─── Basemap Configs ──────────────────────────────────────────────────────────

BASEMAPS = {
    "streets": {
        "tiles": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
        "name": "Streets",
    },
    "satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "© Esri, Maxar, GeoEye, Earthstar Geographics",
        "name": "Satellite",
    },
    "topo": {
        "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors, SRTM | © OpenTopoMap",
        "name": "Topographic",
    },
    "light": {
        "tiles": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
        "name": "Light",
    },
    "dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
        "name": "Dark",
    },
}

# Distinct colors for multiple layers
LAYER_COLORS = [
    "#E63946",  # red
    "#2196F3",  # blue
    "#4CAF50",  # green
    "#FF9800",  # orange
    "#9C27B0",  # purple
    "#00BCD4",  # cyan
]


def get_layer_style(color: str, geometry_type: str) -> dict:
    """Return a Folium GeoJSON style function config."""
    if "point" in geometry_type.lower() or "esriGeometryPoint" in geometry_type:
        return {
            "fillColor": color,
            "color": "#ffffff",
            "weight": 1,
            "fillOpacity": 0.8,
            "radius": 6,
        }
    else:
        return {
            "fillColor": color,
            "color": color,
            "weight": 1.5,
            "fillOpacity": 0.25,
            "opacity": 0.85,
        }


def build_map(
    geo_info: dict,
    layers: list[dict],  # [{"title", "geojson", "geometry_type", "color"}]
    basemap_key: str = "light",
    zoom_start: int = 10,
) -> folium.Map:
    """
    Build a Folium map centered on the geocoded location with all resolved layers.

    Args:
        geo_info: Output from geocode_place() — has lat, lon, bbox, display_name
        layers: List of resolved layer dicts with GeoJSON data
        basemap_key: One of BASEMAPS keys
        zoom_start: Initial zoom level

    Returns:
        folium.Map instance
    """
    lat, lon = geo_info["lat"], geo_info["lon"]
    basemap = BASEMAPS.get(basemap_key, BASEMAPS["light"])

    # Build base map
    m = folium.Map(
        location=[lat, lon],
        zoom_start=zoom_start,
        tiles=basemap["tiles"],
        attr=basemap["attr"],
        prefer_canvas=True,
    )

    # Plugins
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)

    # Add each layer
    for i, layer in enumerate(layers):
        if not layer.get("geojson"):
            continue

        color = layer.get("color", LAYER_COLORS[i % len(LAYER_COLORS)])
        geo_type = layer.get("geometry_type", "")
        style = get_layer_style(color, geo_type)
        title = layer.get("title", f"Layer {i+1}")

        # Use CircleMarker for points, regular GeoJson for polys/lines
        if "point" in geo_type.lower():
            folium.GeoJson(
                layer["geojson"],
                name=title,
                tooltip=folium.GeoJsonTooltip(
                    fields=_safe_tooltip_fields(layer["geojson"]),
                    sticky=False,
                ),
                marker=folium.CircleMarker(
                    radius=style["radius"],
                    fill_color=style["fillColor"],
                    color=style["color"],
                    fill_opacity=style["fillOpacity"],
                    weight=style["weight"],
                ),
            ).add_to(m)
        else:
            folium.GeoJson(
                layer["geojson"],
                name=title,
                style_function=lambda feature, s=style: s,
                tooltip=folium.GeoJsonTooltip(
                    fields=_safe_tooltip_fields(layer["geojson"]),
                    sticky=False,
                ),
            ).add_to(m)

    # Layer control if multiple layers
    if len(layers) > 1:
        folium.LayerControl(collapsed=False).add_to(m)

    # Fit bounds to geography bbox
    bbox = geo_info.get("bbox")
    if bbox:
        minx, miny, maxx, maxy = bbox
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    return m


def _safe_tooltip_fields(geojson: dict) -> list[str]:
    """Extract a safe subset of property fields for tooltips."""
    features = geojson.get("features", [])
    if not features:
        return []

    props = features[0].get("properties", {}) or {}
    # Pick the first 4 non-null string/numeric fields
    fields = []
    for k, v in props.items():
        if v is not None and isinstance(v, (str, int, float)):
            fields.append(k)
        if len(fields) >= 4:
            break
    return fields


def map_to_html(m: folium.Map) -> str:
    """Render a Folium map to an HTML string."""
    return m._repr_html_()


def assign_layer_colors(layer_titles: list[str]) -> dict[str, str]:
    """Assign a distinct color to each layer title."""
    return {
        title: LAYER_COLORS[i % len(LAYER_COLORS)]
        for i, title in enumerate(layer_titles)
    }
