"""
app.py — Map Genie: Natural Language → ArcGIS Online → Folium Map
Powered by Ollama Cloud + ArcGIS REST API + Folium
"""

import os
import streamlit as st
import streamlit.components.v1 as components

from llm import parse_map_intent, refine_layer_query
from agol_search import geocode_place, search_agol_layers, get_layer_info, fetch_geojson
from map_builder import build_map, map_to_html, assign_layer_colors, LAYER_COLORS

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
            st.session_state["prompt"] = ex
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

prompt = st.text_area(
    "What map do you want to build?",
    value=st.session_state.get("prompt", ""),
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

        with st.status(f"🔍 Searching AGOL: '{layer_name}'...", expanded=False) as status:
            # Get LLM-optimized query
            try:
                refined_query = refine_layer_query(layer_name, intent["geography"], model=model_choice)
            except Exception:
                refined_query = layer_name

            # Search AGOL
            candidates = search_agol_layers(
                query=refined_query,
                geography=intent["geography"],
                max_results=max_results,
            )

            if not candidates:
                status.update(label=f"⚠️ No results for '{layer_name}'", state="complete")
                layer_sources.append({"layer": layer_name, "found": False, "title": None})
                continue

            # Try candidates until one returns GeoJSON data
            geojson = None
            chosen = None
            layer_info = None

            for candidate in candidates:
                info = get_layer_info(candidate["url"])
                gj = fetch_geojson(candidate["url"], bbox=geo_info["bbox"])
                if gj and gj.get("features"):
                    geojson = gj
                    chosen = candidate
                    layer_info = info
                    break

            if not geojson or not chosen:
                status.update(label=f"⚠️ Found candidates but no data in bbox for '{layer_name}'", state="complete")
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
                "url": chosen["url"],
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

    with st.status("🗺 Assembling map...", expanded=False) as status:
        try:
            fmap = build_map(
                geo_info=geo_info,
                layers=resolved_layers,
                basemap_key=intent.get("basemap", "light"),
                zoom_start=zoom_level,
            )
            map_html = map_to_html(fmap)
            status.update(label="✅ Map ready", state="complete")
        except Exception as e:
            status.update(label="❌ Map build failed", state="error")
            st.error(f"Map assembly error: {e}")
            st.stop()

    # ── Render map ────────────────────────────────────────────────────────────
    st.markdown("### 🗺 Your Map")
    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    components.html(map_html, height=580, scrolling=False)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Layer summary ─────────────────────────────────────────────────────────
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

elif run_btn and not prompt.strip():
    st.warning("Please enter a map description.")
