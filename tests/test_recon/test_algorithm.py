import cupy as cp
import numpy as np
from httomolib.prep.normalize import normalize as normalize_cupy
from httomolib.recon.algorithm import (
    reconstruct_tomobar,
    reconstruct_tomopy_astra,
)
from numpy.testing import assert_allclose
from tomopy.prep.normalize import normalize
import time
import pytest
from cupy.cuda import nvtx

from tests import MaxMemoryHook


@cp.testing.gpu
def test_reconstruct_tomobar_device_1(data, flats, darks, ensure_clean_memory):
    recon_data = reconstruct_tomobar(
        normalize_cupy(data, flats, darks, cutoff=10, minus_log=True),
        np.linspace(0.0 * np.pi / 180.0, 180.0 * np.pi / 180.0, data.shape[0]),
        79.5,
    )

    assert_allclose(np.mean(recon_data), 0.000798, rtol=1e-07, atol=1e-6)
    assert_allclose(np.mean(recon_data, axis=(1, 2)).sum(), 0.102106, rtol=1e-05)
    assert_allclose(np.std(recon_data), 0.006293, rtol=1e-07, atol=1e-6)
    assert_allclose(np.median(recon_data), -0.000555, rtol=1e-07, atol=1e-6)
    assert recon_data.dtype == np.float32


@cp.testing.gpu
def test_reconstruct_tomobar_device_2(data, flats, darks, ensure_clean_memory):
    recon_data = reconstruct_tomobar(
        normalize_cupy(data, flats, darks, cutoff=20.5, minus_log=False),
        np.linspace(5.0 * np.pi / 360.0, 180.0 * np.pi / 360.0, data.shape[0]),
        15.5,
    )
    assert_allclose(np.mean(recon_data), -0.00015, rtol=1e-07, atol=1e-6)
    assert_allclose(
        np.mean(recon_data, axis=(1, 2)).sum(), -0.019142, rtol=1e-06, atol=1e-5
    )
    assert_allclose(np.std(recon_data), 0.003561, rtol=1e-07, atol=1e-6)
    assert recon_data.dtype == np.float32


@cp.testing.gpu
def test_reconstruct_tomobar_device_3(data, flats, darks, ensure_clean_memory):
    
    normalized = normalize_cupy(data, flats, darks, cutoff=10, minus_log=True)
    
    hook = MaxMemoryHook(normalized.size * normalized.itemsize)
    with hook:
        recon_data = reconstruct_tomobar(
            normalized,
            np.linspace(0.0 * np.pi / 180.0, 180.0 * np.pi / 180.0, data.shape[0]),
            objsize=15
        )
    
    # make sure estimator function is within range (80% min, 100% max)
    max_mem = hook.max_mem
    actual_slices = data.shape[1]
    estimated_slices, _ = reconstruct_tomobar.meta.calc_max_slices(1, (data.shape[0], data.shape[2]), data.dtype, max_mem)
    assert estimated_slices <= actual_slices
    assert estimated_slices / actual_slices >= 0.8 
    
    assert_allclose(np.mean(recon_data), 0.00589072, rtol=1e-6)
    assert_allclose(np.mean(recon_data, axis=(1, 2)).sum(), 0.7540118, rtol=1e-6)


def test_reconstruct_tomopy_fbp_cuda(
    host_data, host_flats, host_darks, ensure_clean_memory
):
    data = normalize(host_data, host_flats, host_darks, cutoff=15.0)
    angles = np.linspace(0.0 * np.pi / 180.0, 180.0 * np.pi / 180.0, data.shape[0])

    recon_data_tomopy = reconstruct_tomopy_astra(data, angles, 79.5, algorithm="FBP_CUDA")

    assert_allclose(np.mean(recon_data_tomopy), 0.008697214, rtol=1e-07, atol=1e-8)
    assert_allclose(np.mean(recon_data_tomopy, axis=(1, 2)).sum(), 1.113243, rtol=1e-06)
    assert_allclose(np.median(recon_data_tomopy), 0.007031, rtol=1e-07, atol=1e-6)
    assert_allclose(np.std(recon_data_tomopy), 0.009089365, rtol=1e-07, atol=1e-8)

    #: check that the reconstructed data is of type float32
    assert recon_data_tomopy.dtype == np.float32


@cp.testing.gpu
@pytest.mark.perf
def test_reconstruct_tomobar_performance(ensure_clean_memory):
    dev = cp.cuda.Device()
    data_host = np.random.random_sample(size=(1801, 5, 2560)).astype(np.float32) * 2.0
    data = cp.asarray(data_host, dtype=np.float32)
    angles = np.linspace(0.0 * np.pi / 180.0, 180.0 * np.pi / 180.0, data.shape[0])
    cor = 79.5

    # cold run first
    reconstruct_tomobar(data, angles, cor)
    dev.synchronize()

    start = time.perf_counter_ns()
    nvtx.RangePush("Core")
    for _ in range(10):
        reconstruct_tomobar(data, angles, cor)
    nvtx.RangePop()
    dev.synchronize()
    duration_ms = float(time.perf_counter_ns() - start) * 1e-6 / 10

    assert "performance in ms" == duration_ms
