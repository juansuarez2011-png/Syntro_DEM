import streamlit as st
import os
import time
import traceback
import tempfile
import zipfile
import numpy as np
import requests
import pystac_client
import planetary_computer
from shapely.geometry import box, shape
import json

st.set_page_config(page_title="Syntro Academy - Descargador DEM", page_icon="🛰️", layout="centered")

st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    </style>
""", unsafe_allow_html=True)

col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.markdown(
            "<div style='width:90px;height:90px;background:linear-gradient(135deg,#3498db,#2c3e50);"
            "border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:36px;'>🛰️</div>",
            unsafe_allow_html=True
        )
with col_title:
    st.title("SYNTRO - DESCARGADOR DEM")
    st.markdown("### Extracción Directa Ligera en la Nube")

st.info("Sube tu archivo de límites (GeoJSON o JSON) para descargar el modelo de elevación digital optimizado.")

uploaded_vector = st.file_uploader(
    "Perímetro del Área de Estudio (.geojson o .json)",
    type=["geojson", "json"]
)

log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()
logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=180)

if st.button("🚀 PROCESAR Y DESCARGAR DEM (.tif)", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(15)
        status_label.text("⏱ Leyendo límites geográficos...")
        registrar_log("Analizando geometría del archivo subido...")

        try:
            geo_data = json.load(uploaded_vector)
            
            # Extraer coordenadas y calcular bounding box simple
            coords = []
            if "features" in geo_data:
                for feat in geo_data["features"]:
                    geom = feat.get("geometry", {})
                    if geom.get("type") == "Polygon":
                        coords.extend(geom.get("coordinates")[0])
                    elif geom.get("type") == "MultiPolygon":
                        for poly in geom.get("coordinates"):
                            coords.extend(poly[0])
            elif "geometry" in geo_data:
                geom = geo_data.get("geometry", {})
                if geom.get("type") == "Polygon":
                    coords.extend(geom.get("coordinates")[0])

            if not coords:
                # Intentar leer bounds genéricos si es un GeoJSON estándar
                raise Exception("No se pudieron extraer las coordenadas del polígono. Asegúrate de que sea un GeoJSON válido.")

            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            west, south, east, north = min(lons), min(lats), max(lons), max(lats)

            registrar_log(f"Extensión detectada -> Oeste: {west:.5f}, Sur: {south:.5f}, Este: {east:.5f}, Norte: {north:.5f}")
            progress_bar.progress(40)

            registrar_log("Conectando con Microsoft Planetary Computer (Copernicus DEM 30m)...")
            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=planetary_computer.sign_inplace,
            )

            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=[west, south, east, north],
                limit=5
            )

            items = list(search.item_collection())
            if not items:
                raise Exception("No se encontraron teselas DEM para las coordenadas indicadas.")

            registrar_log(f"📦 Tesela satelital localizada: {items[0].id}")
            progress_bar.progress(70)

            # Descargar archivo DEM directamente desde la URL firmada de la nube
            url = items[0].assets["data"].href
            registrar_log("Descargando segmento de elevación...")
            
            response = requests.get(url, timeout=60)
            if response.status_code != 200:
                raise Exception(f"Error al descargar la tesela DEM (Código HTTP: {response.status_code})")

            temp_dir = tempfile.mkdtemp()
            output_file = os.path.join(temp_dir, "DEM_Syntro_Cloud.tif")
            
            with open(output_file, "wb") as f:
                f.write(response.content)

            progress_bar.progress(90)
            registrar_log("Generando metadatos y archivo comprimido final...")

            zip_output = os.path.join(temp_dir, "DEM_Syntro_Cloud.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_file, arcname="DEM_Syntro_Cloud.tif")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Completado en {elapsed_time}s!")
            st.success("¡DEM descargado y preparado con éxito!")

            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar DEM (.zip)",
                    f_zip,
                    file_name="DEM_Syntro_Cloud.zip",
                    mime="application/zip",
                    use_container_width=True
                )

        except Exception as e:
            error_completo = traceback.format_exc()
            registrar_log(f"❌ Error: {str(e)}")
            st.error(f"Error crítico: {str(e)}")
            with st.expander("🔧 Detalle técnico"):
                st.code(error_completo, language="python")
    else:
        st.warning("Sube un archivo GeoJSON antes de procesar.")
