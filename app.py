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
from rasterio.mask import mask
from rasterio.windows import from_bounds
from rasterio.merge import merge
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
import requests
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM Dinámico Global", page_icon="🛰️", layout="centered")

st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #3498db, #2980b9);
        color: white;
        border: none;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3), 0 1px 3px rgba(0,0,0,0.2);
        transition: all 0.2s ease;
    }
    div.stButton > button:first-child:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 8px rgba(0,0,0,0.4), 0 3px 5px rgba(0,0,0,0.2);
    }
    </style>
""", unsafe_allow_html=True)

col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.markdown(
            "<div style='width:90px;height:90px;background:linear-gradient(135deg,#3498db,#2c3e50);"
            "border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:36px;"
            "box-shadow: 0 8px 16px rgba(0,0,0,0.4);'>🛰️</div>",
            unsafe_allow_html=True
        )
with col_title:
    st.title("SYNTRO - DEM DINÁMICO GLOBAL")
    st.markdown("### Mosaico Satelital Continuo y Recorte Poligonal")

st.info("Sube tu archivo vectorial (KML, KMZ, Shapefile o GeoJSON). El sistema detectará y fusionará automáticamente todas las teselas necesarias sin cortes ni líneas divisorias, generando el ráster rectangular y el análisis poligonal exacto.")

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
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=160)

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

if st.button("🚀 PROCESAR Y FUSIONAR TESELAS DEM", type="primary"):
    if uploaded_file:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(10)
        status_label.text("⏱ Analizando archivo y extrayendo vértices...")
        registrar_log(f"Archivo recibido: {uploaded_file.name}")

        temp_dir = tempfile.mkdtemp()
        
        try:
            file_extension = uploaded_file.name.split('.')[-1].lower()
            input_path = os.path.join(temp_dir, uploaded_file.name)
            
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            todas_coordenadas = []
            lista_poligonos_shapely = []

            # 1. GeoJSON / JSON
            if file_extension in ['geojson', 'json']:
                registrar_log("Procesando formato GeoJSON...")
                with open(input_path, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                
                features = data.get("features", [data] if "geometry" in data else [])
                for feat in features:
                    geom = feat.get("geometry") if "geometry" in feat else feat
                    if geom:
                        coords_geom = extraer_coordenadas_de_geometria(geom)
                        todas_coordenadas.extend(coords_geom)
                        if len(coords_geom) >= 3:
                            p = Polygon(coords_geom)
                            if p.is_valid:
                                lista_poligonos_shapely.append(p)

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
                
                registrar_log("Leyendo geometrías del Shapefile...")
                sf = shapefile.Reader(shp_path)
                for shape_obj in sf.shapes():
                    pts = [(pt[0], pt[1]) for pt in shape_obj.points]
                    todas_coordenadas.extend(pts)
                    if len(pts) >= 3:
                        p = Polygon(pts)
                        if p.is_valid:
                            lista_poligonos_shapely.append(p)

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
                        poly_pts = []
                        for tuple_str in coords_text.split():
                            parts = tuple_str.split(',')
                            if len(parts) >= 2:
                                try:
                                    lon, lat = float(parts[0]), float(parts[1])
                                    todas_coordenadas.append((lon, lat))
                                    poly_pts.append((lon, lat))
                                except ValueError:
                                    continue
                        if len(poly_pts) >= 3:
                            p = Polygon(poly_pts)
                            if p.is_valid:
                                lista_poligonos_shapely.append(p)
            else:
                raise Exception("Formato de archivo no soportado.")

            if not todas_coordenadas:
                raise Exception("No se pudieron extraer coordenadas válidas del archivo.")

            # Crear geometría unificada
            if lista_poligonos_shapely:
                if len(lista_poligonos_shapely) == 1:
                    geometria_recorte = lista_poligonos_shapely[0]
                else:
                    geometria_recorte = lista_poligonos_shapely[0]
                    for poly in lista_poligonos_shapely[1:]:
                        geometria_recorte = geometria_recorte.union(poly)
            else:
                geometria_recorte = Polygon(todas_coordenadas).convex_hull

            geometrias_geojson = [geometria_recorte.__geo_interface__]

            # Bounding Box global para la extensión rectangular
            lons = [c[0] for c in todas_coordenadas]
            lats = [c[1] for c in todas_coordenadas]
            west, south, east, north = min(lons), min(lats), max(lons), max(lats)

            registrar_log(f"Bounding Box Rectangular -> O: {west:.4f}, S: {south:.4f}, E: {east:.4f}, N: {north:.4f}")
            progress_bar.progress(30)

            # Conexión STAC Planetary Computer (Global)
            registrar_log("Conectando con Microsoft Planetary Computer (Copernicus DEM 30m)...")
            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=planetary_computer.sign_inplace,
            )

            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=[west, south, east, north],
                limit=10
            )

            items = list(search.item_collection())
            if not items:
                raise Exception("No se encontraron teselas DEM globales para las coordenadas especificadas.")

            registrar_log(f"📦 Se localizaron {len(items)} tesela(s) satelital(es) cubriendo el área.")
            for it in items:
                registrar_log(f"  -> Tesela: {it.id}")
            progress_bar.progress(50)

            # Descargar y preparar todas las teselas encontradas para mosaico
            src_files_to_mosaic = []
            for idx, item in enumerate(items):
                url = item.assets["data"].href
                registrar_log(f"Descargando segmento [{idx+1}/{len(items)}]: {item.id}...")
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    tile_path = os.path.join(temp_dir, f"tile_{idx}.tif")
                    with open(tile_path, "wb") as tf:
                        tf.write(resp.content)
                    src_files_to_mosaic.append(rasterio.open(tile_path))

            if not src_files_to_mosaic:
                raise Exception("No se pudo descargar ninguna tesela DEM.")

            progress_bar.progress(70)
            registrar_log("Fusionando teselas en un mosaico continuo sin costuras...")

            # Realizar mosaico de todas las teselas descargadas
            mosaic_image, mosaic_transform = merge(src_files_to_mosaic)
            mosaic_meta = src_files_to_mosaic[0].meta.copy()
            mosaic_meta.update({
                "height": mosaic_image.shape[1],
                "width": mosaic_image.shape[2],
                "transform": mosaic_transform
            })

            raw_mosaic_file = os.path.join(temp_dir, "raw_mosaic.tif")
            with rasterio.open(raw_mosaic_file, "w", **mosaic_meta) as dest:
                dest.write(mosaic_image)

            # Cerrar los archivos abiertos del mosaico
            for sf_obj in src_files_to_mosaic:
                sf_obj.close()

            progress_bar.progress(80)
            registrar_log("Generando ráster rectangular continuo y máscara poligonal exacta...")

            # 1. RÁSTER RECTANGULAR CONTINUO (Sin cortes internos)
            with rasterio.open(raw_mosaic_file) as src:
                window = from_bounds(west, south, east, north, src.transform)
                window = window.round_offsets().round_shape()
                
                rect_image = src.read(window=window)
                rect_transform = rasterio.windows.transform(window, src.transform)
                
                rect_meta = src.meta.copy()
                rect_meta.update({
                    "height": rect_image.shape[1],
                    "width": rect_image.shape[2],
                    "transform": rect_transform
                })
                
                rect_dem_path = os.path.join(temp_dir, "DEM_Rectangular_Visualizacion.tif")
                with rasterio.open(rect_dem_path, "w", **rect_meta) as dest:
                    dest.write(rect_image)

                # 2. RECORTE ESTRICTO POR POLÍGONO (Para estadísticas exactas)
                out_image, out_transform = mask(src, geometrias_geojson, crop=True, nodata=np.nan)
                out_meta = src.meta.copy()
                out_meta.update({
                    "height": out_image.shape[1],
                    "width": out_image.shape[2],
                    "transform": out_transform,
                    "nodata": np.nan
                })
                
                clipped_dem_path = os.path.join(temp_dir, "DEM_Poligono_Exacto.tif")
                with rasterio.open(clipped_dem_path, "w", **out_meta) as dest:
                    dest.write(out_image)

                dem_array_poly = out_image[0]

            progress_bar.progress(95)
            registrar_log("Calculando estadísticas altitudinales estrictas dentro de la poligonal...")

            valid_pixels = dem_array_poly[np.isfinite(dem_array_poly)]

            if valid_pixels.size > 0:
                elev_min = float(np.min(valid_pixels))
                elev_max = float(np.max(valid_pixels))
                elev_range = elev_max - elev_min
            else:
                elev_min, elev_max, elev_range = 0.0, 0.0, 0.0

            registrar_log(f"Estadísticas Reales -> Mín: {elev_min:.2f} m | Máx: {elev_max:.2f} m | Rango: {elev_range:.2f} m")

            # Generar reporte de texto
            report_path = os.path.join(temp_dir, "reporte_estadisticas_dem.txt")
            with open(report_path, "w", encoding="utf-8") as rep:
                rep.write("==================================================\n")
                rep.write("  REPORTE ESTADÍSTICO DE ELEVACIÓN - SYNTRO\n")
                rep.write("==================================================\n")
                rep.write(f"Archivo de poligonal: {uploaded_file.name}\n")
                rep.write(f"Teselas fuente fusionadas: {len(items)}\n")
                rep.write(f"Elevación Mínima: {elev_min:.2f} m.s.n.m.\n")
                rep.write(f"Elevación Máxima: {elev_max:.2f} m.s.n.m.\n")
                rep.write(f"Rango Altitudinal (Desnivel): {elev_range:.2f} m\n")
                rep.write("==================================================\n")

            # Empaquetar en ZIP
            zip_output = os.path.join(temp_dir, "DEM_Syntro_Optimizado.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(rect_dem_path, arcname="DEM_Rectangular_Visualizacion.tif")
                zf.write(clipped_dem_path, arcname="DEM_Poligono_Exacto.tif")
                zf.write(report_path, arcname="reporte_estadisticas_dem.txt")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Proceso completado en {elapsed_time}s!")
            st.success("¡Mosaico continuo y recorte poligonal generado con éxito!")

            # Tarjetas de visualización de resultados
            st.markdown("### 📊 Resultados Topográficos de la Poligonal")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric(label="⛰️ Altura Mínima", value=f"{elev_min:.2f} m")
            with m2:
                st.metric(label="🏔️ Altura Máxima", value=f"{elev_max:.2f} m")
            with m3:
                st.metric(label="📏 Rango (Desnivel)", value=f"{elev_range:.2f} m")

            st.write("")
            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar Paquete (.zip con Mosaico Rectangular, Poligonal y Reporte)",
                    f_zip,
                    file_name="DEM_Syntro_Optimizado.zip",
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
