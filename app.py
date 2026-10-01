import streamlit as st
import os
import time
import traceback
import tempfile
import zipfile
import json
import xml.etree.ElementTree as ET
import shapefile
import rasterio
from rasterio.plot import reshape_as_image
import numpy as np
import requests
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM Avanzado", page_icon="🛰️", layout="centered")

st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    .metric-card {
        background: #2b2b36;
        border: 1px solid #3d3d4d;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
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
    st.title("SYNTRO - DESCARGADOR DEM MULTIFORMATO")
    st.markdown("### Con Estadísticas de Elevación (Mín, Máx y Rango)")

st.info("Sube tu archivo de límites (KML, KMZ, Shapefile .zip o GeoJSON) para extraer la topografía y sus métricas.")

uploaded_file = st.file_uploader(
    "Área de Estudio",
    type=["geojson", "json", "kml", "kmz", "zip"]
)

log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()
logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=150)

def extraer_coordenadas_de_geometria(geom_dict):
    coords = []
    g_type = geom_dict.get("type")
    g_coords = geom_dict.get("coordinates", [])

    if g_type == "Polygon":
        for ring in g_coords:
            for pt in ring:
                coords.append((pt[0], pt[1]))
    elif g_type == "MultiPolygon":
        for poly in g_coords:
            for ring in poly:
                for pt in ring:
                    coords.append((pt[0], pt[1]))
    elif g_type == "Point":
        coords.append((g_coords[0], g_coords[1]))
    elif g_type == "LineString":
        for pt in g_coords:
            coords.append((pt[0], pt[1]))
    return coords

if st.button("🚀 PROCESAR Y CALCULAR ESTADÍSTICAS", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(10)
        status_label.text("⏱ Analizando archivo y extrayendo geometría...")
        registrar_log(f"Archivo recibido: {uploaded_file.name}")

        temp_dir = tempfile.mkdtemp()
        
        try:
            file_extension = uploaded_file.name.split('.')[-1].lower()
            input_path = os.path.join(temp_dir, uploaded_file.name)
            
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            todas_coordenadas = []

            # 1. GeoJSON / JSON
            if file_extension in ['geojson', 'json']:
                registrar_log("Procesando formato GeoJSON...")
                with open(input_path, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                if "features" in data:
                    for feat in data["features"]:
                        geom = feat.get("geometry")
                        if geom:
                            todas_coordenadas.extend(extraer_coordenadas_de_geometria(geom))
                elif "geometry" in data:
                    todas_coordenadas.extend(extraer_coordenadas_de_geometria(data["geometry"]))

            # 2. Shapefile (.zip)
            elif file_extension == 'zip':
                registrar_log("Descomprimiendo Shapefile (.zip)...")
                with zipfile.ZipFile(input_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                
                shp_path = None
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        if file.endswith('.shp'):
                            shp_path = os.path.join(root, file)
                            break
                if not shp_path:
                    raise Exception("No se encontró ningún archivo .shp dentro del .zip.")
                
                registrar_log("Leyendo geometrías del Shapefile...")
                sf = shapefile.Reader(shp_path)
                for shape_obj in sf.shapes():
                    for pt in shape_obj.points:
                        todas_coordenadas.append((pt[0], pt[1]))

            # 3. KML / KMZ
            elif file_extension in ['kml', 'kmz']:
                registrar_log(f"Procesando archivo {file_extension.upper()}...")
                kml_content = None
                if file_extension == 'kmz':
                    with zipfile.ZipFile(input_path, 'r') as kmz_ref:
                        kml_filename = [name for name in kmz_ref.namelist() if name.endswith('.kml')][0]
                        with kmz_ref.open(kml_filename) as kf:
                            kml_content = kf.read()
                else:
                    with open(input_path, 'rb') as kf:
                        kml_content = kf.read()

                root = ET.fromstring(kml_content)
                for elem in root.iter():
                    if elem.tag.endswith('coordinates') and elem.text:
                        coords_text = elem.text.strip()
                        for tuple_str in coords_text.split():
                            parts = tuple_str.split(',')
                            if len(parts) >= 2:
                                try:
                                    lon, lat = float(parts[0]), float(parts[1])
                                    todas_coordenadas.append((lon, lat))
                                except ValueError:
                                    continue
            else:
                raise Exception("Formato de archivo no soportado.")

            if not todas_coordenadas:
                raise Exception("No se pudieron extraer coordenadas válidas del archivo.")

            # Bounding Box
            lons = [c[0] for c in todas_coordenadas]
            lats = [c[1] for c in todas_coordenadas]
            west, south, east, north = min(lons), min(lats), max(lons), max(lats)

            registrar_log(f"Extensión -> O: {west:.4f}, S: {south:.4f}, E: {east:.4f}, N: {north:.4f}")
            progress_bar.progress(40)

            # STAC Planetary Computer
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
                raise Exception("No se encontraron teselas DEM para esta zona.")

            registrar_log(f"📦 Tesela localizada: {items[0].id}")
            progress_bar.progress(65)

            url = items[0].assets["data"].href
            registrar_log("Descargando segmento de elevación ráster...")
            
            response = requests.get(url, timeout=60)
            if response.status_code != 200:
                raise Exception(f"Error descargando tesela DEM (HTTP: {response.status_code})")

            output_file = os.path.join(temp_dir, "DEM_Syntro_Cloud.tif")
            with open(output_file, "wb") as f:
                f.write(response.content)

            progress_bar.progress(85)
            registrar_log("Calculando estadísticas topográficas con Rasterio...")

            # Cálculo de Mínimo, Máximo y Rango con Rasterio
            with rasterio.open(output_file) as src:
                dem_array = src.read(1)
                nodata = src.nodatavals[0]
                
                # Filtrar valores NoData si existen
                if nodata is not None:
                    valid_pixels = dem_array[dem_array != nodata]
                else:
                    valid_pixels = dem_array.flatten()

                # Eliminar ceros anómalos o valores negativos extremos si aplican en agua (opcional, dejamos puros finitos)
                valid_pixels = valid_pixels[np.isfinite(valid_pixels)]

                elev_min = float(np.min(valid_pixels)) if valid_pixels.size > 0 else 0.0
                elev_max = float(np.max(valid_pixels)) if valid_pixels.size > 0 else 0.0
                elev_range = elev_max - elev_min

            registrar_log(f"Mínimo: {elev_min:.2f} m | Máximo: {elev_max:.2f} m | Rango: {elev_range:.2f} m")

            # Crear reporte en texto plano de las estadísticas
            report_path = os.path.join(temp_dir, "reporte_estadisticas_dem.txt")
            with open(report_path, "w", encoding="utf-8") as rep:
                rep.write("=========================================\n")
                rep.write("  REPORTE ESTADÍSTICO DE ELEVACIÓN SYNTRO\n")
                rep.write("=========================================\n")
                rep.write(f"Archivo analizado: {uploaded_file.name}\n")
                rep.write(f"Tesela DEM: {items[0].id}\n")
                rep.write(f"Elevación Mínima: {elev_min:.2f} m.s.n.m.\n")
                rep.write(f"Elevación Máxima: {elev_max:.2f} m.s.n.m.\n")
                rep.write(f"Rango Altitudinal (Desnivel): {elev_range:.2f} m\n")
                rep.write("=========================================\n")

            # Comprimir archivo final ZIP con el TIF y el reporte
            zip_output = os.path.join(temp_dir, "DEM_Syntro_Cloud_Con_Estadisticas.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_file, arcname="DEM_Syntro_Cloud.tif")
                zf.write(report_path, arcname="reporte_estadisticas_dem.txt")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Procesado con éxito en {elapsed_time}s!")
            st.success("¡Modelo DEM y estadísticas calculadas correctamente!")

            # Mostrar tarjetas visuales con el Mínimo, Máximo y Rango
            st.markdown("### 📊 Resultados Topográficos")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric(label="⛰️ Altura Mínima", value=f"{elev_min:.2f} m")
            with m2:
                st.metric(label="🏔️ Altura Máxima", value=f"{elev_max:.2f} m")
            with m3:
                st.metric(label="📏 Rango (Desnivel)", value=f"{elev_range:.2f} m")

            st.write("")
            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar Paquete Completo (.zip con DEM y Reporte)",
                    f_zip,
                    file_name="DEM_Syntro_Estadisticas.zip",
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
        st.warning("Por favor, sube un archivo espacial antes de procesar.")
