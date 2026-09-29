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
    st.markdown("### Extracción Automática por Archivo Perimetral")

st.info("Sube el perímetro de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para procesar de forma segura y descargar el DEM en formato .tif.")

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
        
        progress_bar.progress(10)
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
                progress_bar.progress(30)
                
                # Garantizar CRS de origen y transformar a WGS84
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
                
                registrar_log("Tesela encontrada. Realizando recorte perimetral inicial en origen...")
                progress_bar.progress(70)
                
                dem_url = items[0].assets["data"].href
                output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")
                
                geom_wgs84 = [shapely.geometry.mapping(g) for g in vector_wgs84.geometry]
                
                # 1. Recortar directamente el raster original en WGS84 usando la máscara estricta
                with rasterio.open(dem_url) as src:
                    out_image, out_transform = mask(
                        src, 
                        geom_wgs84, 
                        crop=True, 
                        all_touched=True,
                        filled=True,
                        nodata=-9999.0
                    )
                    src_crs = src.crs
                
                # Limpiar cualquier valor anómalo del recorte WGS84
                out_image[out_image < -500.0] = -9999.0
                
                # Guardar temporalmente este recorte puro en WGS84
                temp_wgs84_path = os.path.join(temp_dir, "temp_clipped_wgs84.tif")
                with rasterio.open(
                    temp_wgs84_path, "w",
                    driver="GTiff",
                    height=out_image.shape[1],
                    width=out_image.shape[2],
                    count=1,
                    dtype="float32",
                    crs=src_crs,
                    transform=out_transform,
                    nodata=-9999.0
                ) as dst_wgs:
                    dst_wgs.write(out_image)
                
                registrar_log("Reproyectando y reescalando a la malla métrica exacta de 2.5 metros en UTM...")
                progress_bar.progress(85)
                
                # 2. Calcular límites métricos exactos del perimetral en UTM
                vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")
                minx, miny, maxx, maxy = vector_utm.total_bounds
                
                res = 2.5
                width = int(round((maxx - minx) / res))
                height = int(round((maxy - miny) / res))
                transform_25m = from_bounds(minx, miny, maxx, maxy, width, height)
                
                reprojected_data = np.full((1, height, width), -9999.0, dtype=np.float32)
                
                with rasterio.open(temp_wgs84_path) as src_clip:
                    rasterio.warp.reproject(
                        source=rasterio.band(src_clip, 1),
                        destination=reprojected_data[0],
                        src_transform=src_clip.transform,
                        src_crs=src_clip.crs,
                        dst_transform=transform_25m,
                        dst_crs=f"EPSG:{epsg_utm}",
                        resampling=Resampling.bilinear,
                        src_nodata=-9999.0,
                        dst_nodata=-9999.0
                    )
                
                # Validación estricta de valores válidos
                reprojected_data[reprojected_data < -500.0] = -9999.0
                
                # Calcular estadísticas internas reales para que GeoLibre las lea de golpe
                valid_mask = (reprojected_data[0] != -9999.0)
                if np.any(valid_mask):
                    real_min = float(np.min(reprojected_data[0][valid_mask]))
                    real_max = float(np.max(reprojected_data[0][valid_mask]))
                    registrar_log(f"Elevación calculada en GeoLibre -> Mínima: {real_min:.2f}m | Máxima: {real_max:.2f}m")
                else:
                    real_min, real_max = 0.0, 100.0

                # 3. Escritura final del GeoTIFF con estadísticas incrustadas para GeoLibre
                with rasterio.open(
                    output_file, 
                    "w", 
                    driver="GTiff",
                    height=height,
                    width=width,
                    count=1,
                    dtype="float32",
                    crs=f"EPSG:{epsg_utm}",
                    transform=transform_25m,
                    nodata=-9999.0,
                    compress="lzw"
                ) as dst_out:
                    dst_out.write(reprojected_data)
                    # Inyectar estadísticas directas en la banda para que el visor las tome por defecto
                    dst_out.update_tags(1, STATISTICS_MINIMUM=str(real_min), STATISTICS_MAXIMUM=str(real_max))

                elapsed_time = round(time.time() - start_time, 2)
                progress_bar.progress(100)
                status_label.text(f"⏱ ¡Proceso completado en {elapsed_time} segundos!")
                registrar_log(f"DEM de 2.5m optimizado para GeoLibre (EPSG:{epsg_utm}).")
                
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
