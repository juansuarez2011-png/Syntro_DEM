import streamlit as st
import os
import time
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
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
    st.markdown("### Extracción Automática por Área de Estudio")

st.info("Sube el perímetro de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para procesar y descargar el DEM en formato .tif.")

# Archivo Geográfico (Único campo de entrada)
uploaded_vector = st.file_uploader(
    "Perímetro del Área de Estudio (GeoJSON, KML, KMZ, SHP en .zip)", 
    type=["geojson", "json", "kml", "kmz", "zip"]
)

# Contenedor de logs y estado
log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()

logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=160)

# Botón para ejecutar la descarga y procesamiento del DEM
if st.button("🚀 DESCARGAR DEM 2.5M (.tif)", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        progress_bar.progress(15)
        status_label.text("⏱️ Leyendo límites del área de estudio...")
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
                
                registrar_log("Tesela encontrada. Procesando recorte y reescalado a 2.5 metros (.tif)...")
                progress_bar.progress(80)
                
                dem_url = items[0].assets["data"].href
                output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")
                
                vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")
                geom = [shapely.geometry.mapping(g) for g in vector_utm.geometry]
                
                # Recorte, reproyección y remuestreo a 2.5m en formato GeoTIFF (.tif)
                with rasterio.open(dem_url) as src:
                    out_image, out_transform = mask(
                        src, 
                        geom, 
                        crop=True, 
                        all_touched=True,
                        filled=True,
                        nodata=-9999.0
                    )
                    
                    minx, miny, maxx, maxy = vector_utm.total_bounds
                    res = 2.5
                    width = int(round((maxx - minx) / res))
                    height = int(round((maxy - miny) / res))
                    
                    if width > 0 and height > 0:
                        transform_25m = from_bounds(minx, miny, maxx, maxy, width, height)
                        reprojected_data = np.zeros((1, height, width), dtype=np.float32)
                        
                        rasterio.warp.reproject(
                            source=out_image,
                            destination=reprojected_data,
                            src_transform=out_transform,
                            src_crs=src.crs,
                            dst_transform=transform_25m,
                            dst_crs=f"EPSG:{epsg_utm}",
                            resampling=Resampling.bilinear,
                            src_nodata=-9999.0,
                            dst_nodata=-9999.0
                        )
                        dem_data = reprojected_data[0]
                        final_transform = transform_25m
                    else:
                        dem_data = out_image[0].astype(np.float32)
                        final_transform = out_transform
                        width = out_image.shape[2]
                        height = out_image.shape[1]

                    out_meta = src.meta.copy()
                    out_meta.update({
                        "driver": "GTiff",
                        "height": height,
                        "width": width,
                        "transform": final_transform,
                        "crs": f"EPSG:{epsg_utm}",
                        "dtype": "float32",
                        "nodata": -9999.0,
                        "compress": "lzw"
                    })
                
                with rasterio.open(output_file, "w", **out_meta) as dst:
                    dst.write(dem_data, 1)

                progress_bar.progress(100)
                status_label.text("⏱️ ¡Proceso finalizado con éxito!")
                registrar_log("DEM de elevación exportado en formato .tif correctamente a partir del área de estudio.")
                
                st.success("¡El DEM de elevación en formato TIF (.tif) a 2.5m está listo para descargar!")
                
                with open(output_file, "rb") as f:
                    st.download_button(
                        "📥 Descargar DEM 2.5m (.tif)", 
                        f, 
                        file_name="DEM_Real_2.5m_Syntro.tif", 
                        mime="image/tiff"
                    )
            else:
                st.error("No se pudo interpretar el archivo vectorial del área de estudio.")
        except Exception as e:
            registrar_log(f"Error: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
    else:
        st.warning("Por favor, sube el archivo con el perímetro de tu área de estudio.")
