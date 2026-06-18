from pathlib import Path
import rasterio


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def open_raster(raster_path):
    return rasterio.open(raster_path)


def get_raster_info(raster_path):
    with rasterio.open(raster_path) as src:
        return {
            "width": src.width,
            "height": src.height,
            "bands": src.count,
            "crs": str(src.crs),
            "transform": src.transform,
            "dtype": src.dtypes[0],
        }


def save_single_band(output_path, array, profile):
    profile.update(
        count=1,
        dtype="float32",
        compress="lzw"
    )

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)