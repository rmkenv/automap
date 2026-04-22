"""
dashboard.py — Data dashboard builder for loaded GeoJSON layers.
Lets users pick fields and chart types to explore loaded layer data.
"""

import streamlit as st
import pandas as pd
from collections import Counter
from map_builder import get_numeric_fields, get_string_fields, get_all_fields

try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def geojson_to_df(geojson: dict) -> pd.DataFrame:
    """Convert a GeoJSON FeatureCollection to a flat DataFrame."""
    rows = []
    for f in geojson.get("features", []):
        props = f.get("properties") or {}
        rows.append(props)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def render_dashboard():
    """Render the full dashboard tab. Reads resolved_layers from session state."""
    resolved_layers = st.session_state.get("resolved_layers", [])

    if not resolved_layers:
        st.info("Build a map first — loaded layers will appear here for exploration.")
        return

    if not HAS_PLOTLY:
        st.error("Plotly is required for the dashboard. Add `plotly` to requirements.txt.")
        return

    # Layer selector
    layer_titles = [l["title"] for l in resolved_layers]
    selected_title = st.selectbox("Select layer to explore", layer_titles, key="dash_layer")
    layer = next(l for l in resolved_layers if l["title"] == selected_title)
    geojson = layer["geojson"]
    df = geojson_to_df(geojson)

    if df.empty:
        st.warning("No attribute data found for this layer.")
        return

    n_features = len(df)
    all_fields = list(df.columns)
    numeric_fields = [c for c in all_fields if pd.api.types.is_numeric_dtype(df[c])]
    string_fields = [c for c in all_fields if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object]

    st.caption(f"{n_features} features · {len(all_fields)} fields")

    # ── Tabs inside dashboard ──────────────────────────────────────────────────
    dtab1, dtab2, dtab3, dtab4 = st.tabs(["📊 Charts", "🔢 Summary Stats", "📋 Data Table", "🗂 Field Explorer"])

    # ── Charts tab ────────────────────────────────────────────────────────────
    with dtab1:
        if not all_fields:
            st.info("No fields available.")
        else:
            col1, col2 = st.columns([2, 1])
            with col1:
                chart_type = st.selectbox(
                    "Chart type",
                    ["Bar — count by category", "Pie — proportion by category",
                     "Histogram — numeric distribution", "Box plot — numeric spread",
                     "Scatter — two numeric fields", "Stacked bar — two categories"],
                    key="dash_chart_type",
                )
            with col2:
                st.markdown("&nbsp;", unsafe_allow_html=True)

            if chart_type == "Bar — count by category":
                if not string_fields:
                    st.info("No categorical fields available.")
                else:
                    field = st.selectbox("Category field", string_fields, key="dash_bar_field")
                    top_n = st.slider("Show top N values", 5, 30, 15, key="dash_bar_topn")
                    counts = df[field].value_counts().head(top_n).reset_index()
                    counts.columns = [field, "count"]
                    fig = px.bar(counts, x=field, y="count", color=field,
                                 title=f"Feature count by {field}",
                                 color_discrete_sequence=px.colors.qualitative.Set2)
                    fig.update_layout(showlegend=False, xaxis_tickangle=-35)
                    st.plotly_chart(fig, use_container_width=True)

            elif chart_type == "Pie — proportion by category":
                if not string_fields:
                    st.info("No categorical fields available.")
                else:
                    field = st.selectbox("Category field", string_fields, key="dash_pie_field")
                    top_n = st.slider("Max slices (others grouped)", 5, 20, 10, key="dash_pie_topn")
                    counts = df[field].value_counts()
                    top = counts.head(top_n)
                    other = counts.iloc[top_n:].sum()
                    if other > 0:
                        top["Other"] = other
                    fig = px.pie(values=top.values, names=top.index,
                                 title=f"Proportion by {field}",
                                 color_discrete_sequence=px.colors.qualitative.Set2)
                    fig.update_traces(textposition="inside", textinfo="percent+label")
                    st.plotly_chart(fig, use_container_width=True)

            elif chart_type == "Histogram — numeric distribution":
                if not numeric_fields:
                    st.info("No numeric fields available.")
                else:
                    field = st.selectbox("Numeric field", numeric_fields, key="dash_hist_field")
                    bins = st.slider("Bins", 5, 100, 20, key="dash_hist_bins")
                    series = df[field].dropna()
                    fig = px.histogram(series, x=field, nbins=bins,
                                       title=f"Distribution of {field}",
                                       color_discrete_sequence=["#2196F3"])
                    fig.update_layout(bargap=0.05)
                    st.plotly_chart(fig, use_container_width=True)

            elif chart_type == "Box plot — numeric spread":
                if not numeric_fields:
                    st.info("No numeric fields available.")
                else:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        num_field = st.selectbox("Numeric field", numeric_fields, key="dash_box_num")
                    with col_b:
                        group_field = st.selectbox("Group by (optional)", ["None"] + string_fields, key="dash_box_grp")
                    if group_field == "None":
                        fig = px.box(df, y=num_field, title=f"{num_field} distribution",
                                     color_discrete_sequence=["#2196F3"])
                    else:
                        top_cats = df[group_field].value_counts().head(10).index.tolist()
                        dff = df[df[group_field].isin(top_cats)]
                        fig = px.box(dff, x=group_field, y=num_field,
                                     title=f"{num_field} by {group_field}",
                                     color=group_field,
                                     color_discrete_sequence=px.colors.qualitative.Set2)
                        fig.update_layout(showlegend=False, xaxis_tickangle=-35)
                    st.plotly_chart(fig, use_container_width=True)

            elif chart_type == "Scatter — two numeric fields":
                if len(numeric_fields) < 2:
                    st.info("Need at least two numeric fields for scatter plot.")
                else:
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        x_field = st.selectbox("X axis", numeric_fields, key="dash_scat_x")
                    with col_b:
                        y_opts = [f for f in numeric_fields if f != x_field]
                        y_field = st.selectbox("Y axis", y_opts, key="dash_scat_y")
                    with col_c:
                        color_opts = ["None"] + string_fields
                        color_field = st.selectbox("Color by", color_opts, key="dash_scat_color")
                    dff = df[[x_field, y_field] + ([color_field] if color_field != "None" else [])].dropna()
                    fig = px.scatter(
                        dff, x=x_field, y=y_field,
                        color=color_field if color_field != "None" else None,
                        title=f"{x_field} vs {y_field}",
                        opacity=0.7,
                        color_discrete_sequence=px.colors.qualitative.Set2,
                    )
                    st.plotly_chart(fig, use_container_width=True)

            elif chart_type == "Stacked bar — two categories":
                if len(string_fields) < 2:
                    st.info("Need at least two categorical fields.")
                else:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        x_field = st.selectbox("X axis (primary category)", string_fields, key="dash_stk_x")
                    with col_b:
                        stack_opts = [f for f in string_fields if f != x_field]
                        stack_field = st.selectbox("Stack by", stack_opts, key="dash_stk_s")
                    top_x = df[x_field].value_counts().head(12).index.tolist()
                    top_s = df[stack_field].value_counts().head(8).index.tolist()
                    dff = df[df[x_field].isin(top_x) & df[stack_field].isin(top_s)]
                    pivot = dff.groupby([x_field, stack_field]).size().reset_index(name="count")
                    fig = px.bar(pivot, x=x_field, y="count", color=stack_field,
                                 barmode="stack",
                                 title=f"{x_field} stacked by {stack_field}",
                                 color_discrete_sequence=px.colors.qualitative.Set2)
                    fig.update_layout(xaxis_tickangle=-35)
                    st.plotly_chart(fig, use_container_width=True)

    # ── Summary Stats tab ─────────────────────────────────────────────────────
    with dtab2:
        if numeric_fields:
            st.markdown("**Numeric fields**")
            st.dataframe(
                df[numeric_fields].describe().round(3).T.rename(columns={
                    "count": "Count", "mean": "Mean", "std": "Std Dev",
                    "min": "Min", "25%": "25th %", "50%": "Median",
                    "75%": "75th %", "max": "Max"
                }),
                use_container_width=True,
            )
        if string_fields:
            st.markdown("**Categorical fields — top values**")
            for f in string_fields[:8]:
                vc = df[f].value_counts().head(5)
                st.markdown(f"**{f}**: " + "  ·  ".join(f"`{v}` ({c})" for v, c in vc.items()))

    # ── Data Table tab ────────────────────────────────────────────────────────
    with dtab3:
        col_select = st.multiselect(
            "Show columns",
            all_fields,
            default=all_fields[:8],
            key="dash_table_cols",
        )
        if col_select:
            st.dataframe(
                df[col_select].head(200),
                use_container_width=True,
                height=400,
            )
            csv = df[col_select].to_csv(index=False)
            st.download_button(
                "⬇️ Download CSV",
                data=csv,
                file_name=f"{selected_title.replace(' ', '_')}.csv",
                mime="text/csv",
            )

    # ── Field Explorer tab ────────────────────────────────────────────────────
    with dtab4:
        st.markdown("**All fields in this layer:**")
        for col in all_fields:
            dtype = str(df[col].dtype)
            n_null = df[col].isna().sum()
            n_unique = df[col].nunique()
            sample = df[col].dropna().head(3).tolist()
            st.markdown(
                f"**`{col}`** &nbsp; `{dtype}` &nbsp; "
                f"{n_unique} unique · {n_null} null &nbsp; "
                f"*e.g. {', '.join(str(s)[:30] for s in sample)}*"
            )
