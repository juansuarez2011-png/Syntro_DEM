import streamlit as st
import os
import time
import traceback
import tempfile
import zipfile
import numpy as np
import geopandas as gpd
import rasterio
import rasterio.mask
import pystac_client
import planetary_computer

st.set_page_config(page_title="Syntro Academy - Descargador DEM", page_icon="🛰️", layout="centered")

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
    st.title("SYNTRO - DESCARGADOR DEM")
    st.markdown("### Extracción Directa por Enmascaramiento Vectorial")

st.info("Sube el perímetro exacto de tu área de estudio (GeoJSON, KML, KMZ o Shapefile en .zip) para descargar el DEM optimizado.")

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

if st.button("🚀 PROCESAR Y DESCARGAR DEM (.tif)", type="primary"):
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
                    vector_gdf = gpd.read_file(vector_path, driver="LIBKML")

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

            if vector_gdf.crs is None:
                vector_gdf.set_crs("EPSG:4326", inplace=True)
            else:
                vector_gdf = vector_gdf.to_crs("EPSG:4326")

            registrar_log(f"✅ Perímetro cargado correctamente: {len(vector_gdf)} feature(s)")
            progress_bar.progress(30)

            west, south, east, north = vector_gdf.total_bounds
            registrar_log(f"Extensión WGS84 -> Oeste: {west:.5f}, Sur: {south:.5f}, Este: {east:.5f}, Norte: {north:.5f}")

            progress_bar.progress(45)
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
                raise Exception("No se encontraron teselas DEM para las coordenadas indicadas.")

            registrar_log(f"📦 Teselas encontradas: {len(items)}")
            
            progress_bar.progress(60)
            registrar_log("Extrayendo y recortando directamente sobre la tesela principal...")

            url = items[0].assets["data"].href
            
            with rasterio.open(url) as src:
                vector_projected = vector_gdf.to_crs(src.crs)
                geometries = [geom for geom in vector_projected.geometry]

                out_image, out_transform = rasterio.mask.mask(src, geometries, crop=True, nodata=-9999.0)
                out_meta = src.meta.copy()

            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": -9999.0,
                "compress": "deflate",
                "tiled": True
            })

            output_file = os.path.join(temp_dir, "DEM_Syntro_Recortado.tif")
            with rasterio.open(output_file, "w", **out_meta) as dest:
                dest.write(out_image)

            progress_bar.progress(85)
            registrar_log("Calculando estadísticas del DEM...")

            with rasterio.open(output_file) as src:
                raster_data = src.read(1)
                valid_pixels = raster_data[(raster_data != -9999.0) & (raster_data > -500.0) & (raster_data < 9000.0)]

                if valid_pixels.size == 0:
                    raise Exception("No se encontraron píxeles de elevación válidos dentro de la máscara.")

                min_elev = float(np.min(valid_pixels))
                max_elev = float(np.max(valid_pixels))
                mean_elev = float(np.mean(valid_pixels))

            registrar_log("📊 Estadísticas capturadas:")
            registrar_log(f"   • Mínimo : {min_elev:.2f} m")
            registrar_log(f"   • Máximo : {max_elev:.2f} m")
            registrar_log(f"   • Promedio: {mean_elev:.2f} m")

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

            zip_output = os.path.join(temp_dir, "DEM_Syntro_Recortado.zip")
            with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(output_file, arcname="DEM_Syntro_Recortado.tif")
                zf.write(qml_path, arcname="DEM_Syntro_Recortado.qml")

            elapsed_time = round(time.time() - start_time, 2)
            progress_bar.progress(100)
            status_label.text(f"⏱ ¡Completado en {elapsed_time}s!")
            st.success("¡DEM procesado y recortado con éxito!")

            col1, col2, col3 = st.columns(3)
            col1.metric("Elev. Mínima", f"{min_elev:.2f} m")
            col2.metric("Elev. Máxima", f"{max_elev:.2f} m")
            col3.metric("Rango", f"{max_elev - min_elev:.2f} m")

            with open(zip_output, "rb") as f_zip:
                st.download_button(
                    "📦 Descargar DEM + Estilo de Color (.zip)",
                    f_zip,
                    file_name="DEM_Syntro_Recortado.zip",
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
