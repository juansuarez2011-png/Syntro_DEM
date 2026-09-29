import streamlit as st
import os
import time
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
import tempfile
import zipfile
import shapely.geometry
import pystac_client
import planetary_computer
from osgeo import gdal

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
    st.markdown("### Extracción Automática por Archivo Perimetral (Motor GDAL)")

st.info("Sube el perímetro de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para procesar con GDAL Warp y descargar el DEM a 2.5m en formato .tif.")

# Archivo Geográfico (Entrada principal)
uploaded_vector = st.file_uploader(
    "Perímetro del Área de Estudio (GeoJSON, KML, KMZ, SHP en .zip)", 
    type=["geojson", "json", "kml", "kmz", "zip"]
)

# Contenedor de logs, barra de progreso y temporizador
log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()

logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=160)

# Botón principal de ejecución
if st.button("🚀 PROCESAR Y DESCARGAR DEM 2.5M (.tif)", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        start_time = time.time()
        
        progress_bar.progress(15)
        status_label.text("⏱️ Leyendo límites del área de estudio...")
        registrar_log("Cargando archivo vectorial subido por el usuario...")
        
        try:
            temp_dir = tempfile.mkdtemp()
            vector_path = os.path.join(temp_dir, uploaded_vector.name)
            with open(vector_path, "wb") as f:
                f.write(uploaded_vector.getbuffer())
                
            # Leer formato vectorial según extensión
            vector_gdf = None
            ext = uploaded_vector.name.split('.')[-1].lower()
            if ext in ["geojson", "json", "kml", "kmz"]:
                if ext == "kml":
                    try:
                        import fiona
                        fiona.drvsupport.supported_drivers['KML'] = 'rw'
                    except:
                        pass
                vector_gdf = gpd.read_file(vector_path)
            elif ext == "zip":
                with zipfile.ZipFile(vector_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                shp_files = [os.path.join(temp_dir, root, f) for root, dirs, files in os.walk(temp_dir) for f in files if f.endswith('.shp')]
                if shp_files:
                    vector_gdf = gpd.read_file(shp_files[0])
            
            if vector_gdf is not None and not vector_gdf.empty:
                progress_bar.progress(35)
                
                # Garantizar CRS de origen y transformar a WGS84 para búsqueda STAC
                if vector_gdf.crs is None:
                    vector_gdf.set_crs("EPSG:4326", inplace=True)
                
                vector_wgs84 = vector_gdf.to_crs("EPSG:4326")
                bounds = vector_wgs84.total_bounds
                west, south, east, north = bounds
                
                # Calcular zona UTM automática basada en el centroide del polígono
                center_lon = (west + east) / 2.0
                center_lat = (south + north) / 2.0
                utm_zone = int((center_lon + 180) / 6) + 1
                hemisphere = "north" if center_lat >= 0 else "south"
                epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone
                
                registrar_log(f"Zona UTM calculada automáticamente: EPSG:{epsg_utm} (Zona {utm_zone} {hemisphere.upper()})")
                
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
                    raise Exception("No se encontraron teselas DEM para la extensión geográfica especificada.")
                
                registrar_log(f"Se encontraron {len(items)} teselas. Preparando motor GDAL Warp...")
                progress_bar.progress(70)
                
                input_urls = [item.assets["data"].href for item in items]
                output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")
                
                # Transformar perimetral a UTM para calcular límites con un pequeño búfer
                vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")
                extent_utm = vector_utm.total_bounds  # minx, miny, maxx, maxy
                
                x_buf = (extent_utm[2] - extent_utm[0]) * 0.02
                y_buf = (extent_utm[3] - extent_utm[1]) * 0.02
                
                xmin = extent_utm[0] - x_buf
                xmax = extent_utm[2] + x_buf
                ymin = extent_utm[1] - y_buf
                ymax = extent_utm[3] + y_buf
                
                registrar_log("Ejecutando motor GDAL Warp (remuestreo bilinear a píxel de 2.5m)...")
                
                # Opciones idénticas al script de QGIS que me pasaste
                warp_options = gdal.WarpOptions(
                    format='GTiff',
                    dstSRS=f"EPSG:{epsg_utm}",
                    xRes=2.5,
                    yRes=2.5,
                    resampleAlg=gdal.GRA_Bilinear,
                    outputBounds=[xmin, ymin, xmax, ymax],
                    warpMemoryLimit=512 * 1024 * 1024,
                    multithread=True,
                    creationOptions=["COMPRESS=DEFLATE", "TILED=YES"]
                )
                
                result_ds = gdal.Warp(output_file, input_urls, options=warp_options)
                if result_ds is None:
                    raise Exception("Falló la ejecución de gdal.Warp para generar el DEM.")
                result_ds = None  # Cierra y guarda el archivo en disco
                
                elapsed_time = round(time.time() - start_time, 2)
                progress_bar.progress(100)
                status_label.text(f"⏱ ¡Proceso completado en {elapsed_time} segundos!")
                registrar_log(f"DEM generado exitosamente mediante GDAL Warp (EPSG:{epsg_utm}).")
                
                st.success("¡El DEM de elevación real a 2.5m está listo para descargar!")
                
                with open(output_file, "rb") as f:
                    st.download_button(
                        "📥 Descargar DEM 2.5m (.tif)", 
                        f, 
                        file_name="DEM_Real_2.5m_Syntro.tif", 
                        mime="image/tiff"
                    )
            else:
                st.error("El archivo vectorial subido está vacío o no se pudo interpretar correctamente.")
        except Exception as e:
            registrar_log(f"Error crítico: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
    else:
        st.warning("Por favor, sube el archivo con el perímetro de tu área de estudio antes de procesar.")
