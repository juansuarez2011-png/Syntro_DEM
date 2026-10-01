import streamlit as st
import os
import time
import traceback
import tempfile
import zipfile
import json
import geopandas as gpd
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

if st.button("🚀 PROCESAR Y DESCARGAR DEM (.tif)", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(15)
        status_label.text("⏱ Leyendo y normalizando geometría espacial...")
        registrar_log(f"Archivo recibido: {uploaded_file.name}")

        temp_dir = tempfile.mkdtemp()
        
        try:
            file_extension = uploaded_file.name.split('.')[-1].lower()
            input_path = os.path.join(temp_dir, uploaded_file.name)
            
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            # Manejo de Shapefile comprimido en ZIP
            if file_extension == 'zip':
                registrar_log("Descomprimiendo Shapefile (.zip)...")
                with zipfile.ZipFile(input_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                
                # Buscar el archivo .shp dentro de la carpeta descomprimida
                shp_file = None
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        if file.endswith('.shp'):
                            shp_file = os.path.join(root, file)
                            break
                if not shp_file:
                    raise Exception("No se encontró ningún archivo .shp dentro del archivo .zip.")
                
                gdf = gpd.read_file(shp_file)

            # Manejo de KML o KMZ
            elif file_extension in ['kml', 'kmz']:
                registrar_log(f"Procesando archivo {file_extension.upper()}...")
                # Habilitar driver KML en Fiona/Geopandas
                gpd.io.file.fiona.drvsupport.supported_drivers['KML'] = 'rw'
                gpd.io.file.fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
                
                if file_extension == 'kmz':
                    # Extraer el kml dentro del kmz
                    with zipfile.ZipFile(input_path, 'r') as kmz_ref:
                        kml_filename = [name for name in kmz_ref.namelist() if name.endswith('.kml')][0]
                        kmz_ref.extract(kml_filename, temp_dir)
                        kml_path = os.path.join(temp_dir, kml_filename)
                    gdf = gpd.read_file(kml_path, driver='KML')
                else:
                    gdf = gpd.read_file(input_path, driver='KML')

            # Manejo de GeoJSON o JSON
            elif file_extension in ['geojson', 'json']:
                registrar_log("Procesando formato GeoJSON...")
                gdf = gpd.read_file(input_path)

            else:
                raise Exception("Formato de archivo no soportado.")

            # Asegurar que esté en coordenadas geográficas WGS84 (EPSG:4326)
            if gdf.crs is not None and gdf.crs != "EPSG:4326":
                registrar_log(f"Reproyectando desde {gdf.crs} a WGS84 (EPSG:4326)...")
                gdf = gdf.to_crs("EPSG:4326")

            # Obtener el Bounding Box global de todas las geometrías
            total_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
            west, south, east, north = total_bounds[0], total_bounds[1], total_bounds[2], total_bounds[3]

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
                raise Exception("No se encontraron teselas DEM para las coordenadas del área seleccionada.")

            registrar_log(f"📦 Tesela satelital localizada: {items[0].id}")
            progress_bar.progress(70)

            # Descargar archivo DEM directamente desde la URL firmada
            url = items[0].assets["data"].href
            registrar_log("Descargando segmento de elevación...")
            
            response = requests.get(url, timeout=60)
            if response.status_code != 200:
                raise Exception(f"Error al descargar la tesela DEM (Código HTTP: {response.status_code})")

            output_file = os.path.join(temp_dir, "DEM_Syntro_Cloud.tif")
            with open(output_file, "wb") as f:
                f.write(response.content)

            progress_bar.progress(90)
            registrar_log("Generando paquete comprimido final...")

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
