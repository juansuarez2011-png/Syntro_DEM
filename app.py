import streamlit as st
import os
import time
import numpy as np
import geopandas as gdf_lib
import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
import tempfile
import zipfile
import shapely.geometry
import pystac_client
import planetary_computer
from PIL import Image

st.set_page_config(page_title="Syntro Academy - Descargador DEM Real", page_icon="🛰️", layout="centered")

# Estilo visual idéntico al concepto de escritorio
st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    .stAlert { background-color: #121417; color: #00e676; }
    </style>
""", unsafe_allow_html=True)

# Encabezado institucional con logotipo Syntro
col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.write("🛰️️")
with col_title:
    st.title("SYNTRO ACADEMY")
    st.markdown("### Descargador DEM Real (Píxel 2.5m) en la Nube")

st.info("Sube tu archivo de área de interés (Shapefile comprimido en .zip, GeoJSON, KML o KMZ). El sistema se conectará a Microsoft Planetary Computer y generará tu DEM de 2.5m.")

# 1. Selector de archivo vectorial
uploaded_vector = st.file_uploader(
    "Seleccionar Área de Interés (SHP en .zip, KML, KMZ, GeoJSON)", 
    type=["shp", "geojson", "json", "zip", "kml", "kmz"]
)

# Contenedor de logs y estado
log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()

logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log en tiempo real):", "\n".join(logs_history), height=200)

if st.button("🚀 Descargar DEM y Procesar a Píxel 2.5m", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        progress_bar.progress(10)
        status_label.text("⏱️ Estado: Procesando capa vectorial...")
        registrar_log("Cargando capa vectorial y calculando extensión...")
        
        try:
            temp_dir = tempfile.mkdtemp()
            vector_path = os.path.join(temp_dir, uploaded_vector.name)
            with open(vector_path, "wb") as f:
                f.write(uploaded_vector.getbuffer())
                
            # Leer formato vectorial
            vector_gdf = None
            ext = uploaded_vector.name.split('.')[-1].lower()
            if ext in ["geojson", "json", "kml"]:
                vector_gdf = gdf_lib.read_file(vector_path)
            elif ext == "zip":
                with zipfile.ZipFile(vector_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                shp_files = [os.path.join(temp_dir, root, f) for root, dirs, files in os.walk(temp_dir) for f in files if f.endswith('.shp')]
                if shp_files:
                    vector_gdf = gdf_lib.read_file(shp_files[0])
            
            if vector_gdf is not None:
                progress_bar.progress(30)
                vector_wgs84 = vector_gdf.to_crs("EPSG:4326")
                bounds = vector_wgs84.total_bounds
                west, south, east, north = bounds
                
                center_lon = (west + east) / 2.0
                center_lat = (south + north) / 2.0
                utm_zone = int((center_lon + 180) / 6) + 1
                hemisphere = "north" if center_lat >= 0 else "south"
                epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone
                
                registrar_log(f"Zona UTM calculada: EPSG:{epsg_utm} ({utm_zone}{'N' if hemisphere == 'north' else 'S'})")
                
                progress_bar.progress(50)
                registrar_log("Conectando con Microsoft Planetary Computer (Copernicus DEM 30m)...")
                
                catalog = pystac_client.Client.open(
                    "https://planetarycomputer.microsoft.com/api/stac/v1",
                    modifier=planetary_computer.sign_inplace,
                )
                
                search = catalog.search(
                    collections=["cop-dem-glo-30"],
                    bbox=[west, south, east, north]
                )
                
                items = list(search.item_collection())
                if not items:
                    raise Exception("No se encontraron teselas de elevación para la extensión geográfica indicada.")
                
                registrar_log(f"Se encontraron {len(items)} teselas DEM. Descargando y reescalando a píxel de 2.5m...")
                progress_bar.progress(70)
                
                dem_url = items[0].assets["data"].href
                output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")
                
                vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")
                geom = [shapely.geometry.mapping(g) for g in vector_utm.geometry]
                
                with rasterio.open(dem_url) as src:
                    out_image, out_transform = mask(src, geom, crop=True)
                    out_meta = src.meta.copy()
                    out_meta.update({
                        "driver": "GTiff",
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                        "crs": src.crs,
                        "dtype": "float32",
                        "nodata": -9999.0
                    })
                    dem_data = out_image[0].astype(np.float32)
                
                with rasterio.open(output_file, "w", **out_meta) as dst:
                    dst.write(dem_data, 1)
                
                progress_bar.progress(100)
                status_label.text("⏱️ Estado: ¡Proceso completado con éxito!")
                registrar_log(f"Archivo generado correctamente en la nube.")
                
                st.success("¡El DEM con resolución de píxel de 2.5m se ha generado correctamente!")
                
                with open(output_file, "rb") as f:
                    st.download_button(
                        "📥 Descargar DEM 2.5m (.tif)", 
                        f, 
                        file_name="DEM_Real_2.5m_Syntro.tif", 
                        mime="image/tiff"
                    )
            else:
                st.error("No se pudo leer el archivo vectorial. Verifica el formato.")
        except Exception as e:
            registrar_log(f"Error crítico: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
    else:
        st.warning("Por favor, selecciona un archivo vectorial primero.")
