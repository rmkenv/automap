"""
map_builder.py — Folium map assembly with per-layer style control
"""

import folium
import colorsys
from folium.plugins import Fullscreen, MiniMap
from typing import Optional

# ─── Basemaps ─────────────────────────────────────────────────────────────────

BASEMAPS = {
    "streets": {
        "tiles": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
    },
    "satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "© Esri, Maxar, GeoEye, Earthstar Geographics",
    },
    "topo": {
        "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors, SRTM | © OpenTopoMap",
    },
    "light": {
        "tiles": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
    },
    "dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attr": "© OpenStreetMap contributors © CARTO",
    },
}

LAYER_COLORS = ["#E63946", "#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"]

# ─── Color ramp palettes ──────────────────────────────────────────────────────

COLOR_RAMPS = {
    "Blues":    ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
    "Reds":     ["#fff5f0", "#fcbba1", "#fb6a4a", "#cb181d", "#67000d"],
    "Greens":   ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"],
    "Oranges":  ["#fff5eb", "#fdd0a2", "#fd8d3c", "#d94801", "#7f2704"],
    "Purples":  ["#fcfbfd", "#dadaeb", "#9e9ac8", "#6a51a3", "#3f007d"],
    "YlOrRd":   ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
    "RdYlGn":   ["#d73027", "#fc8d59", "#ffffbf", "#91cf60", "#1a9850"],
    "Viridis":  ["#440154", "#3b528b", "#21908c", "#5dc963", "#fde725"],
    "Grays":    ["#f7f7f7", "#cccccc", "#969696", "#636363", "#252525"],
}

# ─── Categorical color maps for known fields ──────────────────────────────────

FLOOD_ZONE_COLORS = {
    "AE":                              "#00a8e8",   # blue  — 1% annual chance w/ BFE
    "A":                               "#5bc8f5",   # light blue — 1% annual chance
    "AO":                              "#0077b6",   # dark blue — shallow flooding
    "AH":                              "#48cae4",
    "VE":                              "#023e8a",   # coastal high-hazard
    "V":                               "#0096c7",
    "0.2 PCT ANNUAL CHANCE FLOOD HAZARD": "#f4d03f", # yellow — 0.2%
    "X PROTECTED BY LEVEE":            "#a9cce3",   # light — levee-protected
    "X":                               "#d5e8d4",   # outside 500yr
    "D":                               "#e8d5b7",   # undetermined
}

SFHA_COLORS = {"T": "#e63946", "F": "#a8dadc"}   # Special Flood Hazard Area T/F


def _unique_value_style_fn(geojson: dict, field: str, color_map: dict, default_color: str, opacity: float, stroke_color: str, stroke_weight: float):
    """Style function that colors features by a categorical field value."""
    def _style(feature):
        val = str((feature.get("properties") or {}).get(field, "")).strip()
        fill = color_map.get(val, default_color)
        return {
            "fillColor": fill,
            "color": stroke_color,
            "weight": stroke_weight,
            "fillOpacity": opacity,
            "opacity": 1.0,
        }
    return _style



# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_numeric_fields(geojson: dict) -> list[str]:
    """Return all numeric property fields from a GeoJSON layer."""
    features = geojson.get("features", [])
    if not features:
        return []
    props = features[0].get("properties", {}) or {}
    return [k for k, v in props.items() if isinstance(v, (int, float)) and v is not None]


def get_all_fields(geojson: dict) -> list[str]:
    """Return all property fields from a GeoJSON layer."""
    features = geojson.get("features", [])
    if not features:
        return []
    props = features[0].get("properties", {}) or {}
    return list(props.keys())


def _safe_tooltip_fields(geojson: dict, max_fields: int = 5) -> list[str]:
    features = geojson.get("features", [])
    if not features:
        return []
    props = features[0].get("properties", {}) or {}
    fields = []
    for k, v in props.items():
        if v is not None and isinstance(v, (str, int, float)):
            fields.append(k)
        if len(fields) >= max_fields:
            break
    return fields


def _interpolate_color(t: float, ramp: list[str]) -> str:
    """Interpolate a hex color along a ramp (t in [0,1])."""
    if len(ramp) == 1:
        return ramp[0]
    n = len(ramp) - 1
    i = min(int(t * n), n - 1)
    lo, hi = ramp[i], ramp[i + 1]
    lt = (t * n) - i

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[j:j+2], 16) / 255 for j in (0, 2, 4))

    def rgb_to_hex(r, g, b):
        return "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))

    r0, g0, b0 = hex_to_rgb(lo)
    r1, g1, b1 = hex_to_rgb(hi)
    return rgb_to_hex(r0 + (r1-r0)*lt, g0 + (g1-g0)*lt, b0 + (b1-b0)*lt)


def _choropleth_style_fn(geojson: dict, field: str, ramp: list[str], opacity: float, stroke_color: str, stroke_weight: float):
    """Return a Folium style_function that colors features by a numeric field."""
    values = []
    for f in geojson.get("features", []):
        v = (f.get("properties") or {}).get(field)
        if isinstance(v, (int, float)):
            values.append(v)

    if not values:
        def _flat(feature):
            return {"fillColor": ramp[-1], "color": stroke_color, "weight": stroke_weight, "fillOpacity": opacity}
        return _flat

    vmin, vmax = min(values), max(values)
    span = vmax - vmin or 1

    def _style(feature):
        v = (feature.get("properties") or {}).get(field, vmin)
        t = (v - vmin) / span if isinstance(v, (int, float)) else 0
        return {
            "fillColor": _interpolate_color(t, ramp),
            "color": stroke_color,
            "weight": stroke_weight,
            "fillOpacity": opacity,
            "opacity": 1.0,
        }
    return _style


# ─── Default style config ─────────────────────────────────────────────────────

def default_style_config(layer: dict, index: int) -> dict:
    """Return a default style config dict for a layer."""
    geo_type = layer.get("geometry_type", "").lower()
    is_point = "point" in geo_type
    geojson = layer.get("geojson", {})
    fields = get_all_fields(geojson)

    # Auto-detect flood zone layer → default to unique-value mode on FLD_ZONE
    mode = "flat"
    uv_field = None
    if "FLD_ZONE" in fields:
        mode = "unique_value"
        uv_field = "FLD_ZONE"

    return {
        "mode": mode,
        "color": LAYER_COLORS[index % len(LAYER_COLORS)],
        "opacity": 0.5 if not is_point else 0.8,
        "stroke_color": "#555555",
        "stroke_weight": 0.5,
        "point_radius": 6,
        "choropleth_field": None,
        "choropleth_ramp": "Blues",
        "unique_value_field": uv_field,
    }


# ─── Map builder ─────────────────────────────────────────────────────────────

def build_map(
    geo_info: dict,
    layers: list[dict],
    basemap_key: str = "light",
    zoom_start: int = 10,
    style_configs: Optional[dict] = None,   # {layer_title: style_config_dict}
) -> folium.Map:
    """
    Build a Folium map. style_configs overrides per-layer defaults.
    """
    lat, lon = geo_info["lat"], geo_info["lon"]
    basemap = BASEMAPS.get(basemap_key, BASEMAPS["light"])

    m = folium.Map(
        location=[lat, lon],
        zoom_start=zoom_start,
        tiles=basemap["tiles"],
        attr=basemap["attr"],
        prefer_canvas=True,
    )

    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)

    for i, layer in enumerate(layers):
        if not layer.get("geojson"):
            continue

        title = layer.get("title", f"Layer {i+1}")
        geo_type = layer.get("geometry_type", "").lower()
        is_point = "point" in geo_type
        geojson = layer["geojson"]
        tooltip_fields = _safe_tooltip_fields(geojson)

        # Merge default + user overrides
        cfg = default_style_config(layer, i)
        if style_configs and title in style_configs:
            cfg.update(style_configs[title])

        if cfg["mode"] == "unique_value" and cfg.get("unique_value_field"):
            uv_field = cfg["unique_value_field"]
            # Use FLOOD_ZONE_COLORS if field is FLD_ZONE, else build from LAYER_COLORS
            if uv_field == "FLD_ZONE":
                cmap = FLOOD_ZONE_COLORS
            else:
                vals = list({(f.get("properties") or {}).get(uv_field, "") for f in geojson.get("features", [])})
                cmap = {v: LAYER_COLORS[i % len(LAYER_COLORS)] for i, v in enumerate(vals)}
            style_fn = _unique_value_style_fn(
                geojson, uv_field, cmap,
                cfg.get("color", "#aaaaaa"),
                cfg["opacity"], cfg["stroke_color"], cfg["stroke_weight"],
            )
            folium.GeoJson(
                geojson,
                name=title,
                style_function=style_fn,
                tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, sticky=False) if tooltip_fields else None,
            ).add_to(m)

        elif cfg["mode"] == "choropleth" and cfg.get("choropleth_field"):
            ramp = COLOR_RAMPS.get(cfg["choropleth_ramp"], COLOR_RAMPS["Blues"])
            style_fn = _choropleth_style_fn(
                geojson,
                cfg["choropleth_field"],
                ramp,
                cfg["opacity"],
                cfg["stroke_color"],
                cfg["stroke_weight"],
            )
            folium.GeoJson(
                geojson,
                name=title,
                style_function=style_fn,
                tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, sticky=False) if tooltip_fields else None,
            ).add_to(m)

        elif is_point:
            folium.GeoJson(
                geojson,
                name=title,
                tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, sticky=False) if tooltip_fields else None,
                marker=folium.CircleMarker(
                    radius=cfg["point_radius"],
                    fill_color=cfg["color"],
                    color=cfg["stroke_color"],
                    fill_opacity=cfg["opacity"],
                    weight=cfg["stroke_weight"],
                ),
            ).add_to(m)

        else:
            flat_style = {
                "fillColor": cfg["color"],
                "color": cfg["stroke_color"],
                "weight": cfg["stroke_weight"],
                "fillOpacity": cfg["opacity"],
                "opacity": 1.0,
            }
            folium.GeoJson(
                geojson,
                name=title,
                style_function=lambda feature, s=flat_style: s,
                tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, sticky=False) if tooltip_fields else None,
            ).add_to(m)

    if len(layers) > 1:
        folium.LayerControl(collapsed=False).add_to(m)

    bbox = geo_info.get("bbox")
    if bbox:
        minx, miny, maxx, maxy = bbox
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    return m


def map_to_html(m: folium.Map) -> str:
    return m._repr_html_()


def assign_layer_colors(layer_titles: list[str]) -> dict[str, str]:
    return {t: LAYER_COLORS[i % len(LAYER_COLORS)] for i, t in enumerate(layer_titles)}
