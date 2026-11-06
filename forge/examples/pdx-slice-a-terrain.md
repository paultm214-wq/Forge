# pdx-slice-a-terrain

Toolchain: python
License: MIT
Prior art: none

<!--
Portland Survival Map (Fallout 4), Slice A - Step 5 of the v3 plan: the
terrain pipeline. Real bare-earth elevation, warped through the project's
compression transform onto the Creation Kit's vertex grid for the Slice A
cells, written as a 16-bit heightmap with a sidecar and a preview.

Canon followed (Portland Survival Map planning v3):
- the transform is r' = r / (K0 * (1 + r / R)), bearing preserved, centred
  on Pioneer Courthouse Square; its constants live ONLY in landmarks.json's
  "transform" block (written by pdx_transform.py) - this module reads them
  from there and never repeats them;
- 1 cell = 4096 CK units = 58.5 m; vertices every 128 units;
- vertical scale starts at 0.5 x real elevation;
- Slice A is the game-meter box x -1000..1000, y -950..650.

Every number in Acceptance was checked against a reference implementation.
UTM reference values come from pyproj (EPSG:4269 -> EPSG:26910). The test
GeoTIFFs below were written with tifffile and read back with it.

The CK's heightmap import takes a single-channel 32-bit float DDS (Paul,
from the CK, 4 Nov). The DDS layout here follows Microsoft's DDS docs
(DDS_HEADER, DDS_PIXELFORMAT, DDS_HEADER_DXT10; DXGI_FORMAT_R32_FLOAT is
41, its legacy FourCC D3DFMT_R32F is 114). Still open: whether the CK wants
the floats normalised 0..1 or in real height units, and whether it needs a
particular image size - so both value modes and both header kinds are
options, and the sidecar records which was written. The grid is laid out
exactly on the cell grid (33 x 33 vertices per cell, shared edges, which is
also the layout of a LAND record's VHGT in xEdit's Fallout 4 definitions).
-->

## Goal

A command-line tool, run on Gemini against DEM tiles Paul downloads, that
writes the Slice A heightmap:

```
python3 pdx_terrain.py --landmarks landmarks.json --dem tile1.tif --dem tile2.tif --out terrain/
```

Outputs in `terrain/`: `slice_a_height.dds` (single-channel 32-bit float,
what the CK imports), `slice_a_height.png` (16-bit grayscale),
`slice_a_height.raw` (the same values, little-endian uint16),
`slice_a_height.json` (the sidecar) and `slice_a_preview.png` (8-bit RGB
with the Slice A landmarks as red dots, to eyeball alignment).

Getting the elevation data (for Paul, not for the tests): the USGS 3DEP 1 m
DEMs are bare earth, in UTM in metres on NAD83 (Portland is zone 10, so
EPSG:26910), with elevations in metres on NAVD88, one tile per 10 km. Slice
A crosses a tile edge, so expect two tiles. Pick them in The National Map
Downloader. The new OR_PortlandMetro_B24 QL1 lidar was published on
5 March 2026; its "Source (OPR) DEMs" are under
StagedProducts/Elevation/OPR/Projects/OR_PortlandMetro_B24/ on
prd-tnm.s3.amazonaws.com, and the seamless 1 m DEM from it was "to be
generated soon" at the time. The 1/3 arc-second DEM (about 10 m,
geographic NAD83, EPSG:4269) also works, more coarsely. Check which is
current before downloading.

## Constraints

- Language / runtime: Python 3.12, standard library only (struct, zlib,
  math, json, argparse). No numpy, no GDAL.
- Module name: `pdx_terrain`
- No network access. Tests write their DEMs under the test's temp folder.
- The transform constants come from the landmarks file's `"transform"`
  object (keys `origin_lat`, `origin_lon`, `m_per_deg_lat`,
  `m_per_deg_lon`, `K0`, `R`, `units_per_m`, `cell_units`,
  `vertical_scale`; other keys are ignored). No default values for them
  in the module.
- GeoTIFF support: little-endian ("II") classic TIFF; one band;
  BitsPerSample 16 or 32 with SampleFormat 1 (uint), 2 (int) or 3
  (float; also 64-bit float); Compression 1 (none), 5 (LZW), 8 or 32946
  (Deflate); Predictor 1, 2 (horizontal differencing) or 3 (floating
  point); strips or tiles. Anything else raises `ValueError` naming the
  tag and its value (e.g. `"Compression 7 is not supported"`).
- GeoTIFF georeferencing: ModelPixelScaleTag (33550), ModelTiepointTag
  (33922) and GeoKeyDirectoryTag (34735), raster type PixelIsArea (the
  tiepoint is the top-left CORNER of the top-left pixel; pixel (col, row)
  has its centre at x0 + (col + 0.5) * sx, y0 - (row + 0.5) * sy).
  Supported CRSs: projected EPSG:26910 (NAD83 / UTM 10N, metres) and
  geographic EPSG:4269 or EPSG:4326 (degrees: x = longitude, y =
  latitude). Others raise `ValueError` naming the EPSG code. GDAL_NODATA
  (42113, ASCII) marks no-data when present.
- NAD83 and WGS84 differ by about a metre here; treat lat/lon from the
  transform as NAD83. That is far below a vertex (128 units = 1.83 game
  metres, 4.6 or more real metres).

## Reference facts

Exact format facts every chunk needs. (A coder and the planner both got
these wrong in the 4 Nov run: GTModelTypeGeoKey's values swapped, FLOAT and
DOUBLE given 1 and 2 bytes, the tiepoint read as LONGs.) Checked against
tifffile's tables and a reader that decodes the fixtures below.

- **IFD entry:** 12 bytes - tag (uint16), type (uint16), count (uint32),
  then 4 bytes that hold the value itself when count x size <= 4 (packed
  from the first byte) or else a uint32 file offset to it.
- **Field types (size in bytes):** 1 BYTE 1, 2 ASCII 1, 3 SHORT 2
  (unsigned), 4 LONG 4 (unsigned), 5 RATIONAL 8, 6 SBYTE 1, 7 UNDEFINED 1,
  8 SSHORT 2, 9 SLONG 4, 10 SRATIONAL 8, 11 FLOAT 4, 12 DOUBLE 8.
  StripOffsets, StripByteCounts, TileOffsets and TileByteCounts may be
  SHORT or LONG.
- **Tags:** 256 ImageWidth, 257 ImageLength, 258 BitsPerSample, 259
  Compression, 273 StripOffsets, 277 SamplesPerPixel, 278 RowsPerStrip
  (default: the whole image), 279 StripByteCounts, 317 Predictor (default
  1), 322 TileWidth, 323 TileLength, 324 TileOffsets, 325 TileByteCounts,
  339 SampleFormat (default 1).
- **Strips:** strip i holds rows i x RowsPerStrip onwards; the last strip
  may hold fewer rows. **Tiles:** tiles across = ceil(width / TileWidth),
  tile index = (row // TileLength) x across + col // TileWidth; edge tiles
  are stored full size (padded), so a row inside a tile is TileWidth
  samples long.
- **GeoTIFF:** 33550 ModelPixelScaleTag = 3 DOUBLE (sx, sy, sz); 33922
  ModelTiepointTag = 6 DOUBLE (i, j, k, x, y, z), so the top-left corner is
  (x - i x sx, y + j x sy). 34735 GeoKeyDirectoryTag = SHORTs: a 4-SHORT
  header (KeyDirectoryVersion, KeyRevision, MinorRevision, NumberOfKeys),
  then NumberOfKeys entries of 4 SHORTs (KeyID, TIFFTagLocation, Count,
  Value_Offset); TIFFTagLocation 0 means the value IS Value_Offset.
  Keys: 1024 GTModelTypeGeoKey (1 = projected, 2 = geographic,
  3 = geocentric); 1025 GTRasterTypeGeoKey (1 = PixelIsArea,
  2 = PixelIsPoint); 2048 GeographicTypeGeoKey (the EPSG code of a
  geographic CRS); 3072 ProjectedCSTypeGeoKey (the EPSG code of a
  projected CRS). For PixelIsPoint the tiepoint is the CENTRE of pixel
  (i, j): the corner is half a pixel further up and left. 42113 GDAL_NODATA
  is ASCII.
- **LZW (TIFF 6.0, section 13):** codes are packed most significant bit
  first. 256 = Clear, 257 = EndOfInformation; new table entries start at
  258; codes are 9 bits wide after a Clear. After each entry is added, the
  width grows by one bit when the next free code reaches 511, 1023 or 2047
  ("early change"), up to 12 bits. The first code after a Clear is output
  as is and adds no entry. When a code equals the next free code, its
  string is the previous string plus the previous string's first byte.
  Each strip or tile is its own LZW stream.
- **Predictor 2:** per row of a strip or tile, each sample after the first
  adds the sample before it, in the sample's integer type (wrapping, two's
  complement for signed).
- **Predictor 3 (floating point):** per row of width w (the tile width, or
  the image width for strips) with b bytes per sample: first undo the
  byte differencing over the row's w x b bytes - byte[k] = (byte[k] +
  byte[k - 1]) mod 256 for k >= 1 - then the row is b byte planes, most
  significant first: pixel c's value, big-endian, is the bytes
  row[j x w + c] for j = 0 .. b - 1.
- **Byte order:** "II" means the header, IFD and plain samples are
  little-endian.

## Interfaces

**Transform** (`cfg` is the landmarks file's `"transform"` dict):

- `load_config(path) -> dict`: the `"transform"` object of a landmarks
  JSON file; `KeyError` naming the first missing key above.
- `compress(x, y, cfg) -> (xc, yc)`: as pdx_transform.py - r = hypot(x, y);
  (0, 0) maps to (0, 0); otherwise r' = r / (K0 * (1 + r / R)) along the
  same bearing.
- `inverse_compress(xc, yc, cfg) -> (x, y)`: the exact inverse,
  r = r' * K0 / (1 - r' * K0 / R) along the same bearing; (0, 0) -> (0, 0);
  `ValueError` when r' * K0 >= R (outside the transform's range).
- `game_to_latlon(xc, yc, cfg) -> (lat, lon)`: inverse_compress, then
  lat = origin_lat + y / m_per_deg_lat, lon = origin_lon + x / m_per_deg_lon.
- `latlon_to_utm(lat, lon, zone=10) -> (easting, northing)`: Transverse
  Mercator on GRS80 (a = 6378137 m, f = 1 / 298.257222101), k0 = 0.9996,
  false easting 500000 m, central meridian zone * 6 - 183 degrees,
  northern hemisphere (Kruger series to n^3 is accurate to well under 1 mm
  here).

**GeoTIFF reading:**

- `GeoTiff(path)`: reads the header and tags (not the pixels), as in
  Reference facts (PixelIsPoint files get their corner moved half a pixel).
  Attributes: `width`, `height`, `epsg` (int), `projected` (bool),
  `origin` ((x0, y0), the top-left corner), `pixel` ((sx, sy), both
  positive), `nodata` (float or None).
- `GeoTiff.value(col, row) -> float | None`: one pixel; None outside the
  raster or at no-data. Blocks (strips or tiles) are decoded on first use
  and cached, so reading a window decodes only the blocks it touches.
- `GeoTiff.sample(x, y, clamp=True) -> float | None`: bilinear on pixel
  centres, in the file's CRS. A point inside the raster's outer edge (from
  the top-left corner to the bottom-right corner) but beyond the outermost
  pixel centres clamps to the edge pixels; with `clamp=False` such a point
  gives None. A point outside the edge always gives None. When any of the four neighbours is
  no-data, the result is the valid neighbour with the largest bilinear
  weight (ties: the first of (c0, r0), (c0+1, r0), (c0, r0+1),
  (c0+1, r0+1)); None when all four are no-data.
- `lzw_decode(data: bytes) -> bytes`: one TIFF LZW stream (a strip or a
  tile), as in Reference facts. A code that is neither in the table nor the
  next free code raises ValueError.
- `write_geotiff(path, rows, origin, pixel, epsg, nodata=None)`: writes
  `rows` (a list of equal-length lists of floats, row 0 = north) as an
  uncompressed float32 GeoTIFF that `GeoTiff` reads back exactly - for
  tests and debugging.

**The grid:**

- `SliceGrid(x_min_m, x_max_m, y_min_m, y_max_m, cfg)`: the whole cells
  that cover the game-metre box. Cell index = floor(metres * units_per_m /
  cell_units) for each edge. Attributes: `cells` ((cx_min, cx_max, cy_min,
  cy_max), inclusive), `width` and `height` in vertices
  ((cx_max - cx_min + 1) * 32 + 1, likewise for y), `spacing` (cell_units /
  32 = 128 units).
- `SliceGrid.vertex_ck(col, row) -> (ck_x, ck_y)`: row 0 is the NORTH edge:
  ck_x = cx_min * cell_units + col * spacing,
  ck_y = (cy_max + 1) * cell_units - row * spacing.
- `SliceGrid.vertex_game_m(col, row) -> (xc, yc)`: vertex_ck / units_per_m.
- `SliceGrid.ck_to_pixel(ck_x, ck_y) -> (col, row)`: the inverse of
  vertex_ck, as floats.

**The heightmap:**

- `build_heightmap(grid, dems, cfg) -> dict`: for every vertex, the game
  point to lat/lon, then to the DEM's CRS (UTM via latlon_to_utm, or lon/lat
  as is), sampled from the first DEM in `dems` (a list of `GeoTiff`) that
  gives a value with `clamp=False`, or, when none does, the first that
  gives one with clamping (real DEM tiles overlap, so a point near one
  tile's edge is interpolated in its neighbour instead of clamped). Height in CK units = elevation_m * vertical_scale *
  units_per_m. Returns `{"heights": rows (list of lists of floats, row 0
  north), "missing": count of vertices no DEM covered, "min": lowest,
  "max": highest}`. Missing vertices get the lowest height found, and when
  no vertex is covered at all it raises `ValueError("no DEM covers the
  slice")`.
- `quantize(heights, lo, hi) -> list[list[int]]`: round((h - lo) / (hi - lo)
  * 65535), clamped to 0..65535; all 0 when hi == lo.
- `write_png16(path, values)`: 16-bit grayscale PNG (colour type 0, bit
  depth 16, no interlace, filter 0 on every row, one zlib IDAT is fine),
  big-endian samples as PNG requires.
- `write_raw16(path, values)`: the same values, little-endian uint16, row 0
  first, no header.
- `dds_values(heights, lo, hi, scale) -> list[list[float]]`: `scale`
  "normalized" gives (h - lo) / (hi - lo) (all 0.0 when hi == lo);
  "units" gives the heights unchanged (CK units). Anything else:
  ValueError.
- `write_dds_r32f(path, values, header="dx10")`: a single-channel 32-bit
  float DDS, no mipmaps, all integers little-endian:
  - the magic `b"DDS "`;
  - DDS_HEADER (124 bytes): dwSize 124; dwFlags 0x100F (CAPS 0x1 | HEIGHT
    0x2 | WIDTH 0x4 | PITCH 0x8 | PIXELFORMAT 0x1000); dwHeight; dwWidth;
    dwPitchOrLinearSize = (width * 32 + 7) // 8; dwDepth 0; dwMipMapCount
    0; dwReserved1 eleven zeros; DDS_PIXELFORMAT (32 bytes: dwSize 32,
    dwFlags 0x4 (DDPF_FOURCC), dwFourCC, then dwRGBBitCount and the four
    masks all 0); dwCaps 0x1000 (DDSCAPS_TEXTURE); dwCaps2, dwCaps3,
    dwCaps4 and dwReserved2 0;
  - `header="dx10"`: dwFourCC is `b"DX10"`, followed by DDS_HEADER_DXT10
    (20 bytes): dxgiFormat 41 (DXGI_FORMAT_R32_FLOAT), resourceDimension 3
    (TEXTURE2D), miscFlag 0, arraySize 1, miscFlags2 0;
  - `header="legacy"`: dwFourCC is the integer 114 (D3DFMT_R32F) and there
    is no DXT10 header;
  - then the pixels: row 0 (north) first, each value a little-endian
    float32.
  Any other `header`: ValueError.
- `write_preview(path, heights, lo, hi, grid, landmarks)`: 8-bit RGB PNG,
  one pixel per vertex, gray = round((h - lo) / (hi - lo) * 255) (all 0
  when hi == lo). Each landmark (a dict with `ck_x` and `ck_y`) inside the
  grid is a 5 x 5 red (255, 0, 0) square centred on
  round(ck_to_pixel(...)), cut off at the edges; landmarks outside are
  skipped. Returns how many it drew.
- `sidecar(grid, hm, lo, hi, cfg, dem_paths) -> dict`: `width`, `height`,
  `cells` ({"x_min", "x_max", "y_min", "y_max"}), `spacing_units` (128),
  `pixel_0_0_ck` ([ck_x, ck_y] of vertex (0, 0)), `height_min_units`,
  `height_max_units`, `units_per_step` ((hi - lo) / 65535),
  `vertical_scale`, `missing`, `dems` (the paths as given), and
  `transform` (a copy of cfg). `main` adds `dds`: {"file":
  "slice_a_height.dds", "header": "dx10" or "legacy", "scale":
  "normalized" or "units"}.

**CLI:** `main(argv) -> int` with `--landmarks PATH` (required), `--dem
PATH` (one or more, in priority order), `--out DIR` (required, created if
missing), `--box XMIN XMAX YMIN YMAX` (game metres, default -1000 1000
-950 650 - Slice A), `--dds-scale normalized|units` (default normalized)
and `--dds-header dx10|legacy` (default dx10). It writes the five files, prints one line per file and
a final line `missing: N`, and returns 0; 2 when no DEM covers the slice
(the message on stderr). `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

## Test fixture and helpers (copy verbatim)

```python
import base64 as _b64
import json as _json
from pathlib import Path as _Path

# Three small GeoTIFFs written with tifffile 2026.9.15. All are 20 x 18
# pixels. Values: the float32 ones are 100 + 2*col - 3*row + 0.25 with
# no-data -999999 at (col 7, row 5); the int16 one is 50 + col - 2*row with
# no-data -9999 at (col 3, row 2).
# - utm_lzw_pred3_tiled.tif: EPSG:26910, LZW, predictor 3, 16 x 16 tiles,
#   top-left corner (525000, 5040000), 10 m pixels.
# - geo_deflate_pred2_strips.tif: EPSG:4269, int16, Deflate, predictor 2,
#   5 rows per strip, top-left corner (-122.80, 45.62), 0.01 degree pixels.
# - geo_none_f32.tif: EPSG:4269, float32, uncompressed, same corner and
#   pixels as the deflate one.
FIXTURE_TIFFS = {
    "utm_lzw_pred3_tiled.tif": (
        "SUkqAAgAAAAUAAABBAABAAAAFAAAAAEBBAABAAAAEgAAAAIBAwABAAAAIAAAAAMBAwABAAAABQAA"
        "AAYBAwABAAAAAQAAABUBAwABAAAAAQAAABoBBQABAAAA/gAAABsBBQABAAAABgEAACgBAwABAAAA"
        "AQAAADEBAgAMAAAADgEAAD0BAwABAAAAAwAAAEIBBAABAAAAEAAAAEMBBAABAAAAEAAAAEQBBAAE"
        "AAAAGgEAAEUBAwAEAAAAKgEAAFMBAwABAAAAAwAAAA6DDAADAAAAMgEAAIKEDAAGAAAASgEAAK+H"
        "AwAQAAAAegEAAIGkAgAIAAAAmgEAAAAAAAABAAAAAQAAAAEAAAABAAAAdGlmZmZpbGUucHkAsAEA"
        "AHMCAAA2AwAAiwMAAMMAwwBVAE4AAAAAAAAAJEAAAAAAAAAkQAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAJAFIEEAAAAA4DlTQQAAAAAAAAAAAQABAAAAAwAABAAAAQABAAEEAAAB"
        "AAEAAAwAAAEAHmktOTk5OTk5AAAAAAAAAAAAAAAAAAAAgBCAEDgkFggBACFAkLhkNhoCP0GiUDYA"
        "AisTicCjESQEOj0NQUbjkig0akkDPUflSIk8EQEtgcmk50lUfR0wAEvmEykhumseSk4nUtngAQ55"
        "iZojyyK0fTUSUZdidDgjwCEZnBin8OUFCnFFjZcrcNU1enc4ptjhass1EnBQtULWVtk9gjBKuIEX"
        "F0kl2iZEvK+vkiv0SH15YmDjeFgwzBAFtRKnMtql9nAnBAIA9jKEDytTr84DeZzOQmpWguf1M4gI"
        "gBDAEDXsDg0HgYEAULe0IhxAgzAh0TikOgUEisDAMLAT5jIAiEDiUfkkGIQBjEZfkKAT8j6Akcjk"
        "skIUagsZewEAgDf8vkUymcZmsDX0feE6nU+gaAoM0g1FjLqpAKpQAplNoVPj7kpAQqtXrEUoYAqE"
        "Vb1IC1fsMVsdlijYpActVridtj7SpAiud0hF2jLMpAovd8k1ajLGpAuweEAF+irApA0xeEx0UXVI"
        "HWTvmVia0pEhitgsGMxuGiqupBGzV0gIgBCAEDgkFgwPBEJhIHAkNh0OLkGQEGgsTikXAECjEUAc"
        "KjwFh8OMUXi0SjcnlEplUrlktl0vmExmUzmk1m03nE5nU7nk9n0/oFBoVDolFo1HpAAgIIAQgBA1"
        "9A4NB4GqAJCyZCIcgINEIdE4pCIFBIrA1FCwIUoyAIlII/I5JJZNJ5RKZVK5ZLZdL5hMZlM5pNZt"
        "N5xOZ1O55PZ9P6BQaFN4CA=="
    ),
    "geo_deflate_pred2_strips.tif": (
        "SUkqAAgAAAATAAABBAABAAAAFAAAAAEBBAABAAAAEgAAAAIBAwABAAAAEAAAAAMBAwABAAAACAAA"
        "AAYBAwABAAAAAQAAABEBBAAEAAAA8gAAABUBAwABAAAAAQAAABYBBAABAAAABQAAABcBAwAEAAAA"
        "AgEAABoBBQABAAAACgEAABsBBQABAAAAEgEAACgBAwABAAAAAQAAADEBAgAMAAAAGgEAAD0BAwAB"
        "AAAAAgAAAFMBAwABAAAAAgAAAA6DDAADAAAAJgEAAIKEDAAGAAAAPgEAAK+HAwAQAAAAbgEAAIGk"
        "AgAGAAAAjgEAAAAAAACgAQAAwAEAANoBAAD0AQAAIAAaABoAEwABAAAAAQAAAAEAAAABAAAAdGlm"
        "ZmZpbGUucHkAexSuR+F6hD97FK5H4XqEPwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "MzMzMzOzXsCPwvUoXM9GQAAAAAAAAAAAAQABAAAAAwAABAAAAQACAAEEAAABAAEAAAgAAAEArRAt"
        "OTk5OQAAAAAAAAAAAAAAAAB4nDNiYCQKGhCpTg9MHrzhqI5fnQ6R5mkRqQ4AdqQDRXic02BgJAqq"
        "EalOhUh1SkSqUyBSHQB9ZAEUeJyTY2AkCsoQqU6KSHUSRKoTI1IdAGX0AOJ4nBNhYCQKChGpToBI"
        "dQAfVABw"
    ),
    "geo_none_f32.tif": (
        "SUkqAAgAAAASAAABBAABAAAAFAAAAAEBBAABAAAAEgAAAAIBAwABAAAAIAAAAAMBAwABAAAAAQAA"
        "AAYBAwABAAAAAQAAABEBBAABAAAAgAEAABUBAwABAAAAAQAAABYBBAABAAAAEgAAABcBBAABAAAA"
        "oAUAABoBBQABAAAA5gAAABsBBQABAAAA7gAAACgBAwABAAAAAQAAADEBAgAMAAAA9gAAAFMBAwAB"
        "AAAAAwAAAA6DDAADAAAAAgEAAIKEDAAGAAAAGgEAAK+HAwAQAAAASgEAAIGkAgAIAAAAagEAAAAA"
        "AAABAAAAAQAAAAEAAAABAAAAdGlmZmZpbGUucHkAexSuR+F6hD97FK5H4XqEPwAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAMzMzMzOzXsCPwvUoXM9GQAAAAAAAAAAAAQABAAAAAwAABAAA"
        "AQACAAEEAAABAAEAAAgAAAEArRAtOTk5OTk5AAAAAAAAAAAAAAAAAAAAAIDIQgCAzEIAgNBCAIDU"
        "QgCA2EIAgNxCAIDgQgCA5EIAgOhCAIDsQgCA8EIAgPRCAID4QgCA/EIAQABDAEACQwBABEMAQAZD"
        "AEAIQwBACkMAgMJCAIDGQgCAykIAgM5CAIDSQgCA1kIAgNpCAIDeQgCA4kIAgOZCAIDqQgCA7kIA"
        "gPJCAID2QgCA+kIAgP5CAEABQwBAA0MAQAVDAEAHQwCAvEIAgMBCAIDEQgCAyEIAgMxCAIDQQgCA"
        "1EIAgNhCAIDcQgCA4EIAgORCAIDoQgCA7EIAgPBCAID0QgCA+EIAgPxCAEAAQwBAAkMAQARDAIC2"
        "QgCAukIAgL5CAIDCQgCAxkIAgMpCAIDOQgCA0kIAgNZCAIDaQgCA3kIAgOJCAIDmQgCA6kIAgO5C"
        "AIDyQgCA9kIAgPpCAID+QgBAAUMAgLBCAIC0QgCAuEIAgLxCAIDAQgCAxEIAgMhCAIDMQgCA0EIA"
        "gNRCAIDYQgCA3EIAgOBCAIDkQgCA6EIAgOxCAIDwQgCA9EIAgPhCAID8QgCAqkIAgK5CAICyQgCA"
        "tkIAgLpCAIC+QgCAwkLwI3TJAIDKQgCAzkIAgNJCAIDWQgCA2kIAgN5CAIDiQgCA5kIAgOpCAIDu"
        "QgCA8kIAgPZCAICkQgCAqEIAgKxCAICwQgCAtEIAgLhCAIC8QgCAwEIAgMRCAIDIQgCAzEIAgNBC"
        "AIDUQgCA2EIAgNxCAIDgQgCA5EIAgOhCAIDsQgCA8EIAgJ5CAICiQgCApkIAgKpCAICuQgCAskIA"
        "gLZCAIC6QgCAvkIAgMJCAIDGQgCAykIAgM5CAIDSQgCA1kIAgNpCAIDeQgCA4kIAgOZCAIDqQgCA"
        "mEIAgJxCAICgQgCApEIAgKhCAICsQgCAsEIAgLRCAIC4QgCAvEIAgMBCAIDEQgCAyEIAgMxCAIDQ"
        "QgCA1EIAgNhCAIDcQgCA4EIAgORCAICSQgCAlkIAgJpCAICeQgCAokIAgKZCAICqQgCArkIAgLJC"
        "AIC2QgCAukIAgL5CAIDCQgCAxkIAgMpCAIDOQgCA0kIAgNZCAIDaQgCA3kIAgIxCAICQQgCAlEIA"
        "gJhCAICcQgCAoEIAgKRCAICoQgCArEIAgLBCAIC0QgCAuEIAgLxCAIDAQgCAxEIAgMhCAIDMQgCA"
        "0EIAgNRCAIDYQgCAhkIAgIpCAICOQgCAkkIAgJZCAICaQgCAnkIAgKJCAICmQgCAqkIAgK5CAICy"
        "QgCAtkIAgLpCAIC+QgCAwkIAgMZCAIDKQgCAzkIAgNJCAICAQgCAhEIAgIhCAICMQgCAkEIAgJRC"
        "AICYQgCAnEIAgKBCAICkQgCAqEIAgKxCAICwQgCAtEIAgLhCAIC8QgCAwEIAgMRCAIDIQgCAzEIA"
        "AHVCAAB9QgCAgkIAgIZCAICKQgCAjkIAgJJCAICWQgCAmkIAgJ5CAICiQgCApkIAgKpCAICuQgCA"
        "skIAgLZCAIC6QgCAvkIAgMJCAIDGQgAAaUIAAHFCAAB5QgCAgEIAgIRCAICIQgCAjEIAgJBCAICU"
        "QgCAmEIAgJxCAICgQgCApEIAgKhCAICsQgCAsEIAgLRCAIC4QgCAvEIAgMBCAABdQgAAZUIAAG1C"
        "AAB1QgAAfUIAgIJCAICGQgCAikIAgI5CAICSQgCAlkIAgJpCAICeQgCAokIAgKZCAICqQgCArkIA"
        "gLJCAIC2QgCAukIAAFFCAABZQgAAYUIAAGlCAABxQgAAeUIAgIBCAICEQgCAiEIAgIxCAICQQgCA"
        "lEIAgJhCAICcQgCAoEIAgKRCAICoQgCArEIAgLBCAIC0QgAARUIAAE1CAABVQgAAXUIAAGVCAABt"
        "QgAAdUIAAH1CAICCQgCAhkIAgIpCAICOQgCAkkIAgJZCAICaQgCAnkIAgKJCAICmQgCAqkIAgK5C"
    ),
}


# TIFF LZW streams written by imagecodecs.lzw_encode, with what they decode
# to. ab3000 crosses the 511 and 1023 code-width steps.
import random as _random

def _ab3000():
    r = _random.Random(7)
    return bytes(r.choice(b'ab') for _ in range(3000))

LZW_VECTORS = {
    "short": (
        (
            "gBUJ5CIpPKROJ5UgUEg0LgsHhMBA"
        ),
        b"TOBEORNOTTOBEORTOBEORNOT",
    ),
    "bytes": (
        (
            "gAAAICAYEAoGA4IBIKBYMBoOB4QCISCYUCoWC4YDIaDYcDoeD4gEIiEYkEomE4oFIqFYsFou"
            "F4wGIyGY0Go2G44HI6HY8Ho+H5AIJCIZEIpGI5IJJKJZMJpOJ5QKJSKZUKpWK5YLJaLZcLpe"
            "L5gMJiMZkMpmM5oNJqNZsNpuN5wOJyOZ0Op2O54PJ6PZ8Pp+P6AQKCQaEQqGQ6IRKKRaMRqO"
            "R6QSKSSaUSqWS6YTKaTacTqeT6gUKiUakUqmU6oVKqVasVquV6wWKyWa0Wq2W64XK6Xa8Xq+"
            "X7AYLCYbEYrGY7IZLKZbMZrOZ7QaLSabUarWa7YbLabbcbreb7gcLicbkcrmc7odLqdbsdru"
            "d7weLyeb0er2e74fL6fb8fp/H+gSCIMhCFIYhyIIkiiLIwjSOI8kCRJIkyUJUliXJgmSaJsn"
            "CdJ4nygKEoijKQpSmKcqCpKoqysK0rivLAsSyLMtC1LYty4Lkui7LwvS+L8wDBMIwzEMUxjH"
            "MgyTKMszDNM4zzQNE0jTNQ1TWNc2DZNo2zcN03jfOA4TiOM5DlOY5zoOk6jrOw7TuO88DxPI"
            "8z0PU9j3Pg+T6Ps/D9P4/0AICA=="
        ),
        bytes(range(256)) * 2,
    ),
    "ab3000": (
        (
            "gBiMMCMMDgsFMUEg0HhUDgkKhsJiUJg8OiUIg0Ui0ajkTh0YhcXi0bhsVgUdh8ejUgksQjcI"
            "j0wl0Zk8LkMpk8ijE1jk0kcVn1Al05nEMoUwk0sokZmkPllMj8ynNNn8Mp0XilXkFMnVbrNT"
            "iNNp1RpFImsfmNnr9JrkQrNbpMrt9mq1Tus7nEinlLk08qsqrF/teBm9gq1kieAlsvuNRrFf"
            "ocxn9duF4o1ywmIvdip9XtVjr1yvFdu1ptFvv2FoktxMhqVPtGht1SzM7xGXs9xseimU21F1"
            "x+orVV21k2PHqFB3XBzGnu9hm2Hs2D2+EyGP32kvVCzfG7NFo9A21+zOzyW+09aktG8Ww8Ur"
            "kkjlXKtV06242NLz/psvN5LVPw5zHIiwLVrary2p7AzXwE9jAJesD5wUvsKNc3aUvy4DTMs8"
            "brtXCTNOM3Los88jCw4majvqxi+r20TEus/UFqLBTNry96dLW5SqNTAj9q5CrbvSyD0MbGjk"
            "xc56+PszELwPEcgvQtMVtK/L1uk8cpN4/skxA3L4Ry3cOSAxjhQ2ubWwIwTPQtCMYOuoLftp"
            "B67vi+TetA+r4Mc9rJv7McoNHGs9pRMsuv/OMItHEjhuyzr5zvCcLKhDbLys7c8zrDFAJvQF"
            "Ntaw8iO7AL5VHE7WT/SkvsNGLsKVJcHS/R1J1myNTxDKjpzZUMMR8xT2VK3UlRnFsm19RVGO"
            "itlVT9O0dO44T4x69swP5NU/UGjsMurRD9vOoyAg"
        ),
        _ab3000(),
    ),
}


def lzw_vector(name):
    """(encoded bytes, decoded bytes) of one LZW_VECTORS entry."""
    enc, raw = LZW_VECTORS[name]
    return _b64.b64decode("".join(enc)), raw


def fixture_tiff(name, folder):
    """Write one of FIXTURE_TIFFS into folder and return its Path."""
    p = _Path(folder) / name
    p.write_bytes(_b64.b64decode("".join(FIXTURE_TIFFS[name])))
    return p


def with_short_tag(path, tag, value, out):
    """Copy a little-endian TIFF to `out` with one SHORT tag of its first
    IFD set to `value` (e.g. tag 259, Compression, to 7)."""
    import struct
    b = bytearray(_Path(path).read_bytes())
    ifd = struct.unpack_from("<I", b, 4)[0]
    for i in range(struct.unpack_from("<H", b, ifd)[0]):
        e = ifd + 2 + 12 * i
        t, typ, cnt = struct.unpack_from("<HHI", b, e)
        if t == tag:
            assert typ == 3 and cnt == 1, "not a single SHORT"
            struct.pack_into("<H", b, e + 8, value)
            _Path(out).write_bytes(bytes(b))
            return _Path(out)
    raise KeyError(tag)


# The project's real transform constants, as pdx_transform.py writes them
# into landmarks.json.
REAL_TRANSFORM = {
    "origin_name": "Pioneer Courthouse Square",
    "origin_lat": 45.5189, "origin_lon": -122.6794,
    "m_per_deg_lat": 111140.0, "m_per_deg_lon": 78030.0,
    "K0": 2.5, "R": 11000.0, "units_per_m": 4096 / 58.5,
    "cell_units": 4096, "vertical_scale": 0.5,
    "formula": "r' = r / (K0 * (1 + r / R)), bearing preserved; CK units = m * 4096/58.5",
}


def landmarks_file(folder, landmarks=()):
    """A landmarks.json like pdx_transform.py writes (only what this module reads)."""
    p = _Path(folder) / "landmarks.json"
    p.write_text(_json.dumps({"project": "Portland Survival Map", "transform": REAL_TRANSFORM,
                              "landmarks": list(landmarks)}))
    return p


def read_dds(path):
    """(header fields, dxt10 fields or None, rows of float32) for the DDS
    files this module writes."""
    import struct
    b = _Path(path).read_bytes()
    assert b[:4] == b"DDS ", "no DDS magic"
    names = ["size", "flags", "height", "width", "pitch", "depth", "mipmaps"]
    h = dict(zip(names, struct.unpack_from("<7I", b, 4)))
    h["reserved1"] = list(struct.unpack_from("<11I", b, 32))
    pf = struct.unpack_from("<2I4s5I", b, 76)
    h["pf_size"], h["pf_flags"], h["fourcc"] = pf[0], pf[1], pf[2]
    h["pf_rest"] = list(pf[3:])
    h["caps"] = list(struct.unpack_from("<5I", b, 108))
    pos = 128
    dx10 = None
    if h["fourcc"] == b"DX10":
        dx10 = dict(zip(["format", "dimension", "misc", "array", "misc2"],
                        struct.unpack_from("<5I", b, pos)))
        pos += 20
    w, ht = h["width"], h["height"]
    assert len(b) == pos + w * ht * 4, f"size {len(b)} != {pos + w * ht * 4}"
    rows = [list(struct.unpack_from("<" + "f" * w, b, pos + r * w * 4)) for r in range(ht)]
    return h, dx10, rows


def read_png(path):
    """(width, height, bit_depth, colour_type, rows of samples) for the PNGs
    this module writes (filter 0 rows only)."""
    import struct, zlib
    b = _Path(path).read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, ihdr = 8, b"", None
    while pos < len(b):
        n, kind = struct.unpack(">I4s", b[pos:pos + 8])
        data = b[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            idat += data
        pos += 12 + n
    w, h, depth, ctype = ihdr[:4]
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3}[ctype]
    bpp = ch * depth // 8
    stride = w * bpp
    rows = []
    for r in range(h):
        line = raw[r * (stride + 1):(r + 1) * (stride + 1)]
        assert line[0] == 0, "row filter is not 0"
        line = line[1:]
        if depth == 16:
            rows.append(list(struct.unpack(">" + "H" * (w * ch), line)))
        else:
            rows.append(list(line))
    return w, h, depth, ctype, rows
```

## Acceptance

Transform (cfg = REAL_TRANSFORM):

1. `inverse_compress(1000, 0, cfg)` is (3235.294117647059, 0.0) within
   1e-6 (exactly 2500 / 0.772727...);
   `inverse_compress(1000, -950, cfg)` is (3641.5538, -3459.4761) within
   1e-3; `compress(*inverse_compress(x, y))` returns (x, y) within 1e-9 for
   (1000, -950), (-1000, 650), (0.5, 0) and (0, 0).
2. `inverse_compress(4400, 0, cfg)` raises ValueError (r' * K0 = R), and so
   does (3200, 3200).
3. `game_to_latlon(0, 0, cfg)` is (45.5189, -122.6794) exactly;
   `game_to_latlon(1000, 0, cfg)` is (45.5189, -122.637937817) within 1e-9;
   `game_to_latlon(0, 650, cfg)` is (45.536055540, -122.6794) within 1e-9.
4. `latlon_to_utm` matches pyproj within 0.01 m: (45.5189, -122.6794) ->
   (525039.069, 5040646.225); (45.615, -122.77) -> (517932.546,
   5051298.522); (45.435, -122.555) -> (534806.402, 5031371.535).
5. `load_config` on `landmarks_file(tmp)` returns the dict; a file whose
   transform lacks "R" raises KeyError mentioning "R".

GeoTIFF:

6. `lzw_decode(enc)` gives `raw` for every `enc, raw = lzw_vector(name)`
   (short, bytes, ab3000) - tests use only these vectors and the fixtures,
   never hand-encoded LZW bits. Every pixel of all three fixtures reads back as documented (float32
   values exact; no-data pixels give None). Attributes: the UTM one has
   epsg 26910, projected True, origin (525000, 5040000), pixel (10, 10),
   nodata -999999.0; the geographic ones epsg 4269, projected False,
   origin (-122.80, 45.62), pixel (0.01, 0.01); the int16 one nodata
   -9999.0.
7. Sampling the UTM fixture: the centre of pixel (col 3, row 4)
   (x = 525035, y = 5039955) gives 94.25; 5 m east of it, 95.25; 5 m east
   and 5 m south, 93.75. The centre of the no-data pixel (col 7, row 5)
   (x = 525075, y = 5039945) gives 101.25 (its east neighbour). (525001,
   5039999), just inside the top-left corner, clamps to pixel (0, 0):
   100.25; (525199, 5039821), just inside the bottom-right corner, gives
   pixel (19, 17): 87.25. (524999, 5040000), 1 m west of the raster's
   edge, gives None, and so does (525201, 5039900).
8. `write_geotiff` then `GeoTiff`: a 7 x 5 raster of arbitrary floats
   (including negatives and 1e-3 steps) reads back exactly at float32
   precision, with the given origin, pixel, epsg and nodata.
9. `with_short_tag(utm fixture, 259, 7, ...)` (Compression 7) raises
   ValueError whose message contains "Compression 7" when opened or read; an
   EPSG:32610 file written with write_geotiff raises ValueError containing
   "32610" when opened.

Grid:

10. `SliceGrid(-1000, 1000, -950, 650, cfg)`: cells (-18, 17, -17, 11),
    width 1153, height 929, spacing 128. vertex_ck(0, 0) = (-73728,
    49152); vertex_ck(1152, 928) = (73728, -69632); ck_to_pixel inverts
    vertex_ck exactly at (0, 0), (576, 400) and (1152, 928).
11. `SliceGrid(-60, 60, -60, 60, cfg)`: cells (-2, 1, -2, 1), 129 x 129.

Heightmap (a synthetic UTM DEM with `write_geotiff`: 10 m pixels, top-left
corner (524600, 5041100), 100 x 100 pixels, elevation at pixel centre
(E, N) = 50 + 0.01 * (E - 525000) + 0.02 * (N - 5040600), so every
bilinear sample of it is exactly that plane):

12. For `SliceGrid(-60, 60, -60, 60, cfg)`, every vertex height equals
    the plane at latlon_to_utm(game_to_latlon(vertex_game_m)) times
    vertical_scale * units_per_m, within 0.01 units; missing is 0; min and
    max are the smallest and largest of those.
13. Split the same plane into two overlapping files, like real tiles:
    columns 0..50 (west, corner (524600, 5041100)) and columns 49..99
    (east, corner (525090, 5041100)). The heightmap from `[west, east]`
    equals the one-file heightmap within 0.01 units.
14. A DEM covering only the west half of the slice: missing is the number
    of vertices it does not cover (> 0), and those vertices hold the
    minimum height. A DEM nowhere near the slice raises ValueError
    "no DEM covers the slice".
15. The same plane written in geographic EPSG:4269 - top-left corner
    (-122.6860, 45.5240), 120 x 100 pixels of 0.0001 degree, each value
    the plane at latlon_to_utm of that pixel centre's (lat, lon) - gives
    heights within 1.0 units of the UTM one (the plane is not exactly
    linear in degrees, so allow the small difference).

Outputs:

16. `quantize([[0.0, 5.0, 10.0]], 0.0, 10.0)` is [[0, 32768, 65535]]
    (round(32767.5) is 32768 in Python); hi == lo gives zeros; values
    outside clamp.
17. `write_png16` then `read_png`: width, height, depth 16, colour type 0
    and every value back exactly, for a 3 x 2 grid including 0 and 65535.
    `write_raw16`: the file is width * height * 2 bytes, little-endian,
    row 0 first.
18. `write_preview` on the 129 x 129 slice with landmarks at the CK
    position of vertex (64, 64), at vertex (0, 0) (a corner: the square is
    cut off) and one far outside: returns 2; `read_png` gives colour type
    2, depth 8; the pixels at (64, 64) and (66, 66) are (255, 0, 0), (67,
    67) is not; (0, 0) and (2, 2) are red.
19. `main(["--landmarks", L, "--dem", D, "--out", out, "--box", "-60",
    "60", "-60", "60"])` with the synthetic plane returns 0 and writes the
    five files. The JSON sidecar has width 129, height 129, cells
    {"x_min": -2, "x_max": 1, "y_min": -2, "y_max": 1}, spacing_units 128,
    pixel_0_0_ck [-8192, 8192], missing 0, the transform's K0 2.5, and
    height_min_units / height_max_units equal to build_heightmap's min and
    max. The PNG's values equal quantize(heights, min, max).
20. `main` with a DEM nowhere near the box returns 2.

The DDS (the CK's import format):

21. `write_dds_r32f` with a 3 x 2 grid [[0.0, 0.5, 1.0], [-2.5, 1e-3,
    1234.5]] and the default header, read with `read_dds`: size 124, flags
    0x100F, height 2, width 3, pitch 12, depth 0, mipmaps 0, reserved1 all
    zero, pf_size 32, pf_flags 0x4, fourcc b"DX10", pf_rest all zero, caps
    [0x1000, 0, 0, 0, 0]; dx10 {"format": 41, "dimension": 3, "misc": 0,
    "array": 1, "misc2": 0}; the file is 4 + 124 + 20 + 24 = 172 bytes;
    every value back at float32 precision.
22. With `header="legacy"`: the fourcc field is the bytes of the integer
    114 (`struct.pack("<I", 114)`), there is no DXT10 header and the file
    is 152 bytes. `header="dx9"` raises ValueError.
23. `dds_values([[0.0, 5.0, 10.0]], 0.0, 10.0, "normalized")` is
    [[0.0, 0.5, 1.0]]; with "units" it is the heights unchanged; hi == lo
    with "normalized" gives zeros; "metres" raises ValueError.
24. `main` (the synthetic plane, box -60 60 -60 60) writes
    slice_a_height.dds, 129 x 129, whose values are dds_values(heights,
    min, max, "normalized") at float32 precision and the sidecar's dds is
    {"file": "slice_a_height.dds", "header": "dx10", "scale":
    "normalized"}. With `--dds-scale units --dds-header legacy` the values
    are the heights themselves and the sidecar says so.

## Playtest

On Gemini, with the two 1 m DEM tiles that cover Slice A:

- `python3 pdx_terrain.py --landmarks landmarks.json --dem
  dem/USGS_1M_10_x52y505_OR_PortlandMetro_B24.tif --dem
  dem/USGS_1M_10_x52y504_OR_PortlandMetro_B24.tif --out terrain/` prints
  `missing: 0` (these two 1 m tiles cover all of Slice A: eastings about
  521,100-529,000, northings 5,036,900-5,043,100).
- The CK's heightmap import accepts slice_a_height.dds. If it refuses the
  DX10 header, try `--dds-header legacy`; if the terrain comes out flat or
  absurdly tall, try the other `--dds-scale`.
- In slice_a_preview.png the Willamette is a dark band running north to
  south through the east half, the West Hills rise on the west side, and
  the red dots for Pioneer Square, the Rose Quarter and the OHSU tram sit
  where they should relative to the river and hills.
- height_max_units in the sidecar is near Council Crest's elevation (about
  327 m) times 0.5 times 70.02, if Council Crest falls inside the box.
- Pure-Python LZW on a 1 m tile takes minutes, not seconds; only the blocks
  Slice A touches are decoded.
