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
import numpy as np
import requests
import pystac_client
import planetary_computer
from osgeo import gdal
from pyproj import Transformer

st.set_page_config(
    title="Syntro Academy - Descargador DEM 2.5m (Píxel Fino)", 
    page_icon="🛰️", 
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
    st.title("SYNTRO - MOTOR DEM DE 2.5M (PYTHON & GDAL)")
    st.markdown("### Procesamiento Avanzado con Cálculo Automático de Zona UTM y Remuestreo Fino")

st.info("Sube tu archivo vectorial (SHP en ZIP, KML, KMZ o GeoJSON). El motor conectará con Microsoft Planetary Computer, calculará la zona UTM correspondiente, aplicará un búfer del 2% y reescalará mediante GDAL Warp a un píxel continuo de 2.5m.")

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

if st.button("🚀 INICIAR PROCESO DEM 2.5M", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(10)
        status_label.text("⏱ Analizando geometría y extrayendo vértices...")
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
                registrar_log("Leyendo formato GeoJSON...")
                with open(input_path, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                
                features = data.get("features", [data] if "geometry" in data else [])
                for feat in features:
                    geom = feat.get("geometry") if "geometry" in feat else feat
                    if geom:
                        coords_geom = extraer_coordenadas_de_geometria(geom)
                        todas_coordenadas.extend(coords_geom)

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
                    raise Exception("No se encontró ningún archivo .shp dentro del archivo .zip.")
                
                registrar_log("Extrayendo puntos del Shapefile...")
                sf = shapefile.Reader(shp_path)
                for shape_obj in sf.shapes():
                    pts = [(pt[0], pt[1]) for pt in shape_obj.points]
                    todas_coordenadas.extend(pts)

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

            # Cálculo de extensión WGS84
            lons = [c[0] for c in todas_coordenadas]
            lats = [c[1] for c in todas_coordenadas]
            west, south, east, north = min(lons), min(lats), max(lons), max(lats)

            center_lon = (west + east) / 2.0
            center_lat = (south + north) / 2.0
            
            # Cálculo automático de Zona UTM (replicando lógica de QGIS)[cite: 13]
            utm_zone = int((center_lon + 180) / 6) + 1
            hemisphere = "north" if center_lat >= 0 else "south"
            epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone

            registrar_log(f"Zona UTM calculada: EPSG:{epsg_utm} ({utm_zone}{'N' if hemisphere == 'north' else 'S'})")
            progress_bar.progress(30)

            # Conexión STAC Planetary Computer[cite: 13]
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

            registrar_log(f"Se encontraron {len(items)} teselas DEM. Preparando reescalado a 2.5m...")
            progress_bar.progress(50)

            input_urls = [item.assets["data"].href for item in items]
            output_dem_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")

            # Transformación de límites a UTM y aplicación de búfer del 2%[cite: 13]
            transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg_utm}", always_xy=True)
            xmin_utm, ymin_utm = transformer.transform(west, south)
            xmax_utm, ymax_utm = transformer.transform(east, north)

            x_buf = (xmax_utm - xmin_utm) * 0.02
            y_buf = (ymax_utm - ymin_utm) * 0.02

            xmin = xmin_utm - x_buf
            xmax = xmax_utm + x_buf
            ymin = ymin_utm - y_buf
            ymax = ymax_utm + y_buf

            progress_bar.progress(70)
            registrar_log("Ejecutando GDAL Warp (resolución de píxel: 2.5m con interpolación bilineal)...")

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

            result_ds = gdal.Warp(output_dem_file, input_urls, options=warp_options)
            if result_ds is None:
                raise Exception("Falló la ejecución de gdal.Warp para generar el DEM de 2.5m.")
            result_ds = None

            progress_bar.progress(90)
            registrar_log(f"Archivo generado correctamente: {output_dem_file}")

            # Lectura de estadísticas topográficas
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
                rep.write(f"Proyección CRS: EPSG:{epsg_utm}\n")
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
                st.metric(label="🏔️ Altura Máxima", value=f"{elev_max:.2f} m")
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
