import streamlit as st
import os
import time
import traceback
import tempfile
import zipfile
import json
import xml.etree.ElementTree as ET
import shapefile
from shapely.geometry import box, shape, Polygon, MultiPolygon
import requests
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM Multiformato", page_icon="🛰️", layout="centered")

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
    st.title("SYNTRO - DESCARGADOR DEM MULTIFORMATO")
    st.markdown("### Soporte para KML, KMZ, SHP (.zip) y GeoJSON")

st.info("Sube tu archivo de límites en formato KML, KMZ, Shapefile (.zip) o GeoJSON.")

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
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=180)

def extraer_coordenadas_de_geometria(geom_dict):
    """Extrae una lista plana de coordenadas (lons, lats) de un diccionario de geometría GeoJSON"""
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

if st.button("🚀 PROCESAR Y DESCARGAR DEM (.tif)", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(15)
        status_label.text("⏱ Analizando y extrayendo geometría...")
        registrar_log(f"Archivo recibido: {uploaded_file.name}")

        temp_dir = tempfile.mkdtemp()
        
        try:
            file_extension = uploaded_file.name.split('.')[-1].lower()
            input_path = os.path.join(temp_dir, uploaded_file.name)
            
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            todas_coordenadas = []

            # 1. Manejo de GeoJSON / JSON
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

            # 2. Manejo de Shapefile comprimido en ZIP
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
                    raise Exception("No se encontró ningún archivo .shp dentro del archivo .zip.")
                
                registrar_log("Leyendo geometrías del Shapefile...")
                sf = shapefile.Reader(shp_path)
                for shape_obj in sf.shapes():
                    for pt in shape_obj.points:
                        todas_coordenadas.append((pt[0], pt[1]))

            # 3. Manejo de KML o KMZ
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

                # Parsear XML del KML buscando etiquetas de coordenadas <coordinates>
                root = ET.fromstring(kml_content)
                # Namespace común en KML
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
                raise Exception("No se pudieron extraer coordenadas válidas del archivo proporcionado.")

            # Calcular Bounding Box global
            lons = [c[0] for c in todas_coordenadas]
            lats = [c[1] for c in todas_coordenadas]
            west, south, east, north = min(lons), min(lats), max(lons), max(lats)

            registrar_log(f"Extensión detectada -> Oeste: {west:.5f}, Sur: {south:.5f}, Este: {east:.5f}, Norte: {north:.5f}")
            progress_bar.progress(45)

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
                raise Exception("No se encontraron teselas DEM para las coordenadas de la zona.")

            registrar_log(f"📦 Tesela satelital localizada: {items[0].id}")
            progress_bar.progress(70)

            url = items[0].assets["data"].href
            registrar_log("Descargando segmento de elevación...")
            
            response = requests.get(url, timeout=60)
            if response.status_code != 200:
                raise Exception(f"Error al descargar la tesela DEM (Código HTTP: {response.status_code})")

            output_file = os.path.join(temp_dir, "DEM_Syntro_Cloud.tif")
            with open(output_file, "wb") as f:
                f.write(response.content)

            progress_bar.progress(90)
            registrar_log("Generando archivo comprimido final...")

            zip_output = os.path.join(temp_dir, "DEM_Syntro_Cloud.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_file, arcname="DEM_Syntro_Cloud.tif")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Completado en {elapsed_time}s!")
            st.success("¡DEM procesado y descargado con éxito!")

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
        st.warning("Sube un archivo espacial antes de procesar.")
