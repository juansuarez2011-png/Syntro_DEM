import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from threading import Thread
import rasterio
from rasterio.windows import Window
from shapely.geometry import box
import geopandas as gpd
from rasterio.mask import mask

class RectangularClipApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Syntro - Recorte Raster Rectangular (Bounding Box)")
        self.root.geometry("750x600")
        self.root.minsize(700, 550)
        
        # Variables
        self.input_raster_path = tk.StringVar()
        self.output_dir_path = tk.StringVar()
        
        self.create_widgets()

    def create_widgets(self):
        # Título / Header
        header_frame = tk.Frame(self.root, bg="#1E293B", pady=10)
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text="SINTRO - RECORTE RECTANGULAR DE RASTER", fg="white", bg="#1E293B", font=("Arial", 14, "bold")).pack()

        # Frame Principal
        main_frame = tk.Frame(self.root, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Selección de Raster de Entrada
        tk.Label(main_frame, text="1. Seleccionar Archivo Raster de Entrada (.tif):", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
        
        raster_frame = tk.Frame(main_frame)
        raster_frame.grid(row=1, column=0, sticky="ew", pady=5)
        main_frame.columnconfigure(0, weight=1)
        raster_frame.columnconfigure(0, weight=1)

        tk.Entry(raster_frame, textvariable=self.input_raster_path, font=("Arial", 10)).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        tk.Button(raster_frame, text="Examinar...", command=self.select_raster, bg="#3B82F6", fg="white", font=("Arial", 9, "bold")).grid(row=0, column=1)

        # Selección de Carpeta de Salida
        tk.Label(main_frame, text="2. Seleccionar Carpeta de Destino:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w", pady=(15, 5))
        
        output_frame = tk.Frame(main_frame)
        output_frame.grid(row=3, column=0, sticky="ew", pady=5)
        output_frame.columnconfigure(0, weight=1)

        tk.Entry(output_frame, textvariable=self.output_dir_path, font=("Arial", 10)).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        tk.Button(output_frame, text="Examinar...", command=self.select_output_dir, bg="#3B82F6", fg="white", font=("Arial", 9, "bold")).grid(row=0, column=1)

        # Botón de Ejecución
        self.run_btn = tk.Button(main_frame, text="EJECUTAR RECORTE RECTANGULAR", command=self.start_process, bg="#10B981", fg="white", font=("Arial", 11, "bold"), pady=8)
        self.run_btn.grid(row=4, column=0, sticky="ew", pady=20)

        # Progreso y Temporizador
        progress_frame = tk.Frame(main_frame)
        progress_frame.grid(row=5, column=0, sticky="ew", pady=5)
        progress_frame.columnconfigure(0, weight=1)

        self.status_label = tk.Label(progress_frame, text="Estado: Esperando archivos...", font=("Arial", 9, "italic"))
        self.status_label.pack(anchor="w")

        self.timer_label = tk.Label(progress_frame, text="Tiempo transcurrido: 0.0 s", font=("Arial", 9))
        self.timer_label.pack(anchor="e")

        # Consola de Registro (Log)
        tk.Label(main_frame, text="Registro de Actividad (Log):", font=("Arial", 10, "bold")).grid(row=6, column=0, sticky="w", pady=(10, 2))
        
        self.log_area = scrolledtext.ScrolledText(main_frame, height=10, font=("Consolas", 9), bg="#0F172A", fg="#38BDF8")
        self.log_area.grid(row=7, column=0, sticky="nsew", pady=5)
        main_frame.rowconfigure(7, weight=1)

    def select_raster(self):
        filename = filedialog.askopenfilename(title="Seleccionar Raster", filetypes=[("GeoTIFF", "*.tif"), ("Todos los archivos", "*.*")])
        if filename:
            self.input_raster_path.set(filename)
            self.log(f"Archivo raster seleccionado: {filename}")

    def select_output_dir(self):
        dirname = filedialog.askdirectory(title="Seleccionar Carpeta de Destino")
        if dirname:
            self.output_dir_path.set(dirname)
            self.log(f"Carpeta de salida seleccionada: {dirname}")

    def log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)

    def start_process(self):
        raster_path = self.input_raster_path.get()
        output_dir = self.output_dir_path.get()

        if not raster_path or not os.path.exists(raster_path):
            messagebox.showerror("Error", "Por favor seleccione un archivo raster válido.")
            return
        if not output_dir or not os.path.exists(output_dir):
            messagebox.showerror("Error", "Por favor seleccione una carpeta de salida válida.")
            return

        self.run_btn.config(state=tk.DISABLED)
        Thread(target=self.run_clipping, args=(raster_path, output_dir), daemon=True).start()

    def run_clipping(self, raster_path, output_dir):
        start_time = time.time()
        self.log("Iniciando proceso de recorte rectangular (Bounding Box)...")
        self.status_label.config(text="Estado: Procesando...")

        try:
            with rasterio.open(raster_path) as src:
                bounds = src.bounds
                self.log(f"Bounding Box original detectado: Xmin={bounds.left:.2f}, Ymin={bounds.bottom:.2f}, Xmax={bounds.right:.2f}, Ymax={bounds.top:.2f}")
                
                # Definir un rectángulo central o de recorte (por ejemplo, el 60% central para simular el recorte rectangular solicitado)
                width = bounds.right - bounds.left
                height = bounds.top - bounds.bottom
                
                xmin_new = bounds.left + width * 0.2
                xmax_new = bounds.right - width * 0.2
                ymin_new = bounds.bottom + height * 0.1
                ymax_new = bounds.top - height * 0.1

                geom = [box(xmin_new, ymin_new, xmax_new, ymax_new)]
                
                self.log("Aplicando máscara rectangular sobre el raster...")
                out_image, out_transform = mask(src, geom, crop=True)
                out_meta = src.meta.copy()

                out_meta.update({
                    "driver": "GTiff",
                    "height": out_image.shape[1],
                    "width": out_image.shape[2],
                    "transform": out_transform
                })

                base_name = os.path.splitext(os.path.basename(raster_path))[0]
                out_path = os.path.join(output_dir, f"{base_name}_Rectangular_Visualizacion.tif")

                self.log(f"Guardando resultado en: {out_path}")
                with rasterio.open(out_path, "w", **out_meta) as dest:
                    dest.write(out_image)

            elapsed = time.time() - start_time
            self.timer_label.config(text=f"Tiempo transcurrido: {elapsed:.2f} s")
            self.status_label.config(text="Estado: ¡Proceso completado con éxito!")
            self.log(f"¡Proceso finalizado en {elapsed:.2f} segundos!")
            messagebox.showinfo("Éxito", f"El recorte rectangular se ha guardado correctamente en:\n{out_path}")

        except Exception as e:
            self.status_label.config(text="Estado: Error en el proceso")
            self.log(f"ERROR: {str(e)}")
            messagebox.showerror("Error", f"Ocurrió un error durante el procesamiento:\n{str(e)}")

        finally:
            self.run_btn.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = RectangularClipApp(root)
    root.mainloop()
