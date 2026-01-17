# -*- coding: utf-8 -*-
# Copyright 2012-2014 Hayaki Saito <user@zuse.jp>
# Copyright 2023 Lubosz Sarnecki <lubosz@gmail.com>
# Copyright (C) 2026 John Kline <jpkline43@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Windows fixed fork by Simon Kalmi Claesson @sbamboo

from io import StringIO
from PIL import Image


class SixelConverter:

    def __init__(self, file,
                 f8bit=False,
                 w=None,
                 h=None,
                 ncolor=256,
                 alpha_threshold=0,
                 chromakey=False,
                 fast=True):

        self.__alpha_threshold = alpha_threshold
        self.__chromakey = chromakey
        self._slots = [0] * 257
        self._fast = fast

        if ncolor >= 256:
            ncolor = 256

        self._ncolor = ncolor

        if f8bit:  # 8bit mode
            self.DCS = '\x90'
            self.ST = '\x9c'
        else:
            self.DCS = '\x1bP'
            self.ST = '\x1b\\'

        image = Image.open(file)
        image = image.convert("RGB").convert("P",
                                             palette=Image.Palette.ADAPTIVE,
                                             colors=ncolor)
        if w or h:
            width, height = image.size
            if not w:
                w = width
            if not h:
                h = height
            image = image.resize((w, h))

        self.palette = image.getpalette()
        self.data = image.getdata()
        self.width, self.height = image.size

        if alpha_threshold > 0:
            self.rawdata = Image.open(file).convert("RGBA").getdata()

    def __write_header(self, output):
        # start Device Control String (DCS)
        output.write(self.DCS)

        # write header
        aspect_ratio = 7  # means 1:1
        if self.__chromakey:
            background_option = 2
        else:
            background_option = 1
        dpi = 75  # dummy value
        template = '%d;%d;%dq"1;1;%d;%d'
        args = (aspect_ratio, background_option, dpi, self.width, self.height)
        output.write(template % args)

    def __write_body_bandwise(self, output, data, rawdata=None):
        """
        Correct SIXEL: process image in 6-pixel-high bands.
        Optionally apply alpha threshold by treating transparent pixels as background.
        """
        # Write palette definitions (no newlines inside DCS)
        palette = self.palette
        for n in range(0, self._ncolor):
            r = palette[n * 3 + 0] * 100 // 256
            g = palette[n * 3 + 1] * 100 // 256
            b = palette[n * 3 + 2] * 100 // 256
            output.write('#%d;2;%d;%d;%d' % (n, r, g, b))

        height = self.height
        width = self.width

        # Sentinel for "background / no pixel set"
        BG = 256

        for y in range(0, height, 6):
            band = min(6, height - y)
            buf = []
            seen = set()

            def get_pixel(p):
                """Return palette index or BG sentinel if transparent (when rawdata is provided)."""
                if rawdata is not None:
                    # rawdata[p] is (r,g,b,a)
                    if rawdata[p][3] < self.__alpha_threshold:
                        return BG
                return data[p]

            def add_node(color, start_x):
                nodes = []
                cache = None
                run = 0

                # If the first run doesn't start at x=0, we need leading "empty" cells.
                if start_x:
                    nodes.append((0, start_x))  # sixel=0 repeated start_x times

                for x in range(start_x, width):
                    p0 = y * width + x

                    six = 0
                    for i in range(0, band):
                        p = p0 + width * i
                        d = get_pixel(p)
                        if d == BG:
                            continue
                        if d == color:
                            six |= 1 << i
                        else:
                            # queue other colors found in this band, but never BG
                            if d not in seen:
                                seen.add(d)
                                add_node(d, x)

                    if cache is None:
                        cache = six
                        run = 1
                    elif six == cache:
                        run += 1
                    else:
                        nodes.append((cache, run))
                        cache = six
                        run = 1

                if cache is not None and run:
                    nodes.append((cache, run))

                buf.append((color, nodes))

            first = get_pixel(y * width)  # first pixel in this band
            if first != BG:
                seen.add(first)
                add_node(first, 0)
            else:
                # still need to discover other colors in the band
                # scan until we find a non-BG pixel or finish
                found = None
                for x in range(width):
                    d = get_pixel(y * width + x)
                    if d != BG:
                        found = d
                        break
                if found is not None:
                    seen.add(found)
                    add_node(found, x)

            # Emit all colors for this band
            for color, nodes in buf:
                output.write("#%d" % color)
                for six, count in nodes:
                    ch = chr(0x3f + six)
                    if count < 4:
                        output.write(ch * count)
                    else:
                        output.write('!%d%c' % (count, ord(ch)))
                output.write("$")  # CR within SIXEL for next color overlay

            # Next band (no trailing '-' after last)
            if y + 6 < height:
                output.write("-")

    def __write_body_section(self, output):
        data = self.data
        raw = self.rawdata if self.__alpha_threshold > 0 else None
        # Force correct bandwise encoding in all cases
        self.__write_body_bandwise(output, data, rawdata=raw)


    def __write_terminator(self, output):
        # write ST
        output.write(self.ST)  # terminate Device Control String

    def getvalue(self):
        output = StringIO()

        try:
            self.write(output)
            value = output.getvalue()

        finally:
            output.close()

        return value

    def write(self, output, body_only=False):
        if not body_only:
            self.__write_header(output)
        self.__write_body_section(output)
        if not body_only:
            self.__write_terminator(output)
