"""
pages/1_S1_Growth_Curves.py

Second task in the dashboard: on-demand Sentinel-1 VH/VV growth curves for
any uploaded field boundaries, pulled live from Sentinel Hub (Process API) —
no local SNAP preprocessing required. This is independent of the block-level
RVI early-warning pipeline on the Home page; use it to explore raw growth
curves for a new field, sanity-check backscatter, or monitor fields that
aren't yet part of the `blocks.geojson` inventory.
"""
import io
import tempfile
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from src.s1_growth_curves import (
    apply_smoothing,
    authenticate_sentinel_hub,
    build_growth_curves_figure,
    build_process_requests,
    calculate_summary_metrics,
    extract_timeseries,
    load_fields,
    match_field_acquisitions,
    search_sentinel1_catalog,
)

st.set_page_config(page_title="S1 Growth Curves", layout="wide")
st.title("🛰️ Sentinel-1 Growth Curves")
st.caption(
    "Upload field boundaries and fetch VH/VV backscatter growth curves directly from "
    "Sentinel Hub — a lighter-weight companion to the block-level RVI early-warning system."
)


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


with st.sidebar:
    st.header("Inputs")
    geojson_file = st.file_uploader("Field boundaries (GeoJSON)", type=["geojson", "json"])

    c1, c2 = st.columns(2)
    start_date = c1.date_input("Start date", value=date.today() - timedelta(days=180))
    end_date = c2.date_input("End date", value=date.today())

    st.markdown("---")
    st.subheader("Sentinel Hub credentials")
    st.caption("Free credentials: dataspace.copernicus.eu → User Settings → OAuth clients.")
    sh_client_id = st.text_input("Client ID", value=_secret("SH_CLIENT_ID"), type="password")
    sh_client_secret = st.text_input("Client secret", value=_secret("SH_CLIENT_SECRET"), type="password")
    with st.expander("Advanced"):
        sh_base_url = st.text_input("Base URL", value=_secret("SH_BASE_URL", "https://sh.dataspace.copernicus.eu"))
        sh_token_url = st.text_input(
            "Token URL",
            value=_secret(
                "SH_TOKEN_URL",
                "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
            ),
        )
        max_threads = st.slider("Parallel downloads", 1, 10, 5)

    run_clicked = st.button("Run", type="primary", use_container_width=True)

if not run_clicked:
    st.info("Upload a GeoJSON of field boundaries, set a date range and credentials, then click **Run**.")
    st.stop()

if geojson_file is None:
    st.error("Please upload a GeoJSON file of field boundaries.")
    st.stop()
if start_date >= end_date:
    st.error("Start date must be before end date.")
    st.stop()
if not sh_client_id or not sh_client_secret:
    st.error("Sentinel Hub client ID/secret are required (sidebar, or Streamlit secrets).")
    st.stop()

tmp_path = None
try:
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
        tmp.write(geojson_file.getvalue())
        tmp_path = tmp.name

    with st.status("Fetching Sentinel-1 growth curves...", expanded=True) as status:
        fields_gdf = load_fields(tmp_path)
        st.write(f"Loaded {len(fields_gdf)} field(s).")

        status.update(label="Authenticating with Sentinel Hub...")
        config = authenticate_sentinel_hub(sh_client_id, sh_client_secret, sh_base_url, sh_token_url)

        status.update(label="Searching the Sentinel-1 catalog...")
        acquisitions = search_sentinel1_catalog(fields_gdf, str(start_date), str(end_date), config)
        field_acqs = match_field_acquisitions(fields_gdf, acquisitions)
        st.write(f"Found {len(acquisitions)} candidate acquisition(s) over the field extent.")

        status.update(label="Requesting field-clipped rasters from the Process API...")
        requests_info = build_process_requests(fields_gdf, field_acqs, config)
        st.write(f"Downloading {len(requests_info)} field × date raster(s)...")
        df = extract_timeseries(requests_info, config, max_threads=max_threads)

        status.update(label="Smoothing and computing summary metrics...")
        df = apply_smoothing(df)
        summary_df = calculate_summary_metrics(df, str(start_date))

        status.update(label="Done.", state="complete")
except Exception as exc:
    st.error(f"Failed to generate growth curves: {exc}")
    st.stop()
finally:
    if tmp_path:
        Path(tmp_path).unlink(missing_ok=True)

st.subheader("Field growth summary")
st.dataframe(summary_df, use_container_width=True)

st.subheader("Growth curves")
fig = build_growth_curves_figure(df)
st.pyplot(fig, use_container_width=True)

dl1, dl2, dl3 = st.columns(3)
dl1.download_button("Download time-series CSV", df.to_csv(index=False).encode(), "s1_timeseries.csv", "text/csv")

png_buf = io.BytesIO()
fig.savefig(png_buf, format="png", dpi=300, bbox_inches="tight")
dl2.download_button("Download growth curves PNG", png_buf.getvalue(), "growth_curves.png", "image/png")

dl3.download_button("Download summary CSV", summary_df.to_csv(index=False).encode(), "growth_summary.csv", "text/csv")
