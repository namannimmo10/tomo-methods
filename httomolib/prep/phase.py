#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# Copyright 2022 Diamond Light Source Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ---------------------------------------------------------------------------
# Created By  : Tomography Team at DLS <scientificsoftware@diamond.ac.uk>
# Created Date: 01 November 2022
# version ='0.1'
# ---------------------------------------------------------------------------
"""Modules for phase retrieval and phase-contrast enhancement"""

import math

import cupy as cp
import cupyx
import numpy as np
from cupy.cuda import nvtx

__all__ = [
    'fresnel_filter',
    'paganin_filter',
    'retrieve_phase',
]

# Define constants used in phase retrieval method
BOLTZMANN_CONSTANT = 1.3806488e-16  # [erg/k]
SPEED_OF_LIGHT = 299792458e+2  # [cm/s]
PI = 3.14159265359
PLANCK_CONSTANT = 6.58211928e-19  # [keV*s]


# CuPy implementation of Fresnel filter ported from Savu
def fresnel_filter(
    mat: cp.ndarray,
    pattern: str,
    ratio: float,
    apply_log: bool = True
) -> cp.ndarray:
    """
    Apply Fresnel filter.

    Parameters
    ----------
    mat : cp.ndarray
        The data to apply filtering to.

    pattern : str
        Choose 'PROJECTION' for filtering projection, otherwise, will be handled
        generically for other cases.

    ratio : float
        Control the strength of the filter. Greater is stronger.

    apply_log : bool, optional
        Apply negative log function to the data being filtered.

    Returns
    -------
    cp.ndarray
        The filtered data.
    """

    if mat.ndim == 2:
        mat = cp.expand_dims(mat, 0)

    if mat.ndim != 3:
        raise ValueError(f"Invalid number of dimensions in data: {mat.ndim},"
                         " please provide a stack of 2D projections.")

    if apply_log is True:
        mat = -cp.log(mat)

    # Define window
    (depth1, height1, width1) = mat.shape[:3]
    window = _make_window(height1, width1, ratio, pattern)
    pad_width = min(150, int(0.1 * width1))

    # Regardless of working with projections or sinograms, the rows and columns
    # in the images to filter are in the same dimensions of the data: rows in
    # dimension 1, columns in dimension 2 (ie, for projection images, `nrow` is
    # the number of rows in a projection image, and for sinogram images, `nrow`
    # is the number of rows in a sinogram image).
    (_, nrow, ncol) = mat.shape

    # Define array to hold result. Note that, due to the padding applied, the
    # shape of the filtered images are different to the shape of the
    # original/unfiltered images.
    padded_height = mat.shape[1] + pad_width * 2
    res_height = min(nrow, padded_height - pad_width)
    padded_width = mat.shape[2] + pad_width * 2
    res_width = min(ncol, padded_width - pad_width)
    res = cp.zeros((mat.shape[0], res_height, res_width))

    # Loop over images and apply filter
    for i in range(mat.shape[0]):
        if pattern == "PROJECTION":
            top_drop = 10  # To remove the time stamp in some data
            mat_pad = cp.pad(mat[i][top_drop:], (
                (pad_width + top_drop, pad_width), (pad_width, pad_width)),
                mode="edge")
            win_pad = cp.pad(window, pad_width, mode="edge")
            mat_dec = \
                cp.fft.ifft2(cp.fft.fft2(mat_pad) / cp.fft.ifftshift(win_pad))
            mat_dec = cp.real(
                mat_dec[pad_width:pad_width + nrow, pad_width:pad_width + ncol])
            res[i] = mat_dec
        else:
            mat_pad = \
                cp.pad(mat[i], ((0, 0), (pad_width, pad_width)), mode='edge')
            win_pad = cp.pad(window, ((0, 0), (pad_width, pad_width)),
                             mode="edge")
            mat_fft = cp.fft.fftshift(cp.fft.fft(mat_pad), axes=1) / win_pad
            mat_dec = cp.fft.ifft(cp.fft.ifftshift(mat_fft, axes=1))
            mat_dec = cp.real(mat_dec[:, pad_width:pad_width + ncol])
            res[i] = mat_dec

    if apply_log is True:
        res = cp.exp(-res)

    return cp.asarray(res, dtype=cp.float32)


def _make_window(height, width, ratio, pattern):
    center_hei = int(cp.ceil((height - 1) * 0.5))
    center_wid = int(cp.ceil((width - 1) * 0.5))
    if pattern == "PROJECTION":
        ulist = (1.0 * cp.arange(0, width) - center_wid) / width
        vlist = (1.0 * cp.arange(0, height) - center_hei) / height
        u, v = cp.meshgrid(ulist, vlist)
        win2d = 1.0 + ratio * (u ** 2 + v ** 2)
    else:
        ulist = (1.0 * cp.arange(0, width) - center_wid) / width
        win1d = 1.0 + ratio * ulist ** 2
        win2d = cp.tile(win1d, (height, 1))

    return win2d


## %%%%%%%%%%%%%%%%%%%%%%% paganin_filter %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
#: CuPy implementation of Paganin filter from Savu
@nvtx.annotate()
def paganin_filter(
    data: cp.ndarray,
    ratio: float = 250.0,
    energy: float = 53.0,
    distance: float = 1.0,
    resolution: float = 1.28,
    pad_y: int = 100,
    pad_x: int = 100,
    pad_method: str = 'edge',
    increment: float = 0.0
) -> cp.ndarray:
    """
    Apply Paganin filter (for denoising or contrast enhancement) to
    projections.

    Parameters
    ----------
    data : cp.ndarray
        The stack of projections to filter.

    ratio : float, optional
        Ratio of delta/beta.

    energy : float, optional
        Beam energy in keV.

    distance : float, optional
        Distance from sample to detector in metres.

    resolution : float, optional
        Pixel size in microns.

    pad_y : int, optional
        Pad the top and bottom of projections.

    pad_x : int, optional
        Pad the left and right of projections.

    pad_method : str, optional
        Numpy pad method to use.

    increment : float, optional
        Increment all values by this amount before taking the log.

    Returns
    -------
    cp.ndarray
        The stack of filtered projections.
    """
    # Check the input data is valid
    if data.ndim == 2:
        data = cp.expand_dims(data, 0)

    if data.ndim != 3:
        raise ValueError(f"Invalid number of dimensions in data: {data.ndim},"
                         " please provide a stack of 2D projections.")

    # Setup various values for the filter
    _, height, width = data.shape
    micron = 10 ** (-6)
    keV = 1000.0
    energy *= keV
    resolution *= micron
    wavelength = (1240.0 / energy) * 10.0 ** (-9)

    height1 = height + 2 * pad_y
    width1 = width + 2 * pad_x
    centery = cp.ceil(height1 / 2.0) - 1.0
    centerx = cp.ceil(width1 / 2.0) - 1.0

    # Define the paganin filter, taking into account the padding that will be
    # applied to the projections (if any)
    dpx = 1.0 / (width1 * resolution)
    dpy = 1.0 / (height1 * resolution)
    pxlist = (cp.arange(width1) - centerx) * dpx
    pylist = (cp.arange(height1) - centery) * dpy
    pxx = cp.zeros((height1, width1), dtype=cp.float32)
    pxx[:, 0:width1] = pxlist
    pyy = cp.zeros((height1, width1), dtype=cp.float32)
    pyy[0:height1, :] = cp.reshape(pylist, (height1, 1))
    pd = (pxx * pxx + pyy * pyy) * wavelength * distance * math.pi

    filter1 = 1.0 + ratio * pd
    filtercomplex = filter1 + filter1 * 1j

    # Apply padding to all the 2D projections
    data = cp.pad(data, ((0, 0), (pad_y, pad_y), (pad_x, pad_x)),
                  mode=pad_method)

    # Define array to hold result, which will not have the padding applied to it
    res = cp.zeros((data.shape[0], height, width), dtype=cp.float32)

    # Loop over projections and apply the filter
    for i in range(data.shape[0]):
        # Noted performance <- COMMENT PRESERVED FROM SAVU CODE, NOT SURE WHAT IT
        # MEANS YET THOUGH...
        proj = cp.nan_to_num(data[i])
        proj[proj == 0] = 1.0
        pci1 = cp.fft.fft2(cp.asarray(proj, dtype=cp.float32))
        pci2 = cp.fft.fftshift(pci1) / filtercomplex
        fpci = cp.abs(cp.fft.ifft2(pci2))
        proj = -0.5 * ratio * cp.log(fpci + increment)
        res[i] = proj[pad_y: pad_y + height, pad_x: pad_x + width]

    return res


## %%%%%%%%%%%%%%%%%%%%%%% retrieve_phase %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
#: CuPy implementation of retrieve_phase from TomoPy
@nvtx.annotate()
def retrieve_phase(
    tomo: cp.ndarray,
    pixel_size: float = 1e-4,
    dist: float = 50.,
    energy: float = 20.,
    alpha: float = 1e-3,
    pad: bool = True
) -> cp.ndarray:
    """
    Perform single-step phase retrieval from phase-contrast measurements
    :cite:`Paganin:02`.

    Parameters
    ----------
    tomo : cp.ndarray
        3D tomographic data.
    pixel_size : float, optional
        Detector pixel size in cm.
    dist : float, optional
        Propagation distance of the wavefront in cm.
    energy : float, optional
        Energy of incident wave in keV.
    alpha : float, optional
        Regularization parameter.
    pad : bool, optional
        If True, extend the size of the projections by padding with zeros.

    Returns
    -------
    cp.ndarray
        Approximated 3D tomographic phase data.
    """

    # Check the input data is valid
    if tomo.ndim == 2:
        tomo = cp.expand_dims(tomo, 0)

    if tomo.ndim != 3:
        raise ValueError(f"Invalid number of dimensions in data: {tomo.ndim},"
                         " please provide a stack of 2D projections.")

    # New dimensions and pad value after padding.
    py, pz, val = _calc_pad(tomo, pixel_size, dist, energy, pad)

    # Compute the reciprocal grid.
    _, dy, dz = tomo.shape
    w2 = _reciprocal_grid(pixel_size, dy + 2 * py, dz + 2 * pz)

    # Filter in Fourier space.
    phase_filter = cp.fft.fftshift(
        _paganin_filter_factor(energy, dist, alpha, w2))

    prj = cp.full((dy + 2*py, dz + 2*pz), val, dtype=cp.float32)

    # Apply phase retrieval
    return _retrieve_phase(tomo, phase_filter, py, pz, prj, pad)


def _retrieve_phase(
    tomo: cp.ndarray,
    phase_filter: cp.ndarray,
    px: int,
    py: int,
    prj: cp.ndarray,
    pad: bool
) -> cp.ndarray:
    _, dy, dz = tomo.shape
    num_projs = tomo.shape[0]
    normalized_phase_filter = phase_filter / phase_filter.max()
    for m in range(num_projs):
        prj[px:dy + px, py:dz + py] = tomo[m]
        prj[:px] = prj[px]
        prj[-px:] = prj[-px-1]
        prj[:, :py] = prj[:, py][:, cp.newaxis]
        prj[:, -py:] = prj[:, -py-1][:, cp.newaxis]
        # TomoPy has its own 2D FFT implementations
        # https://github.com/tomopy/tomopy/blob/master/source/tomopy/util/misc.py,
        # the NumPy equivalent in CuPy has been used as an alternative
        # https://docs.cupy.dev/en/stable/reference/generated/cupy.fft.fft2.html#.
        fproj = cp.fft.fft2(prj)
        fproj *= normalized_phase_filter
        proj = cp.real(cp.fft.ifft2(fproj))
        if pad:
            proj = proj[px:dy + px, py:dz + py]
        tomo[m] = proj
    return tomo


def _calc_pad(tomo: cp.ndarray, pixel_size: float, dist: float, energy: float,
              pad: bool) -> tuple[int, int, float]:
    """
    Calculate new dimensions and pad value after padding.

    Parameters
    ----------
    tomo : ndarray
        3D tomographic data.
    pixel_size : float
        Detector pixel size in cm.
    dist : float
        Propagation distance of the wavefront in cm.
    energy : float
        Energy of incident wave in keV.
    pad : bool
        If True, extend the size of the projections by padding with zeros.

    Returns
    -------
    int
        Pad amount in projection axis.
    int
        Pad amount in sinogram axis.
    float
        Pad value.
    """
    _, dy, dz = tomo.shape
    wavelength = _wavelength(energy)
    py, pz, val = 0, 0, 0
    if pad:
        val = _calc_pad_val(tomo)
        py = _calc_pad_width(dy, pixel_size, wavelength, dist)
        pz = _calc_pad_width(dz, pixel_size, wavelength, dist)
    return py, pz, val


def _wavelength(energy: float) -> float:
    return 2 * PI * PLANCK_CONSTANT * SPEED_OF_LIGHT / energy


def _paganin_filter_factor(energy: float, dist: float, alpha: float,
                           w2: cp.ndarray) -> cp.ndarray:
    return 1 / (_wavelength(energy) * dist * w2 / (4 * PI) + alpha)


def _calc_pad_width(dim: int, pixel_size: float, wavelength: float,
                    dist: float) -> int:
    pad_pix = cp.ceil(PI * wavelength * dist / pixel_size ** 2)
    return int((pow(2, cp.ceil(cp.log2(dim + pad_pix))) - dim) * 0.5)


def _calc_pad_val(tomo: cp.ndarray) -> float:
    return cp.mean((tomo[..., 0] + tomo[..., -1]) * 0.5)


def _reciprocal_grid(pixel_size: float, nx: int, ny: int) -> cp.ndarray:
    """
    Calculate reciprocal grid.

    Parameters
    ----------
    pixel_size : float
        Detector pixel size in cm.
    nx, ny : int
        Size of the reciprocal grid along x and y axes.

    Returns
    -------
    ndarray
        Grid coordinates.
    """
    # Sampling in reciprocal space.
    indx = _reciprocal_coord(pixel_size, nx)
    indy = _reciprocal_coord(pixel_size, ny)
    cp.square(indx, out=indx)
    cp.square(indy, out=indy)
    # TODO: Explicitly generate the result equivalent to `np.add.outer()` using
    # nested loops, since CuPy doesn't yet have a released version which
    # provides `ufunc.outer`, see https://github.com/cupy/cupy/pull/7049,
    # https://github.com/cupy/cupy/issues/7082, and
    # https://github.com/cupy/cupy/issues/6866.
    #
    # When `ufunc.outer` is supported in CuPy, this code can be updated
    # accordingly.
    grid = cp.empty((len(indx),len(indy)))
    for i in range(len(indx)):
        for j in range(len(indy)):
            grid[i,j] = cp.add(indx[i], indy[j])
    return grid


def _reciprocal_coord(pixel_size: float, num_grid: int) -> cp.ndarray:
    """
    Calculate reciprocal grid coordinates for a given pixel size
    and discretization.

    Parameters
    ----------
    pixel_size : float
        Detector pixel size in cm.
    num_grid : int
        Size of the reciprocal grid.

    Returns
    -------
    ndarray
        Grid coordinates.
    """
    n = num_grid - 1
    rc = cp.arange(-n, num_grid, 2, dtype=cp.float32)
    rc *= 0.5 / (n * pixel_size)
    return  rc