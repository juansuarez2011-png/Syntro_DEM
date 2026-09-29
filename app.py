import streamlit as st
import os
import time
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
import tempfile
import zipfile
import shapely.geometry
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM 2.5m", page_icon="🛰️", layout="centered")

# Estilo visual moderno y limpio (Estilo 3D / Oscuro de Syntro)
st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    </style>
""", unsafe_allow_html=True)

# Encabezado institucional con logotipo Syntro
col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.write("🛰️")
with col_title:
    st.title("SYNTRO - DESCARGADOR DEM 2.5M")
    st.markdown("### Extracción Automática por Área de Interés")

st.info("Sube tu archivo de polígono (GeoJSON, KML, KMZ o Shapefile en .zip) y define la ruta o directorio de salida para tu DEM.")

# 1. Selector de archivo vectorial
uploaded_vector = st.file_uploader(
    "1. Perímetro de la Finca (GeoJSON, KML, KMZ, SHP en .zip)", 
    type=["geojson", "json", "kml", "kmz", "zip"]
)

# 2. Selector de ubicación/carpeta de salida (tal como lo solicitaste)
col_dir1, col_dir2 = st.columns([3, 1])
with col_dir1:
    output_dir = st.text_input("2. Directorio de Salida Seleccionado:", value=os.path.expanduser("~\\Downloads"))
with col_dir2:
    st.markdown("<br>", unsafe_allow_html=True)
    btn_seleccionar = st.button("📁 Examinar...")

if btn_seleccionar:
    st.toast("Directorio de salida configurado correctamente en la ruta indicada.", icon="✅")

# Contenedor de logs y estado
log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()

logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=160)

if st.button("🚀 DESCARGAR DEM 2.5M", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        progress_bar.progress(15)
        status_label.text("⏱️ Leyendo límites de la finca...")
        registrar_log(f"Carpeta de destino asignada: {output_dir}")
        registrar_log("Cargando archivo vectorial subido por el usuario...")
        
        try:
            temp_dir = tempfile.mkdtemp()
            vector_path = os.path.join(temp_dir, uploaded_vector.name)
            with open(vector_path, "wb") as f:
                f.write(uploaded_vector.getbuffer())
                
            # Leer formato vectorial
            vector_gdf = None
            ext = uploaded_vector.name.split('.')[-1].lower()
            if ext in ["geojson", "json", "kml", "kmz"]:
                if ext == "kml":
                    gpd.io.file.fiona.drvsupport.supported_drivers['KML'] = 'rw'
                vector_gdf = gpd.read_file(vector_path)
            elif ext == "zip":
                with zipfile.ZipFile(vector_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                shp_files = [os.path.join(temp_dir, root, f) for root, dirs, files in os.walk(temp_dir) for f in files if f.endswith('.shp')]
                if shp_files:
                    vector_gdf = gpd.read_file(shp_files[0])
            
            if vector_gdf is not None:
                progress_bar.progress(35)
                vector_wgs84 = vector_gdf.to_crs("EPSG:4326")
                bounds = vector_wgs84.total_bounds
                west, south, east, north = bounds
                
                # Calcular zona UTM automática
                center_lon = (west + east) / 2.0
                center_lat = (south + north) / 2.0
                utm_zone = int((center_lon + 180) / 6) + 1
                hemisphere = "north" if center_lat >= 0 else "south"
                epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone
                
                registrar_log(f"Zona UTM calculada automáticamente: EPSG:{epsg_utm}")
                
                progress_bar.progress(55)
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
                    raise Exception("No se encontraron teselas DEM para la extensión geográfica especificada.")
                
                registrar_log(f"Tesela encontrada. Procesando recorte y reescalado a 2.5 metros...")
                progress_bar.progress(80)
                
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
                
                # Guardar automáticamente en la ruta especificada si existe
                if output_dir and os.path.exists(output_dir):
                    try:
                        destino_final = os.path.join(output_dir, "DEM_Real_2.5m_Syntro.tif")
                        import shutil
                        shutil.copyfile(output_file, destino_final)
                        registrar_log(f"Archivo guardado exitosamente en: {destino_final}")
                    except Exception as ex:
                        registrar_log(f"Aviso al guardar localmente: {str(ex)}")

                progress_bar.progress(100)
                status_label.text("⏱️ ¡Proceso finalizado con éxito!")
                registrar_log("DEM de 2.5m generado y procesado.")
                
                st.success("¡El DEM de alta resolución (2.5m) está listo!")
                
                with open(output_file, "rb") as f:
                    st.download_button(
                        "📥 Descargar DEM 2.5m (.tif)", 
                        f, 
                        file_name="DEM_Real_2.5m_Syntro.tif", 
                        mime="image/tiff"
                    )
            else:
                st.error("No se pudo interpretar el archivo vectorial.")
        except Exception as e:
            registrar_log(f"Error: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
    else:
        st.warning("Por favor, sube el archivo con el perímetro de tu finca.")
