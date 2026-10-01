import streamlit as st
import os
import time
import traceback
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

st.markdown("""
    <style>
    .main { background-color: #1e1e24; color: #ffffff; }
    .stApp { background-color: #1e1e24; }
    h1, h2, h3 { color: #3498db !important; }
    </style>
""", unsafe_allow_html=True)

col_logo, col_title = st.columns([1, 4])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=90)
    else:
        st.markdown(
            "<div style='width:90px;height:90px;background:linear-gradient(135deg,#3498db,#2c3e50);"
            "border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:36px;'>🛰️</div>",
            unsafe_allow_html=True
        )
with col_title:
    st.title("SYNTRO - DESCARGADOR DEM 2.5M")
    st.markdown("### Extracción Automática por Archivo Perimetral")

st.info("Sube el perímetro exacto de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para procesar y descargar el DEM recortado.")

uploaded_vector = st.file_uploader(
    "Perímetro del Área de Estudio (GeoJSON, KML, KMZ, SHP en .zip)",
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

if st.button("🚀 PROCESAR Y DESCARGAR DEM 2.5M (.tif)", type="primary"):
    if uploaded_vector:
        logs_history.clear()
        start_time = time.time()

        progress_bar.progress(10)
        status_label.text("⏱ Leyendo límites geográficos...")
        registrar_log("Cargando archivo vectorial y validando CRS...")

        try:
            temp_dir = tempfile.mkdtemp()
            vector_path = os.path.join(temp_dir, uploaded_vector.name)
            with open(vector_path, "wb") as f:
                f.write(uploaded_vector.getbuffer())

            # ============ LECTURA VECTORIAL ROBUSTA ============
            vector_gdf = None
            ext = uploaded_vector.name.split('.')[-1].lower()

            if ext in ["geojson", "json"]:
                vector_gdf = gpd.read_file(vector_path)

            elif ext in ["kml", "kmz"]:
                try:
                    import fiona
                    fiona.drvsupport.supported_drivers['KML'] = 'rw'
                    fiona.drvsupport.supported_drivers['KMZ'] = 'rw'
                    fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
                except Exception:
                    pass
                try:
                    vector_gdf = gpd.read_file(vector_path, driver="KML")
                except Exception:
                    try:
                        vector_gdf = gpd.read_file(vector_path, driver="LIBKML")
                    except Exception:
                        from osgeo import ogr
                        ogr.UseExceptions()
                        ds = ogr.Open(vector_path)
                        layer = ds.GetLayer(0)
                        features = []
                        for feat in layer:
                            geom = feat.GetGeometryRef()
                            if geom is not None:
                                features.append(shapely.wkt.loads(geom.ExportToWkt()))
                        ds = None
                        vector_gdf = gpd.GeoDataFrame(geometry=features, crs="EPSG:4326")

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
                raise Exception("El archivo vectorial está vacío o no se pudo interpretar.")

            vector_gdf = vector_gdf[vector_gdf.geometry.notnull()].copy()

            # Asegurar asignación inicial WGS84 si carece de CRS
            if vector_gdf.crs is None:
                vector_gdf.set_crs("EPSG:4326", inplace=True)
            else:
                vector_gdf = vector_gdf.to_crs("EPSG:4326")

            registrar_log(f"✅ Perímetro cargado correctamente: {len(vector_gdf)} feature(s)")
            progress_bar.progress(30)

            # Obtener límites exactos en WGS84 para la consulta STAC
            west, south, east, north = vector_gdf.total_bounds
            registrar_log(f"Extensión WGS84 -> Oeste: {west:.5f}, Sur: {south:.5f}, Este: {east:.5f}, Norte: {north:.5f}")

            # Calcular Zona UTM automática basada en el centroide
            center_lon = (west + east) / 2.0
            center_lat = (south + north) / 2.0
            utm_zone = int((center_lon + 180) / 6) + 1
            hemisphere = "north" if center_lat >= 0 else "south"
            epsg_utm = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone

            registrar_log(f"Zona UTM asignada: EPSG:{epsg_utm} (Zona {utm_zone} {hemisphere.upper()})")

            # Reproyectar el vector a UTM para operaciones métricas precisas
            vector_utm = vector_gdf.to_crs(f"EPSG:{epsg_utm}")

            progress_bar.progress(50)
            registrar_log("Conectando con Microsoft Planetary Computer (Copernicus DEM)...")

            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=planetary_computer.sign_inplace,
            )

            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=[west, south, east, north],
                limit=50
            )

            items = list(search.item_collection())
            if not items:
                raise Exception("No se encontraron teselas DEM para las coordenadas indicadas.")

            registrar_log(f"📦 Teselas satelitales obtenidas: {len(items)}")
            for it in items:
                registrar_log(f"   • {it.id}")

            progress_bar.progress(70)
            output_file = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.tif")

            minx, miny, maxx, maxy = vector_utm.total_bounds
            buffer_m = 150.0  # Buffer de seguridad para evitar bordes vacíos
            b_minx, b_miny = minx - buffer_m, miny - buffer_m
            b_maxx, b_maxy = maxx + buffer_m, maxy + buffer_m

            res = 2.5
            width = int(round((b_maxx - b_minx) / res))
            height = int(round((b_maxy - b_miny) / res))
            transform_25m = from_bounds(b_minx, b_miny, b_maxx, b_maxy, width, height)

            reprojected_data = np.full((1, height, width), -9999.0, dtype=np.float32)

            registrar_log(f"🌐 Muestreando teselas a resolución de 2.5m...")

            for idx, item in enumerate(items):
                try:
                    dem_url = item.assets["data"].href
                    with rasterio.open(dem_url) as src:
                        src_nodata = src.nodata if src.nodata is not None else -9999.0
                        tile_data = np.full((height, width), -9999.0, dtype=np.float32)

                        rasterio.warp.reproject(
                            source=rasterio.band(src, 1),
                            destination=tile_data,
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=transform_25m,
                            dst_crs=f"EPSG:{epsg_utm}",
                            resampling=Resampling.cubic,
                            src_nodata=src_nodata,
                            dst_nodata=-9999.0
                        )

                        valid_tile = (tile_data > -500.0) & (tile_data < 9000.0)
                        reprojected_data[0][valid_tile] = tile_data[valid_tile]
                except Exception as e_tile:
                    registrar_log(f"⚠️ Aviso en tesela {item.id}: {e_tile}")
                    continue

            temp_dem_path = os.path.join(temp_dir, "temp_reprojected.tif")
            with rasterio.open(
                temp_dem_path, "w", driver="GTiff",
                height=height, width=width, count=1,
                dtype="float32", crs=f"EPSG:{epsg_utm}",
                transform=transform_25m, nodata=-9999.0,
                compress="lzw", tiled=True
            ) as dst:
                dst.write(reprojected_data)

            progress_bar.progress(85)
            registrar_log("Aplicando recorte estricto por geometría perimetral...")

            geom_utm = [shapely.geometry.mapping(g) for g in vector_utm.geometry]

            with rasterio.open(temp_dem_path) as src:
                out_image, out_transform = mask(
                    src, geom_utm, crop=True,
                    all_touched=False,
                    filled=True,
                    nodata=-9999.0
                )

                # Filtrar valores fuera de rango físico lógico
                mask_bad = (out_image[0] < -500.0) | (out_image[0] > 9000.0)
                out_image[0][mask_bad] = np.float32(-9999.0)

                valid_pixels = out_image[0][
                    (out_image[0] > -500.0) & (out_image[0] < 9000.0)
                ]

                if valid_pixels.size == 0:
                    raise Exception("No se encontraron píxeles de elevación válidos dentro del polígono. Verifica que el polígono esté sobre tierra firme.")

                min_elev = float(np.min(valid_pixels))
                max_elev = float(np.max(valid_pixels))
                mean_elev = float(np.mean(valid_pixels))

                registrar_log("📊 Estadísticas reales capturadas:")
                registrar_log(f"   • Mínimo : {min_elev:.2f} m")
                registrar_log(f"   • Máximo : {max_elev:.2f} m")
                registrar_log(f"   • Promedio: {mean_elev:.2f} m")
                registrar_log(f"   • Píxeles válidos: {valid_pixels.size:,}")

                with rasterio.open(
                    output_file, "w", driver="GTiff",
                    height=out_image.shape[1],
                    width=out_image.shape[2],
                    count=1, dtype="float32",
                    crs=f"EPSG:{epsg_utm}",
                    transform=out_transform,
                    nodata=-9999.0,
                    compress="lzw",
                    tiled=True
                ) as dst_out:
                    dst_out.write(out_image)
                    dst_out.set_band_description(1, "Elevacion_m")

            # Generar archivo de estilo QML automático con el rango real capturado
            qml_path = output_file.replace(".tif", ".qml")
            qml_content = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="AllStyleCategories">
  <pipe>
    <rasterrenderer type="singlebandpseudocolor" opacity="1" alphaBand="-1" band="1">
      <rasterTransparency>
        <singleValuePixelList>
          <pixelListEntry min="-9999" max="-9999" percentTransparent="100"/>
        </singleValuePixelList>
      </rasterTransparency>
      <minMaxOrigin>
        <limits>MinMax</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Exact</statAccuracy>
      </minMaxOrigin>
      <rastershader>
        <colorrampshader classificationMode="2" colorrampType="INTERPOLATED" clip="0">
          <item value="{min_elev}" label="{min_elev:.2f} m" color="#2b83ba" alpha="255"/>
          <item value="{(min_elev + max_elev) / 2}" label="{(min_elev + max_elev) / 2:.2f} m" color="#ffffbf" alpha="255"/>
          <item value="{max_elev}" label="{max_elev:.2f} m" color="#d7191c" alpha="255"/>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
  </pipe>
</qgis>
"""
            with open(qml_path, "w", encoding="utf-8") as f_qml:
                f_qml.write(qml_content)

            zip_output = os.path.join(temp_dir, "DEM_Real_2.5m_Syntro.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_file, arcname="DEM_Real_2.5m_Syntro.tif")
                zf.write(qml_path, arcname="DEM_Real_2.5m_Syntro.qml")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Completado en {elapsed_time}s!")
            st.success("¡DEM procesado con éxito!")

            col1, col2, col3 = st.columns(3)
            col1.metric("Elev. Mínima", f"{min_elev:.2f} m")
            col2.metric("Elev. Máxima", f"{max_elev:.2f} m")
            col3.metric("Rango", f"{max_elev - min_elev:.2f} m")

            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar DEM + Estilo de Color (.zip)",
                    f_zip,
                    file_name="DEM_Real_2.5m_Syntro.zip",
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
        st.warning("Sube el perímetro antes de procesar.")
