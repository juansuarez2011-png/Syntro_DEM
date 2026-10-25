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
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.merge import merge
import numpy as np
import requests
import pystac_client
import planetary_computer
from pyproj import Transformer, CRS
import geopandas as gpd
import shapely.geometry
import re

st.set_page_config(
    page_title="Syntro Academy - Descargador DEM 2.5m (Píxel Fino)", 
    page_icon="🛰", 
    layout="wide"
)

st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #2ecc71, #27ae60);
        color: white;
        border: none;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3), 0 1px 3px rgba(0,0,0,0.2);
        transition: all 0.2s ease;
        font-weight: bold;
        font-size: 15px;
        padding: 10px 20px;
    }
    div.stButton > button:first-child:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 8px rgba(0,0,0,0.4), 0 3px 5px rgba(0,0,0,0.2);
    }
    </style>
""", unsafe_allow_html=True)

col_logo, col_title = st.columns([1, 5])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=95)
    else:
        st.markdown(
            "<div style='width:95px;height:95px;background:linear-gradient(135deg,#3498db,#2c3e50);"
            "border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:40px;"
            "box-shadow: 0 8px 16px rgba(0,0,0,0.4);'>🛰️</div>",
            unsafe_allow_html=True
        )
with col_title:
    st.title("SYNTRO - MOTOR DEM DE 2.5M (PYTHON & RASTERIO)")
    st.markdown("### Procesamiento Avanzado con Detección Automática de CRS y Remuestreo Fino")

st.info("Sube tu archivo vectorial (SHP en ZIP, KML, KMZ, GeoJSON o JSON). El motor detectará automáticamente el sistema de coordenadas (CRS), incluso si está en metros UTM, consultará Microsoft Planetary Computer y reescalará a un píxel continuo de 2.5m.")

uploaded_file = st.file_uploader(
    "Área de Estudio (Poligonal)",
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

if st.button("🚀 INICIAR PROCESO DEM 2.5M", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(10)
        status_label.text("⏱ Analizando geometría y detectando CRS...")
        registrar_log(f"Archivo recibido: {uploaded_file.name}")

        temp_dir = tempfile.mkdtemp()
        
        try:
            file_extension = uploaded_file.name.split('.')[-1].lower()
            input_path = os.path.join(temp_dir, uploaded_file.name)
            
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            gdf = None

            # 1. GeoJSON / JSON / Zipped Shapefiles con GeoPandas
            if file_extension in ['geojson', 'json', 'zip', 'gpkg']:
                if file_extension == 'zip':
                    registrar_log("Descomprimiendo archivo ZIP...")
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
                    registrar_log("Leyendo Shapefile con GeoPandas...")
                    gdf = gpd.read_file(shp_path)
                else:
                    registrar_log(f"Leyendo formato {file_extension.upper()} con GeoPandas...")
                    gdf = gpd.read_file(input_path)

            # 2. KML / KMZ
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
                polygons_coords = []
                for elem in root.iter():
                    if elem.tag.endswith('coordinates') and elem.text:
                        coords_text = elem.text.strip()
                        ring = []
                        for tuple_str in coords_text.split():
                            parts = tuple_str.split(',')
                            if len(parts) >= 2:
                                try:
                                    lon, lat = float(parts[0]), float(parts[1])
                                    ring.append((lon, lat))
                                except ValueError:
                                    continue
                        if len(ring) >= 3:
                            polygons_coords.append(shapely.geometry.Polygon(ring))
                if polygons_coords:
                    poly_geom = shapely.geometry.MultiPolygon(polygons_coords) if len(polygons_coords) > 1 else polygons_coords[0]
                    gdf = gpd.GeoDataFrame(geometry=[poly_geom], crs="EPSG:4326")
                else:
                    raise Exception("No se pudieron extraer coordenadas válidas del archivo KML/KMZ.")
            else:
                raise Exception("Formato de archivo no soportado.")

            if gdf is None or gdf.empty:
                raise Exception("El archivo vectorial no contiene geometrías válidas.")

            # Validación y corrección dinámica de CRS si no viene declarado
            if gdf.crs is None:
                filename_upper = uploaded_file.name.upper()
                utm_zone_match = re.search(r'(?:UTM)?\s*(\d{1,2})\s*([NS])?', filename_upper)
                if utm_zone_match and utm_zone_match.group(1):
                    zone = int(utm_zone_match.group(1))
                    hemis = utm_zone_match.group(2) if utm_zone_match.group(2) else 'N'
                    epsg_inferido = 32600 + zone if hemis == 'N' else 32700 + zone
                    gdf.set_crs(epsg=epsg_inferido, inplace=True)
                    registrar_log(f"CRS no definido en metadatos. Inferido por nombre de archivo a EPSG:{epsg_inferido}")
                else:
                    bounds = gdf.total_bounds
                    if max(abs(bounds[0]), abs(bounds[2]), abs(bounds[1]), abs(bounds[3])) > 180:
                        registrar_log("Coordenadas proyectadas detectadas sin CRS. Asignando UTM 18N por defecto...")
                        gdf.set_crs(epsg=32618, inplace=True)
                    else:
                        gdf.set_crs(epsg=4326, inplace=True)

            registrar_log(f"CRS de origen detectado: {gdf.crs}")

            # Reproyección automática a WGS84 (EPSG:4326) para la consulta STAC de Planetary Computer
            gdf_wgs84 = gdf.to_crs("EPSG:4326")
            west, south, east, north = gdf_wgs84.total_bounds
            registrar_log(f"BBox WGS84 generado correctamente: West={west:.4f}, South={south:.4f}, East={east:.4f}, North={north:.4f}")

            center_lon = (west + east) / 2.0
            center_lat = (south + north) / 2.0
            
            # Cálculo automático de Zona UTM óptima para el reprocesamiento final
            utm_zone = int((center_lon + 180) / 6) + 1
            hemisphere = "north" if center_lat >= 0 else "south"
            epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone

            registrar_log(f"Zona UTM de salida calculada: EPSG:{epsg_utm} ({utm_zone}{'N' if hemisphere == 'north' else 'S'})")
            progress_bar.progress(30)

            # Conexión STAC Planetary Computer
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

            registrar_log(f"Se encontraron {len(items)} teselas DEM. Descargando...")
            progress_bar.progress(45)

            src_files_to_mosaic = []
            for idx, item in enumerate(items):
                url = item.assets["data"].href
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    tile_path = os.path.join(temp_dir, f"tile_{idx}.tif")
                    with open(tile_path, "wb") as tf:
                        tf.write(resp.content)
                    src_files_to_mosaic.append(rasterio.open(tile_path))

            if not src_files_to_mosaic:
                raise Exception("No se pudo descargar ninguna tesela DEM.")

            progress_bar.progress(60)
            registrar_log("Fusionando teselas base...")

            mosaic_image, mosaic_transform = merge(src_files_to_mosaic, method="first")
            mosaic_meta = src_files_to_mosaic[0].meta.copy()
            mosaic_meta.update({
                "height": mosaic_image.shape[1],
                "width": mosaic_image.shape[2],
                "transform": mosaic_transform
            })

            temp_mosaic_path = os.path.join(temp_dir, "mosaic_wgs84.tif")
            with rasterio.open(temp_mosaic_path, "w", **mosaic_meta) as dest:
                dest.write(mosaic_image)

            for sf_obj in src_files_to_mosaic:
                sf_obj.close()

            progress_bar.progress(75)
            registrar_log("Reescalando y proyectando a 2.5m (Rasterio Warp)...")

            dst_crs = f"EPSG:{epsg_utm}"
            output_dem_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")

            with rasterio.open(temp_mosaic_path) as src:
                transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
                xmin_utm, ymin_utm = transformer.transform(west, south)
                xmax_utm, ymax_utm = transformer.transform(east, north)

                x_buf = (xmax_utm - xmin_utm) * 0.02
                y_buf = (ymax_utm - ymin_utm) * 0.02

                xmin = xmin_utm - x_buf
                xmax = xmax_utm + x_buf
                ymin = ymin_utm - y_buf
                ymax = ymax_utm + y_buf

                width = int(np.ceil((xmax - xmin) / 2.5))
                height = int(np.ceil((ymax - ymin) / 2.5))

                dst_transform = rasterio.transform.from_bounds(xmin, ymin, xmax, ymax, width, height)

                dst_meta = src.meta.copy()
                dst_meta.update({
                    "crs": dst_crs,
                    "transform": dst_transform,
                    "width": width,
                    "height": height,
                    "compress": "deflate",
                    "tiled": True
                })

                with rasterio.open(output_dem_file, "w", **dst_meta) as dst:
                    reproject(
                        source=rasterio.band(src, 1),
                        destination=rasterio.band(dst, 1),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=dst_crs,
                        resampling=Resampling.bilinear
                    )

            progress_bar.progress(90)
            registrar_log(f"Archivo generado correctamente: {output_dem_file}")

            with rasterio.open(output_dem_file) as src:
                arr = src.read(1)
                valid_pixels = arr[np.isfinite(arr) & (arr != src.nodata)]
                elev_min = float(np.min(valid_pixels)) if valid_pixels.size > 0 else 0.0
                elev_max = float(np.max(valid_pixels)) if valid_pixels.size > 0 else 0.0
                elev_range = elev_max - elev_min

            report_path = os.path.join(temp_dir, "reporte_dem_2.5m.txt")
            with open(report_path, "w", encoding="utf-8") as rep:
                rep.write("==================================================\n")
                rep.write("  REPORTE DEM 2.5M - SYNTRO ACADEMY\n")
                rep.write("==================================================\n")
                rep.write(f"Archivo vectorial: {uploaded_file.name}\n")
                rep.write(f"Proyección CRS: {dst_crs}\n")
                rep.write(f"Resolución espacial: 2.5 m x 2.5 m\n")
                rep.write(f"Elevación Mínima: {elev_min:.2f} m.s.n.m.\n")
                rep.write(f"Elevación Máxima: {elev_max:.2f} m.s.n.m.\n")
                rep.write(f"Rango Altitudinal: {elev_range:.2f} m\n")
                rep.write("==================================================\n")

            zip_output = os.path.join(temp_dir, "DEM_Syntro_2.5m.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_dem_file, arcname="DEM_Real_2.5m_Syntro.tif")
                zf.write(report_path, arcname="reporte_dem_2.5m.txt")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Proceso completado en {elapsed_time}s!")
            st.success("¡DEM de alta resolución (2.5m) generado con éxito!")

            st.markdown("### 📊 Estadísticas Topográficas")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric(label="⛰️ Altura Mínima", value=f"{elev_min:.2f} m")
            with m2:
                st.metric(label="🏔 Altura Máxima", value=f"{elev_max:.2f} m")
            with m3:
                st.metric(label="📏 Desnivel", value=f"{elev_range:.2f} m")

            st.write("")
            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar Paquete (.zip con DEM 2.5m y Reporte)",
                    f_zip,
                    file_name="DEM_Syntro_2.5m.zip",
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
        st.warning("Por favor, sube un archivo vectorial (KML, KMZ, Shapefile o GeoJSON).")
