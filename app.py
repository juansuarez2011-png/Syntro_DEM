import streamlit as st
import os
import time
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.features import geometry_mask
import tempfile
import zipfile
import shapely.geometry
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM 2.5m", page_icon="🛰️", layout="centered")

# ============================================================
# ESTILO VISUAL
# ============================================================
st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    </style>
""", unsafe_allow_html=True)

# ============================================================
# LOGO (ruta absoluta robusta)
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
LOGO_PATH = os.path.join(BASE_DIR, "logo.png")

col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=90)
    else:
        st.write("🛰️")

with col_title:
    st.title("SYNTRO - DESCARGADOR DEM 2.5M")
    st.markdown("### Extracción Automática por Archivo Perimetral")

st.info("Sube el perímetro de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para procesar de forma segura y descargar el DEM en formato .tif.")

# ============================================================
# ENTRADAS
# ============================================================
uploaded_vector = st.file_uploader(
    "Perímetro del Área de Estudio (GeoJSON, KML, KMZ, SHP en .zip)",
    type=["geojson", "json", "kml", "kmz", "zip"]
)

normalizar = st.checkbox(
    "Normalizar elevaciones entre 0 y 1 (solo dentro del perímetro)",
    value=False,
    help="Si se activa, el DEM descargado tendrá valores entre 0 y 1 (min real = 0, max real = 1)."
)

# ============================================================
# LOG / PROGRESO
# ============================================================
log_container = st.empty()
progress_bar = st.progress(0)
status_label = st.empty()

logs_history = []

def registrar_log(mensaje):
    timestamp = time.strftime('%H:%M:%S')
    logs_history.append(f"[{timestamp}] {mensaje}")
    log_container.text_area("Registro de Actividad (Log):", "\n".join(logs_history), height=160)

# ============================================================
# BOTÓN PRINCIPAL
# ============================================================
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

            # ------------------- LECTURA VECTORIAL -------------------
            vector_gdf = None
            ext = uploaded_vector.name.split('.')[-1].lower()

            if ext in ["geojson", "json", "kml", "kmz"]:
                if ext == "kml":
                    try:
                        import fiona
                        fiona.drvsupport.supported_drivers['KML'] = 'rw'
                    except Exception:
                        pass
                vector_gdf = gpd.read_file(vector_path)
            elif ext == "zip":
                with zipfile.ZipFile(vector_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                shp_files = [
                    os.path.join(temp_dir, root, f)
                    for root, dirs, files in os.walk(temp_dir)
                    for f in files if f.endswith('.shp')
                ]
                if shp_files:
                    vector_gdf = gpd.read_file(shp_files[0])

            if vector_gdf is None or vector_gdf.empty:
                raise Exception("El archivo vectorial subido está vacío o no se pudo interpretar correctamente.")

            progress_bar.progress(30)

            if vector_gdf.crs is None:
                vector_gdf.set_crs("EPSG:4326", inplace=True)

            vector_wgs84 = vector_gdf.to_crs("EPSG:4326")
            west, south, east, north = vector_wgs84.total_bounds

            # Cálculo UTM automático
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

            registrar_log("Tesela encontrada. Leyendo banda de elevación principal...")
            progress_bar.progress(70)

            dem_url = items[0].assets["data"].href
            output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")

            vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")
            minx, miny, maxx, maxy = vector_utm.total_bounds

            buffer_m = 100.0
            b_minx, b_miny = minx - buffer_m, miny - buffer_m
            b_maxx, b_maxy = maxx + buffer_m, maxy + buffer_m

            res = 2.5
            width = int(round((b_maxx - b_minx) / res))
            height = int(round((b_maxy - b_miny) / res))
            transform_25m = from_bounds(b_minx, b_miny, b_maxx, b_maxy, width, height)

            # ============================================================
            # REPROYECCIÓN — CAMBIO CLAVE: nearest en vez de bilinear
            # ============================================================
            reprojected_data = np.full((1, height, width), -9999.0, dtype=np.float32)

            with rasterio.open(dem_url) as src:
                src_nodata = src.nodata if src.nodata is not None else -9999.0

                rasterio.warp.reproject(
                    source=rasterio.band(src, 1),
                    destination=reprojected_data[0],
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform_25m,
                    dst_crs=f"EPSG:{epsg_utm}",
                    resampling=Resampling.nearest,   # 🔥 antes era bilinear → causaba -7793.37
                    src_nodata=src_nodata,
                    dst_nodata=-9999.0
                )

            # 🔥 LIMPIEZA EXTREMA: rango terrestre válido [-500, 9000]
            mask_invalid = (reprojected_data[0] < -500.0) | (reprojected_data[0] > 9000.0)
            reprojected_data[0][mask_invalid] = np.float32(-9999.0)

            # Forzar valores nodata exactos
            reprojected_data[0] = np.where(
                reprojected_data[0] == np.float32(-9999.0),
                np.float32(-9999.0),
                reprojected_data[0]
            ).astype(np.float32)

            temp_dem_path = os.path.join(temp_dir, "temp_reprojected.tif")

            with rasterio.open(
                temp_dem_path, "w", driver="GTiff",
                height=height, width=width, count=1,
                dtype="float32", crs=f"EPSG:{epsg_utm}",
                transform=transform_25m, nodata=-9999.0,
                compress="lzw", tiled=True,
                blockxsize=256, blockysize=256
            ) as dst:
                dst.write(reprojected_data)
                dst.update_tags(nodata=-9999.0)

            registrar_log("Aplicando recorte vectorial exacto sobre la malla métrica...")
            progress_bar.progress(85)

            geom_utm = [shapely.geometry.mapping(g) for g in vector_utm.geometry]

            # ============================================================
            # RECORTE + LIMPIEZA FINAL
            # ============================================================
            with rasterio.open(temp_dem_path) as src:
                out_image, out_transform = mask(
                    src, geom_utm, crop=True,
                    all_touched=False,
                    filled=True,
                    nodata=-9999.0
                )

                # Máscara booleana estricta del interior del polígono
                interior_mask = geometry_mask(
                    geometries=geom_utm,
                    out_shape=(out_image.shape[1], out_image.shape[2]),
                    transform=out_transform,
                    invert=True,
                    all_touched=False
                )

                # Forzar nodata exacto FUERA del polígono
                out_image[0][~interior_mask] = np.float32(-9999.0)

                # Volver a limpiar valores basura DENTRO del polígono
                mask_bad = (out_image[0] < -500.0) | (out_image[0] > 9000.0)
                out_image[0][mask_bad] = np.float32(-9999.0)

                # Extraer SOLO los válidos dentro del perímetro
                valid_pixels = out_image[0][
                    interior_mask
                    & (out_image[0] > -500.0)
                    & (out_image[0] < 9000.0)
                ]

                if valid_pixels.size == 0:
                    raise Exception("No hay píxeles válidos dentro del perímetro después del recorte.")

                min_elev = float(np.min(valid_pixels))
                max_elev = float(np.max(valid_pixels))
                mean_elev = float(np.mean(valid_pixels))
                std_elev = float(np.std(valid_pixels))

                registrar_log("📊 Estadísticas dentro del perímetro:")
                registrar_log(f"   • Mínimo : {min_elev:.2f} m")
                registrar_log(f"   • Máximo : {max_elev:.2f} m")
                registrar_log(f"   • Media  : {mean_elev:.2f} m")
                registrar_log(f"   • Desv.  : {std_elev:.2f} m")
                registrar_log(f"   • Píxeles válidos: {valid_pixels.size:,}")

                # Normalización opcional
                if normalizar:
                    if max_elev > min_elev:
                        rango = max_elev - min_elev
                        interior_valid = (
                            interior_mask
                            & (out_image[0] > -500.0)
                            & (out_image[0] < 9000.0)
                        )
                        out_image[0][interior_valid] = (
                            out_image[0][interior_valid] - min_elev
                        ) / rango
                        registrar_log(f"🔁 DEM normalizado entre 0 y 1 (rango real = {rango:.2f} m).")
                        min_out, max_out = 0.0, 1.0
                    else:
                        registrar_log("⚠️ Rango nulo, no se normaliza.")
                        min_out, max_out = min_elev, max_elev
                else:
                    min_out, max_out = min_elev, max_elev

                # ============================================================
                # GUARDADO FINAL
                # ============================================================
                with rasterio.open(
                    output_file, "w", driver="GTiff",
                    height=out_image.shape[1],
                    width=out_image.shape[2],
                    count=1, dtype="float32",
                    crs=f"EPSG:{epsg_utm}",
                    transform=out_transform,
                    nodata=-9999.0,
                    compress="lzw",
                    tiled=True,
                    blockxsize=256,
                    blockysize=256
                ) as dst_out:
                    dst_out.write(out_image)
                    dst_out.set_band_description(1, "Elevación (m)")
                    dst_out.update_tags(
                        AREA_MIN=f"{min_out:.3f}",
                        AREA_MAX=f"{max_out:.3f}",
                        AREA_MEAN=f"{mean_elev:.3f}",
                        AREA_STD=f"{std_elev:.3f}",
                        NORMALIZED=str(normalizar),
                        SOURCE="Copernicus DEM GLO-30 remuestreado a 2.5m",
                        CRS=f"EPSG:{epsg_utm}",
                        UNITS="meters" if not normalizar else "normalized_0_1"
                    )

            # ============================================================
            # 🔥 SEGUNDO PASO: grabar estadísticas REALES ignorando nodata
            # Esto es lo que QGIS/GeoLibre lee para el histograma
            # ============================================================
            with rasterio.open(output_file, "r+") as dst:
                data_final = dst.read(1)
                nodata_val = dst.nodata

                validos_final = data_final[
                    (data_final != nodata_val)
                    & (data_final > -500.0)
                    & (data_final < 9000.0)
                ]

                if validos_final.size > 0:
                    stats_min = float(validos_final.min())
                    stats_max = float(validos_final.max())
                    stats_mean = float(validos_final.mean())
                    stats_std = float(validos_final.std())

                    # Tags del dataset (namespace general)
                    dst.update_tags(
                        STATISTICS_MINIMUM=f"{stats_min:.6f}",
                        STATISTICS_MAXIMUM=f"{stats_max:.6f}",
                        STATISTICS_MEAN=f"{stats_mean:.6f}",
                        STATISTICS_STDDEV=f"{stats_std:.6f}",
                        STATISTICS_VALID_PERCENT="100",
                        STATISTICS_SKIP_PIXELS="0"
                    )

                    # Tags de la banda 1 (namespace que usa GDAL/QGIS)
                    dst.update_tags(
                        1,
                        STATISTICS_MINIMUM=f"{stats_min:.6f}",
                        STATISTICS_MAXIMUM=f"{stats_max:.6f}",
                        STATISTICS_MEAN=f"{stats_mean:.6f}",
                        STATISTICS_STDDEV=f"{stats_std:.6f}",
                    )

                    registrar_log(
                        f"✅ Estadísticas grabadas en el GeoTIFF: "
                        f"min={stats_min:.2f} m | max={stats_max:.2f} m"
                    )

            # ============================================================
            # FIN
            # ============================================================
            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Proceso completado en {elapsed_time} segundos!")
            registrar_log(f"DEM exportado correctamente (EPSG:{epsg_utm}).")
            registrar_log(f"Rango final del raster: [{min_out:.2f} , {max_out:.2f}] m")

            st.success("¡El DEM de elevación real a 2.5m está listo para descargar!")

            col1, col2, col3 = st.columns(3)
            col1.metric("Elevación mínima", f"{min_elev:.2f} m")
            col2.metric("Elevación máxima", f"{max_elev:.2f} m")
            col3.metric("Rango total", f"{max_elev - min_elev:.2f} m")

            with open(output_file, "rb") as f:
                st.download_button(
                    "📥 Descargar DEM 2.5m (.tif)",
                    f,
                    file_name="DEM_Real_2.5m_Syntro.tif",
                    mime="image/tiff"
                )

        except Exception as e:
            registrar_log(f"Error crítico: {str(e)}")
            st.error(f"Ocurrió un error en el proceso: {str(e)}")
    else:
        st.warning("Por favor, sube el archivo con el perímetro de tu área de estudio antes de procesar.")
