"""
app.py — Map Genie: Natural Language → ArcGIS Online → Folium Map
Powered by Ollama Cloud + ArcGIS REST API + Folium
"""

import os
import streamlit as st
import streamlit.components.v1 as components

from llm import parse_map_intent, refine_layer_query
from agol_search import geocode_place, search_agol_layers, get_known_layer_candidates, get_layer_info, fetch_geojson
from map_builder import build_map, map_to_html, assign_layer_colors, LAYER_COLORS, default_style_config, get_numeric_fields, get_all_fields, COLOR_RAMPS

# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Map Genie",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Styles ───────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500&display=swap');

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
    }
    h1, h2, h3 {
        font-family: 'Space Mono', monospace;
    }
    .main-title {
        font-family: 'Space Mono', monospace;
        font-size: 2.2rem;
        font-weight: 700;
        letter-spacing: -1px;
        line-height: 1.1;
        margin-bottom: 0.2rem;
    }
    .subtitle {
        color: #888;
        font-size: 0.95rem;
        font-family: 'DM Sans', sans-serif;
        margin-bottom: 1.5rem;
    }
    .intent-card {
        background: #f8f9fa;
        border-left: 4px solid #2196F3;
        border-radius: 4px;
        padding: 1rem 1.25rem;
        margin: 1rem 0;
        font-family: 'Space Mono', monospace;
        font-size: 0.82rem;
    }
    .layer-chip {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 500;
        margin: 2px 3px;
        color: white;
    }
    .status-ok { color: #4CAF50; font-weight: 600; }
    .status-warn { color: #FF9800; font-weight: 600; }
    .status-err { color: #E63946; font-weight: 600; }
    .map-container {
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        overflow: hidden;
        margin-top: 1rem;
    }
    .source-link {
        font-size: 0.78rem;
        color: #888;
        text-decoration: none;
    }
    .source-link:hover { color: #2196F3; }
    div[data-testid="stTextArea"] textarea {
        font-family: 'DM Sans', sans-serif;
        font-size: 0.95rem;
        min-height: 100px;
    }
    .stButton > button {
        font-family: 'Space Mono', monospace;
        font-weight: 700;
        letter-spacing: 0.5px;
        border-radius: 4px;
    }
</style>
""", unsafe_allow_html=True)


# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Settings")

    ollama_key = st.text_input(
        "Ollama API Key",
        value=os.environ.get("OLLAMA_API_KEY", ""),
        type="password",
        help="Get your key at ollama.com/settings/keys",
    )
    if ollama_key:
        os.environ["OLLAMA_API_KEY"] = ollama_key

    model_choice = st.selectbox(
        "Model",
        options=["gpt-oss:120b", "gpt-oss:70b", "llama3.3:70b"],
        index=0,
        help="Ollama Cloud models — gpt-oss:120b recommended for best layer matching",
    )

    max_results = st.slider(
        "Max AGOL results per layer",
        min_value=1, max_value=8, value=3,
        help="More results = slower but better layer candidates",
    )

    zoom_level = st.slider("Default zoom", min_value=7, max_value=14, value=10)

    st.divider()

    st.markdown("### 📋 Example Prompts")
    examples = [
        "Show me FEMA flood zones and census tracts in Baltimore, MD",
        "Map hospitals and parks in Washington DC with a satellite basemap",
        "Show superfund sites and low income areas in Detroit, MI",
        "Give me wetlands and farmland in the Chesapeake Bay watershed",
        "Show bike infrastructure and transit stops in Philadelphia, PA",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex[:20]}", use_container_width=True):
            st.session_state["prompt_input"] = ex
            st.rerun()

    st.divider()
    st.caption("Data sourced from ArcGIS Online public layers.")
    st.caption("LLM: Ollama Cloud · Map: Folium · Geo: Nominatim")


# ─── Main UI ─────────────────────────────────────────────────────────────────

st.markdown('<div class="main-title">🗺️ Map Genie</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Describe a map in plain English → get an interactive map with live ArcGIS data</div>',
    unsafe_allow_html=True,
)

if "prompt_input" not in st.session_state:
    st.session_state["prompt_input"] = ""

prompt = st.text_area(
    "What map do you want to build?",
    placeholder="e.g. Show me FEMA flood zones and census tracts in Baltimore, MD with a light basemap",
    key="prompt_input",
    height=80,
)

col1, col2 = st.columns([1, 5])
with col1:
    run_btn = st.button("✨ Build Map", type="primary", use_container_width=True)
with col2:
    if not os.environ.get("OLLAMA_API_KEY"):
        st.warning("Set your Ollama API Key in the sidebar to get started.")



# ─── Map render + style panel ─────────────────────────────────────────────────

def _render_map_and_controls(map_html: str, zoom_level: int):
    """Render map HTML and the layer styling panel below it."""

    st.markdown("### 🗺 Your Map")
    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    components.html(map_html, height=580, scrolling=False)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Layer Sources summary ─────────────────────────────────────────────────
    layer_sources = st.session_state.get("layer_sources", [])
    color_map = st.session_state.get("color_map", {})
    st.markdown("### 📋 Layer Sources")
    for src in layer_sources:
        color = color_map.get(src["layer"], "#888")
        chip = f'<span class="layer-chip" style="background:{color}">{src["layer"]}</span>'
        if src["found"]:
            st.markdown(
                f'{chip} &nbsp; <span class="status-ok">✓</span> '
                f'**{src["title"]}** &nbsp;·&nbsp; '
                f'<span style="color:#888">{src["owner"]}</span> &nbsp;·&nbsp; '
                f'{src["feat_count"]} features &nbsp; '
                f'<a class="source-link" href="{src["item_url"]}" target="_blank">View on AGOL ↗</a>',
                unsafe_allow_html=True,
            )
        else:
            title_hint = f" (closest: {src['title']})" if src.get("title") else ""
            st.markdown(
                f'{chip} &nbsp; <span class="status-warn">⚠</span> '
                f'No data found in this area{title_hint}',
                unsafe_allow_html=True,
            )

    # ── Style panel ───────────────────────────────────────────────────────────
    resolved_layers = st.session_state.get("resolved_layers", [])
    if not resolved_layers:
        return

    st.markdown("### 🎨 Style Layers")
    style_configs = st.session_state.get("style_configs", {})
    updated_configs = {}

    for i, layer in enumerate(resolved_layers):
        title = layer["title"]
        geo_type = layer.get("geometry_type", "").lower()
        is_point = "point" in geo_type
        geojson = layer["geojson"]
        numeric_fields = get_numeric_fields(geojson)
        cfg = style_configs.get(title, default_style_config(layer, i))

        with st.expander(f"**{title}**", expanded=(i == 0)):
            mode_options = ["Flat Color", "Choropleth (color by field)"]
            mode_idx = 1 if cfg.get("mode") == "choropleth" and numeric_fields else 0
            if not is_point and numeric_fields:
                mode_label = st.radio(
                    "Style mode",
                    mode_options,
                    index=mode_idx,
                    key=f"mode_{title}",
                    horizontal=True,
                )
                new_mode = "choropleth" if mode_label == "Choropleth (color by field)" else "flat"
            else:
                new_mode = "flat"

            col1, col2 = st.columns(2)

            if new_mode == "flat" or is_point:
                with col1:
                    new_color = st.color_picker(
                        "Fill color" if not is_point else "Point color",
                        value=cfg.get("color", LAYER_COLORS[i % len(LAYER_COLORS)]),
                        key=f"color_{title}",
                    )
                with col2:
                    new_opacity = st.slider(
                        "Opacity", 0.0, 1.0,
                        value=float(cfg.get("opacity", 0.5)),
                        step=0.05,
                        key=f"opacity_{title}",
                    )

                col3, col4 = st.columns(2)
                with col3:
                    new_stroke_color = st.color_picker(
                        "Stroke color",
                        value=cfg.get("stroke_color", "#333333"),
                        key=f"stroke_{title}",
                    )
                with col4:
                    if is_point:
                        new_radius = st.slider(
                            "Point radius", 2, 20,
                            value=int(cfg.get("point_radius", 6)),
                            key=f"radius_{title}",
                        )
                        new_weight = cfg.get("stroke_weight", 1.0)
                    else:
                        new_weight = st.slider(
                            "Stroke weight", 0.0, 5.0,
                            value=float(cfg.get("stroke_weight", 1.0)),
                            step=0.5,
                            key=f"weight_{title}",
                        )
                        new_radius = cfg.get("point_radius", 6)

                updated_configs[title] = {
                    "mode": "flat",
                    "color": new_color,
                    "opacity": new_opacity,
                    "stroke_color": new_stroke_color,
                    "stroke_weight": new_weight,
                    "point_radius": new_radius,
                    "choropleth_field": cfg.get("choropleth_field"),
                    "choropleth_ramp": cfg.get("choropleth_ramp", "Blues"),
                }

            else:  # choropleth
                with col1:
                    field_idx = 0
                    if cfg.get("choropleth_field") in numeric_fields:
                        field_idx = numeric_fields.index(cfg["choropleth_field"])
                    new_field = st.selectbox(
                        "Color by field",
                        numeric_fields,
                        index=field_idx,
                        key=f"field_{title}",
                    )
                with col2:
                    ramp_options = list(COLOR_RAMPS.keys())
                    ramp_idx = ramp_options.index(cfg.get("choropleth_ramp", "Blues")) if cfg.get("choropleth_ramp") in ramp_options else 0
                    new_ramp = st.selectbox(
                        "Color ramp",
                        ramp_options,
                        index=ramp_idx,
                        key=f"ramp_{title}",
                    )

                col3, col4 = st.columns(2)
                with col3:
                    new_opacity = st.slider(
                        "Fill opacity", 0.0, 1.0,
                        value=float(cfg.get("opacity", 0.6)),
                        step=0.05,
                        key=f"opacity_{title}",
                    )
                with col4:
                    new_weight = st.slider(
                        "Stroke weight", 0.0, 5.0,
                        value=float(cfg.get("stroke_weight", 0.5)),
                        step=0.5,
                        key=f"weight_{title}",
                    )

                # Show ramp preview
                ramp_colors = COLOR_RAMPS[new_ramp]
                gradient = ", ".join(ramp_colors)
                st.markdown(
                    f'<div style="height:12px;border-radius:4px;background:linear-gradient(to right,{gradient});margin:4px 0 8px"></div>',
                    unsafe_allow_html=True,
                )

                updated_configs[title] = {
                    "mode": "choropleth",
                    "choropleth_field": new_field,
                    "choropleth_ramp": new_ramp,
                    "opacity": new_opacity,
                    "stroke_color": cfg.get("stroke_color", "#333333"),
                    "stroke_weight": new_weight,
                    "color": cfg.get("color", LAYER_COLORS[i % len(LAYER_COLORS)]),
                    "point_radius": cfg.get("point_radius", 6),
                }

    # Restyle button
    if st.button("🔄 Apply Styles", type="primary"):
        st.session_state["style_configs"] = updated_configs
        geo_info = st.session_state["geo_info"]
        basemap_key = st.session_state.get("basemap_key", "light")
        try:
            fmap = build_map(
                geo_info=geo_info,
                layers=resolved_layers,
                basemap_key=basemap_key,
                zoom_start=zoom_level,
                style_configs=updated_configs,
            )
            new_html = map_to_html(fmap)
            st.markdown("### 🗺 Restyled Map")
            st.markdown('<div class="map-container">', unsafe_allow_html=True)
            components.html(new_html, height=580, scrolling=False)
            st.markdown("</div>", unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Restyle error: {e}")



# ─── Pipeline ────────────────────────────────────────────────────────────────

if run_btn and prompt.strip():

    if not os.environ.get("OLLAMA_API_KEY"):
        st.error("Please enter your Ollama API Key in the sidebar.")
        st.stop()

    st.divider()

    # ── Step 1: Parse intent ──────────────────────────────────────────────────
    with st.status("🧠 Parsing your map request...", expanded=True) as status:
        try:
            intent = parse_map_intent(prompt.strip(), model=model_choice)
            status.update(label="✅ Intent parsed", state="complete", expanded=False)
        except Exception as e:
            status.update(label="❌ LLM parsing failed", state="error")
            st.error(f"Could not parse your request: {e}")
            st.stop()

    # Show parsed intent
    color_map = assign_layer_colors(intent["layers"])
    chips_html = "".join(
        f'<span class="layer-chip" style="background:{color_map[l]}">{l}</span>'
        for l in intent["layers"]
    )

    st.markdown(f"""
    <div class="intent-card">
        <b>📍 Geography:</b> {intent['geography']}<br>
        <b>🗂 Layers:</b> {chips_html}<br>
        <b>🗺 Basemap:</b> {intent['basemap']}
        {"<br><b>📝 Notes:</b> " + intent['notes'] if intent.get('notes') else ""}
    </div>
    """, unsafe_allow_html=True)

    # ── Step 2: Geocode ───────────────────────────────────────────────────────
    with st.status(f"📍 Geocoding: {intent['geography']}...", expanded=False) as status:
        geo_info = geocode_place(intent["geography"])
        if not geo_info:
            status.update(label="❌ Geocoding failed", state="error")
            st.error(f"Could not geocode '{intent['geography']}'. Try a more specific place name.")
            st.stop()
        status.update(label=f"✅ Located: {geo_info['display_name'][:60]}...", state="complete")

    # ── Step 3: Find layers ───────────────────────────────────────────────────
    resolved_layers = []
    layer_sources = []

    for layer_name in intent["layers"]:
        color = color_map.get(layer_name, "#2196F3")

        with st.status(f"🔍 Finding layer: '{layer_name}'...", expanded=False) as status:
            # Step 1: known catalog first (fast, reliable)
            candidates = get_known_layer_candidates(layer_name)

            # Step 2: AGOL search fallback (with LLM-refined query, no geography appended)
            if not candidates:
                try:
                    refined_query = refine_layer_query(layer_name, intent["geography"], model=model_choice)
                except Exception:
                    refined_query = layer_name
                agol_candidates = search_agol_layers(
                    query=refined_query,
                    max_results=max_results,
                )
                candidates = agol_candidates

            if not candidates:
                status.update(label=f"⚠️ No candidates found for '{layer_name}'", state="complete")
                layer_sources.append({"layer": layer_name, "found": False, "title": None})
                continue

            # Try candidates until one returns GeoJSON data
            geojson = None
            chosen = None
            layer_info = None

            for candidate in candidates:
                info = get_layer_info(candidate["query_url"])
                gj = fetch_geojson(
                    candidate["query_url"],
                    bbox=geo_info["bbox"],
                    sr=candidate.get("sr", "4326"),
                )
                if gj and gj.get("features"):
                    geojson = gj
                    chosen = candidate
                    layer_info = info
                    break

            if not geojson or not chosen:
                status.update(label=f"⚠️ No data returned for '{layer_name}' — service may be down", state="complete")
                layer_sources.append({"layer": layer_name, "found": False, "title": candidates[0]["title"] if candidates else None})
                continue

            geo_type = (layer_info or {}).get("geometry_type", "esriGeometryPolygon")
            feat_count = len(geojson.get("features", []))

            resolved_layers.append({
                "title": chosen["title"],
                "layer_label": layer_name,
                "geojson": geojson,
                "geometry_type": geo_type,
                "color": color,
                "url": chosen["query_url"],
                "item_url": chosen.get("item_url", ""),
                "owner": chosen.get("owner", ""),
                "snippet": chosen.get("snippet", ""),
            })

            layer_sources.append({
                "layer": layer_name,
                "found": True,
                "title": chosen["title"],
                "owner": chosen["owner"],
                "item_url": chosen.get("item_url", ""),
                "feat_count": feat_count,
            })

            status.update(
                label=f"✅ '{layer_name}' → {chosen['title']} ({feat_count} features)",
                state="complete",
            )

    # ── Step 4: Build map ─────────────────────────────────────────────────────
    if not resolved_layers:
        st.warning("No data layers could be loaded. Try a different location or layer description.")
        st.stop()

    # Save to session state so style panel can rebuild without re-fetching
    st.session_state["resolved_layers"] = resolved_layers
    st.session_state["geo_info"] = geo_info
    st.session_state["basemap_key"] = intent.get("basemap", "light")
    st.session_state["layer_sources"] = layer_sources
    st.session_state["color_map"] = color_map
    # Reset style configs on new map build
    st.session_state["style_configs"] = {
        layer["title"]: default_style_config(layer, i)
        for i, layer in enumerate(resolved_layers)
    }

    with st.status("🗺 Assembling map...", expanded=False) as status:
        try:
            fmap = build_map(
                geo_info=geo_info,
                layers=resolved_layers,
                basemap_key=intent.get("basemap", "light"),
                zoom_start=zoom_level,
                style_configs=st.session_state["style_configs"],
            )
            map_html = map_to_html(fmap)
            status.update(label="✅ Map ready", state="complete")
        except Exception as e:
            status.update(label="❌ Map build failed", state="error")
            st.error(f"Map assembly error: {e}")
            st.stop()

    _render_map_and_controls(map_html, zoom_level)


elif run_btn and not prompt.strip():
    st.warning("Please enter a map description.")
