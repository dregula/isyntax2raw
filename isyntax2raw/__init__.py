#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2019 Glencoe Software, Inc. All rights reserved.
#
# This software is distributed under the terms described by the LICENSE.txt
# file you can find at the root of the distribution bundle.  If the file is
# missing please request a copy by contacting info@glencoesoftware.com

import json
import logging
import math
import os

import numpy as np
import pixelengine
import softwarerendercontext
import softwarerenderbackend
import zarr

from datetime import datetime
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from threading import BoundedSemaphore

from PIL import Image
from kajiki import PackageLoader
from tifffile import imwrite


log = logging.getLogger(__name__)

