import streamlit as st
import os
import time
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
import tempfile
import pystac_client
import planetary_computer
from pyproj import Transformer

st.set_page_config(page_title="Syntro Academy - Descargador DEM 2.5m UTM", page_icon="🛰️", layout="centered")

# Estilo visual moderno y limpio (Estilo 3D / Oscuro de Syntro)
st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    </style>
""", unsafe_allow_html=True)

# Encabezado institucional
col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.write("🛰️")
with col_title:
    st.title("SYNTRO - DESCARGADOR DEM 2.5M (UTM)")
    st.markdown("### Configuración Directa por Coordenadas UTM")

st.info("Ingresa los parámetros de tu Zona UTM y los límites en metros (Bounding Box) de tu área de estudio para procesar y descargar el DEM.")

# Configuración de Coordenadas UTM Directas
st.markdown("#### 📐 Parámetros de Coordenadas UTM")
col1, col2 = st.columns(2)
with col1:
    utm_zone = st.number_input("Zona UTM", min_value=1, max_value=60, value=18, step=1, help="Ejemplo: Zulia / Venezuela se ubica típicamente en la zona 18 o 19.")
with col2:
    hemisphere = st.selectbox("Hemisferio", ["Norte", "Sur"], index=0)

hemisphere_code = "north" if hemisphere == "Norte" else "south"

# Definir EPSG UTM
# Zonas norte: 32601-32660, Zonas sur: 32701-32760
epsg_utm = (32600 + utm_zone) if hemisphere_code == "north" else (32700 + utm_zone)

st.markdown(f"**Sistema de Referencia Activo:** `EPSG:{epsg_utm}` (UTM Zona {utm_zone} {hemisphere})")

st.markdown("---")
st.markdown("#### 📍 Límites del Área de Estudio (Coordenadas en Metros - UTM)")
col3, col4 = st.columns(2)
with col3:
    xmin = st.number_input("Coordenada Este Mínima (Xmin / Metros)", value=200000.0, format="%.2f")
    ymin = st.number_input("Coordenada Norte Mínima (Ymin / Metros)", value=1100000.0, format="%.2f")
with col4:
    xmax = st.number_input("Coordenada Este Máxima (Xmax / Metros)", value=210000.0, format="%.2f")
    ymax = st.number_input("Coordenada Norte Máxima (Ymax / Metros)", value=1110000.0, format="%.2f")

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
if st.button("🚀 PROCESAR Y DESCARGAR DEM 2.5M (.tif)", type="primary"):
    if xmin >= xmax or ymin >= ymax:
        st.error("Error en los límites: Los valores máximos deben ser mayores que los mínimos.")
    else:
        logs_history.clear()
        progress_bar.progress(15)
        status_label.text("⏱️ Transformando coordenadas UTM a Geográficas (WGS84)...")
        registrar_log(f"Iniciando procesamiento con EPSG:{epsg_utm}...")
        
        try:
            # Transformador de UTM a WGS84 para buscar la tesela satelital global
            transformer_to_wgs84 = Transformer.from_crs(f"EPSG:{epsg_utm}", "EPSG:4326", always_xy=True)
            
            # Esquinas del rectángulo UTM transformadas a grados decimales
            lon_min, lat_min = transformer_to_wgs84.transform(xmin, ymin)
            lon_max, lat_max = transformer_to_wgs84.transform(xmax, ymax)
            
            # Asegurar orden correcto de bounding box geográfica [west, south, east, north]
            wgs84_bbox = [
                min(lon_min, lon_max),
                min(lat_min, lat_max),
                max(lon_min, lon_max),
                max(lat_min, lat_max)
            ]
            
            progress_bar.progress(35)
            registrar_log(f"BBox WGS84 calculada: {[round(c, 4) for c in wgs84_bbox]}")
            registrar_log("Conectando con Microsoft Planetary Computer (Copernicus DEM 30m)...")
            
            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=planetary_computer.sign_inplace,
            )
            
            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=wgs84_bbox
            )
            
            items = list(search.item_collection())
            if not items:
                raise Exception("No se encontraron teselas DEM para la extensión geográfica especificada.")
            
            registrar_log("Tesela encontrada. Extrayendo y reescalando a 2.5 metros (.tif)...")
            progress_bar.progress(65)
            
            dem_url = items[0].assets["data"].href
            temp_dir = tempfile.mkdtemp()
            output_file = os.path.join(temp_dir, f"DEM_UTM_{utm_zone}_{hemisphere.upper()}_2.5m.tif")
            
            # Definir dimensiones y resolución de salida exactas a 2.5m en metros UTM
            res = 2.5
            width = int(round((xmax - xmin) / res))
            height = int(round((ymax - ymin) / res))
            
            if width <= 0 or height <= 0:
                raise Exception("Las dimensiones calculadas son inválidas. Revisa las coordenadas ingresadas.")
            
            transform_25m = from_bounds(xmin, ymin, xmax, ymax, width, height)
            reprojected_data = np.zeros((1, height, width), dtype=np.float32)
            
            with rasterio.open(dem_url) as src:
                rasterio.warp.reproject(
                    source=rasterio.band(src, 1),
                    destination=reprojected_data[0],
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform_25m,
                    dst_crs=f"EPSG:{epsg_utm}",
                    resampling=Resampling.bilinear,
                    src_nodata=-9999.0,
                    dst_nodata=-9999.0
                )
            
            out_meta = {
                "driver": "GTiff",
                "height": height,
                "width": width,
                "transform": transform_25m,
                "crs": f"EPSG:{epsg_utm}",
                "dtype": "float32",
                "nodata": -9999.0,
                "compress": "lzw"
            }
            
            with rasterio.open(output_file, "w", **out_meta) as dst:
                dst.write(reprojected_data[0], 1)

            progress_bar.progress(100)
            status_label.text("⏱️ ¡Proceso finalizado con éxito!")
            registrar_log("DEM proyectado y guardado correctamente en coordenadas UTM.")
            
            st.success("¡El DEM en formato TIF (.tif) a 2.5m con referencia UTM está listo para descargar!")
            
            with open(output_file, "rb") as f:
                st.download_button(
                    "📥 Descargar DEM 2.5m (.tif)", 
                    f, 
                    file_name=f"DEM_UTM_Z{utm_zone}_{hemisphere.upper()}_2.5m.tif", 
                    mime="image/tiff"
                )
                
        except Exception as e:
            registrar_log(f"Error: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
