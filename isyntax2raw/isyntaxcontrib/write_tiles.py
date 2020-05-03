
from isyntax2raw import log
import os
import json
from tifffile import imwrite
from datetime import datetime
from kajiki import PackageLoader
from isyntax2raw.isyntaxcontrib.max_queue_pool import MaxQueuePool
import pixelengine
import softwarerendercontext
import softwarerenderbackend
import math
import numpy as np
import zarr
from PIL import Image
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait


class WriteTiles(object):

    def __init__(
        self, tile_width, tile_height, resolutions, file_type, max_workers,
        batch_size, input_path, output_path
    ):
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.resolutions = resolutions
        self.file_type = file_type
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.input_path = input_path
        self.slide_directory = output_path
        self.zarr_store = None
        self.zarr_group = None

        os.mkdir(self.slide_directory)

        render_context = softwarerendercontext.SoftwareRenderContext()
        render_backend = softwarerenderbackend.SoftwareRenderBackend()

        self.pixel_engine = pixelengine.PixelEngine(
            render_backend, render_context
        )
        self.pixel_engine["in"].open(self.input_path, "ficom")

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.pixel_engine["in"].close()

    def write_metadata(self):
        """write metadata to a JSON file"""
        pe_in = self.pixel_engine["in"]
        metadata_file = os.path.join(self.slide_directory, "METADATA.json")

        with open(metadata_file, "w", encoding="utf-8") as f:
            metadata = {
                "Barcode":
                    pe_in.BARCODE,
                "DICOM acquisition date":
                    pe_in.DICOM_ACQUISITION_DATETIME,
                "DICOM last calibration date":
                    pe_in.DICOM_DATE_OF_LAST_CALIBRATION,
                "DICOM time of last calibration":
                    pe_in.DICOM_TIME_OF_LAST_CALIBRATION,
                "DICOM manufacturer":
                pe_in.DICOM_MANUFACTURER,
                "DICOM manufacturer model name":
                    pe_in.DICOM_MANUFACTURERS_MODEL_NAME,
                "DICOM device serial number":
                    pe_in.DICOM_DEVICE_SERIAL_NUMBER,
                "Color space transform":
                    pe_in.colorspaceTransform(),
                "Block size":
                    pe_in.blockSize(),
                "Number of tiles":
                    pe_in.numTiles(),
                "Bits stored":
                    pe_in.bitsStored(),
                "Derivation description":
                    pe_in.DICOM_DERIVATION_DESCRIPTION,
                "DICOM software version":
                    pe_in.DICOM_SOFTWARE_VERSIONS,
                "Number of images":
                    pe_in.numImages()
            }

            size_x = 0
            size_y = 0
            label_x = 0
            label_y = 0
            macro_x = 0
            macro_y = 0
            pixel_size_x = 1.0
            pixel_size_y = 1.0

            for image in range(pe_in.numImages()):
                img = pe_in[image]
                image_metadata = {
                    "Image type": img.IMAGE_TYPE,
                    "DICOM lossy image compression method":
                        img.DICOM_LOSSY_IMAGE_COMPRESSION_METHOD,
                    "DICOM lossy image compression ratio":
                        img.DICOM_LOSSY_IMAGE_COMPRESSION_RATIO,
                    "DICOM derivation description":
                        img.DICOM_DERIVATION_DESCRIPTION,
                    "Image dimension names":
                        img.IMAGE_DIMENSION_NAMES,
                    "Image dimension types":
                        img.IMAGE_DIMENSION_TYPES,
                    "Image dimension units":
                        img.IMAGE_DIMENSION_UNITS,
                    "Image dimension ranges":
                        img.IMAGE_DIMENSION_RANGES,
                    "Image dimension discrete values":
                        img.IMAGE_DIMENSION_DISCRETE_VALUES_STRING,
                    "Image scale factor":
                        img.IMAGE_SCALE_FACTOR
                }

                if img.IMAGE_TYPE == "WSI":
                    pixel_size_x = img.IMAGE_SCALE_FACTOR[0]
                    pixel_size_y = img.IMAGE_SCALE_FACTOR[1]

                    view = pe_in.SourceView()
                    image_metadata["Bits allocated"] = view.bitsAllocated()
                    image_metadata["Bits stored"] = view.bitsStored()
                    image_metadata["High bit"] = view.highBit()
                    image_metadata["Pixel representation"] = \
                        view.pixelRepresentation()
                    image_metadata["Planar configuration"] = \
                        view.planarConfiguration()
                    image_metadata["Samples per pixel"] = \
                        view.samplesPerPixel()
                    image_metadata["Number of levels"] = \
                        pe_in.numLevels()

                    for resolution in range(pe_in.numLevels()):
                        dim_ranges = view.dimensionRanges(resolution)
                        level_size_x = self.get_size(dim_ranges[0])
                        level_size_y = self.get_size(dim_ranges[1])
                        image_metadata["Level sizes #%s" % resolution] = {
                            "X": level_size_x,
                            "Y": level_size_y
                        }
                        if resolution == 0:
                            size_x = level_size_x
                            size_y = level_size_y

                elif img.IMAGE_TYPE == "LABELIMAGE":
                    label_x = self.get_size(img.IMAGE_DIMENSION_RANGES[0])
                    label_y = self.get_size(img.IMAGE_DIMENSION_RANGES[1])

                elif img.IMAGE_TYPE == "MACROIMAGE":
                    macro_x = self.get_size(img.IMAGE_DIMENSION_RANGES[0])
                    macro_y = self.get_size(img.IMAGE_DIMENSION_RANGES[1])

                metadata["Image #" + str(image)] = image_metadata

            json.dump(metadata, f)

        timestamp = str(pe_in.DICOM_ACQUISITION_DATETIME)
        ome_timestamp = datetime.strptime(timestamp, "%Y%m%d%H%M%S.%f")

        xml_values = {
            'image': {
                'name': pe_in.BARCODE,
                'acquisitionDate': ome_timestamp.isoformat(),
                'description': pe_in.DICOM_DERIVATION_DESCRIPTION,
                'pixels': {
                    'sizeX': int(size_x),
                    'sizeY': int(size_y),
                    'physicalSizeX': pixel_size_x,
                    'physicalSizeY': pixel_size_y
                }
            },
            'label': {
                'pixels': {
                    'sizeX': int(label_x),
                    'sizeY': int(label_y)
                }
            },
            'macro': {
                'pixels': {
                    'sizeX': int(macro_x),
                    'sizeY': int(macro_y)
                }
            }
        }
        loader = PackageLoader()
        template = loader.import_("isyntax2raw.resources.ome_template")
        xml = template(xml_values).render()
        ome_xml_file = os.path.join(self.slide_directory, "METADATA.ome.xml")
        with open(ome_xml_file, "w") as omexml:
            omexml.write(xml)

    @staticmethod
    def get_size(dim_range):
        """calculate the length in pixels of a dimension"""
        v = (dim_range[2] - dim_range[0]) / dim_range[1]
        if not v.is_integer():
            # isyntax infrastructure should ensure this always divides
            # evenly
            raise ValueError(
                '(%d - %d) / %d results in remainder!' % (
                    dim_range[2], dim_range[0], dim_range[1]
                )
            )
        return v

    def write_label_image(self):
        """write the label image (if present) as a JPEG file"""
        self.write_image_type("LABELIMAGE")

    def write_macro_image(self):
        """write the macro image (if present) as a JPEG file"""
        self.write_image_type("MACROIMAGE")

    def find_image_type(self, image_type):
        """look up a given image type in the pixel engine"""
        pe_in = self.pixel_engine["in"]
        for index in range(pe_in.numImages()):
            if image_type == pe_in[index].IMAGE_TYPE:
                return pe_in[index]
        return None

    def write_image_type(self, image_type):
        """write an image of the specified type"""
        image_container = self.find_image_type(image_type)
        if image_container is not None:
            pixels = image_container.IMAGE_DATA
            image_file = os.path.join(
                self.slide_directory, '%s.jpg' % image_type
            )
            with open(image_file, "wb") as image:
                image.write(pixels)
                log.info("wrote %s image" % image_type)

    def create_tile_directory(self, resolution, width, height):
        tile_directory = os.path.join(
            self.slide_directory, str(resolution)
        )
        if self.file_type in ("n5", "zarr"):
            tile_directory = os.path.join(
                self.slide_directory, "pyramid.%s" % self.file_type
            )
            self.zarr_store = zarr.DirectoryStore(tile_directory)
            if self.file_type == "n5":
                self.zarr_store = zarr.N5Store(tile_directory)
            self.zarr_group = zarr.group(store=self.zarr_store)
            self.zarr_group.create_dataset(
                str(resolution), shape=(3, height, width),
                chunks=(None, self.tile_height, self.tile_width), dtype='B'
            )
        else:
            os.mkdir(tile_directory)
        return tile_directory

    def get_tile_filename(self, tile_directory, x_start, y_start):
        filename = os.path.join(
            os.path.join(tile_directory, str(x_start)),
            "%s.%s" % (y_start, self.file_type)
        )
        if self.file_type in ("n5", "zarr"):
            filename = tile_directory
        return filename

    @staticmethod
    def make_planar(pixels, tile_width, tile_height):
        r = pixels[0::3]
        g = pixels[1::3]
        b = pixels[2::3]
        for v in (r, g, b):
            v.shape = (tile_height, tile_width)
        return np.array([r, g, b])

    def write_pyramid(self):
        """write the slide's pyramid as a set of tiles"""
        pe_in = self.pixel_engine["in"]
        image_container = self.find_image_type("WSI")

        scanned_areas = image_container.IMAGE_VALID_DATA_ENVELOPES
        if scanned_areas is None:
            raise RuntimeError("No valid data envelopes")

        if self.resolutions is None:
            resolutions = range(pe_in.numLevels())
        else:
            resolutions = range(self.resolutions)

        def write_tile(
            pixels, resolution, x_start, y_start, tile_width, tile_height,
            filename
        ):
            """
            :type pixels: bytearray
            :param resolution:
            :param x_start:
            :param y_start:
            :param tile_width:
            :param tile_height:
            :param filename:
            """
            x_end = x_start + tile_width
            y_end = y_start + tile_height
            try:
                if self.file_type in ("n5", "zarr"):
                    # Special case for N5/Zarr which has a single n-dimensional
                    # array representation on disk
                    pixels = self.make_planar(pixels, tile_width, tile_height)
                    z = self.zarr_group[str(resolution)]
                    z[:, y_start:y_end, x_start:x_end] = pixels
                elif self.file_type == 'tiff':
                    # Special case for TIFF to save in planar mode using
                    # deinterleaving and the tifffile library; planar data
                    # is much more performant with the Bio-Formats API
                    pixels = self.make_planar(pixels, tile_width, tile_height)
                    with open(filename, 'wb') as destination:
                        # noinspection PyTypeChecker
                        imwrite(destination, pixels, planarconfig='SEPARATE')
                else:
                    with Image.frombuffer(
                        'RGB', (int(tile_width), int(tile_height)),
                        pixels, 'raw', 'RGB', 0, 1
                    ) as source, open(filename, 'wb') as destination:
                        source.save(destination)
            # noinspection PyBroadException
            except Exception:
                log.error(
                    "Failed to write tile [:, %d:%d, %d:%d] to %s" % (
                        x_start, x_end, y_start, y_end, filename
                    ), exc_info=True
                )

        source_view = pe_in.SourceView()
        for resolution in resolutions:
            # assemble data envelopes (== scanned areas) to extract for
            # this level
            dim_ranges = source_view.dimensionRanges(resolution)
            log.info("dimension ranges = %s" % dim_ranges)
            resolution_x_size = self.get_size(dim_ranges[0])
            resolution_y_size = self.get_size(dim_ranges[1])
            scale_x = dim_ranges[0][1]
            scale_y = dim_ranges[1][1]

            x_tiles = math.ceil(resolution_x_size / self.tile_width)
            y_tiles = math.ceil(resolution_y_size / self.tile_height)

            log.info("# of X (%d) tiles = %d" % (self.tile_width, x_tiles))
            log.info("# of Y (%d) tiles = %d" % (self.tile_height, y_tiles))

            # create one tile directory per resolution level if required
            tile_directory = self.create_tile_directory(
                resolution, resolution_x_size, resolution_y_size
            )

            patches, patch_ids = self.create_patch_list(
                dim_ranges, [x_tiles, y_tiles],
                [self.tile_width, self.tile_height],
                tile_directory
            )
            envelopes = source_view.dataEnvelopes(resolution)
            jobs = []
            with MaxQueuePool(ThreadPoolExecutor, self.max_workers) as pool:
                for i in range(0, len(patches), self.batch_size):
                    # requestRegions(
                    #    self: pixelengine.PixelEngine.View,
                    #    region: List[List[int]],
                    #    dataEnvelopes: pixelengine.PixelEngine.DataEnvelopes,
                    #    enableAsyncRendering: bool=True,
                    #    backgroundColor: List[int]=[0, 0, 0],
                    #    bufferType:
                    #      pixelengine.PixelEngine.BufferType=BufferType.RGB
                    # ) -> list
                    regions = source_view.requestRegions(
                        patches[i:i + self.batch_size], envelopes, True,
                        [0, 0, 0]
                    )
                    while regions:
                        regions_ready = self.pixel_engine.waitAny(regions)
                        for region_index, region in enumerate(regions_ready):
                            view_range = region.range
                            log.debug(
                                "processing tile %s (%s regions ready; "
                                "%s regions left; %s jobs)" % (
                                    view_range, len(regions_ready),
                                    len(regions), len(jobs)
                                )
                            )
                            x_start, x_end, y_start, y_end, level = view_range
                            width = 1 + (x_end - x_start) / scale_x
                            # isyntax infrastructure should ensure this always
                            # divides evenly
                            if not width.is_integer():
                                raise ValueError(
                                    '(1 + (%d - %d) / %d results in '
                                    'remainder!' % (
                                        x_end, x_start, scale_x
                                    )
                                )
                            width = int(width)
                            height = 1 + (y_end - y_start) / scale_y
                            # isyntax infrastructure should ensure this always
                            # divides evenly
                            if not height.is_integer():
                                raise ValueError(
                                    '(1 + (%d - %d) / %d results in '
                                    'remainder!' % (
                                        y_end, y_start, scale_y
                                    )
                                )
                            height = int(height)
                            pixel_buffer_size = width * height * 3
                            pixels = np.empty(pixel_buffer_size, dtype='B')
                            patch_id = patch_ids.pop(regions.index(region))
                            x_start, y_start = patch_id
                            x_start *= self.tile_width
                            y_start *= self.tile_height

                            region.get(pixels)
                            regions.remove(region)

                            filename = self.get_tile_filename(
                                tile_directory, x_start, y_start
                            )
                            jobs.append(pool.submit(
                                write_tile, pixels, resolution,
                                x_start, y_start, width, height,
                                filename
                            ))
            wait(jobs, return_when=ALL_COMPLETED)

    def create_x_directory(self, tile_directory, x_start):
        if self.file_type in ("n5", "zarr"):
            return

        x_directory = os.path.join(tile_directory, str(x_start))
        if not os.path.exists(x_directory):
            os.mkdir(x_directory)

    def create_patch_list(
        self, dim_ranges, tiles, tile_size, tile_directory
    ):
        resolution_x_end = dim_ranges[0][2]
        resolution_y_end = dim_ranges[1][2]
        origin_x = dim_ranges[0][0]
        origin_y = dim_ranges[1][0]
        tiles_x, tiles_y = tiles

        patches = []
        patch_ids = []
        scale_x = dim_ranges[0][1]
        scale_y = dim_ranges[1][1]
        # We'll use the X scale to calculate our level.  If the X and Y scales
        # are not equivalent or not a power of two this will not work but that
        # seems *highly* unlikely
        level = math.log2(scale_x)
        if scale_x != scale_y or not level.is_integer():
            raise ValueError(
                "scale_x=%d scale_y=%d do not match isyntax format "
                "assumptions!" % (
                    scale_x, scale_y
                )
            )
        level = int(level)
        tile_size_x = tile_size[0] * scale_x
        tile_size_y = tile_size[1] * scale_y
        for y in range(tiles_y):
            y_start = origin_y + (y * tile_size_y)
            # Subtracting "scale_y" here makes no sense but it works and
            # reflects the isyntax SDK examples
            y_end = min(
                (y_start + tile_size_y) - scale_y, resolution_y_end - scale_y
            )
            for x in range(tiles_x):
                x_start = origin_x + (x * tile_size_x)
                # Subtracting "scale_x" here makes no sense but it works and
                # reflects the isyntax SDK examples
                x_end = min(
                    (x_start + tile_size_x) - scale_x,
                    resolution_x_end - scale_x
                )
                patch = [x_start, x_end, y_start, y_end, level]
                patches.append(patch)
                # Associating spatial information (tile X and Y offset) in
                # order to identify the patches returned asynchronously
                patch_ids.append((x, y))

                self.create_x_directory(tile_directory, x * self.tile_width)
        return patches, patch_ids
