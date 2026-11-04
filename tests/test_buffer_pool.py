"""
Unit tests for high-performance zero-copy FrameBufferPool.
"""
import threading
import numpy as np
import pytest

from openreview.core.buffer_pool import FrameBufferPool
from openreview.core.reader import ensure_c_contiguous_rgba


@pytest.fixture(autouse=True)
def reset_pool():
    FrameBufferPool.reset_instance()
    yield
    FrameBufferPool.reset_instance()


def test_buffer_pool_singleton():
    p1 = FrameBufferPool.instance()
    p2 = FrameBufferPool.instance()
    assert p1 is p2
    assert p1.enabled is True


def test_buffer_pool_acquire_and_release():
    pool = FrameBufferPool.instance()
    pool.clear()

    shape = (1080, 1920, 4)
    buf1 = pool.acquire(shape, np.float16)
    assert buf1.shape == shape
    assert buf1.dtype == np.float16
    assert buf1.flags.c_contiguous

    # First acquisition should miss
    stats = pool.get_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 0

    # Release back to pool
    success = pool.release(buf1)
    assert success is True
    stats = pool.get_stats()
    assert stats["releases"] == 1
    assert stats["total_buffers"] == 1

    # Re-acquire should hit the recycled buffer
    buf2 = pool.acquire(shape, np.float16)
    assert buf2 is buf1  # Exact same memory reference!
    stats = pool.get_stats()
    assert stats["hits"] == 1
    assert stats["total_buffers"] == 0


def test_buffer_pool_shape_dtype_isolation():
    pool = FrameBufferPool.instance()
    pool.clear()

    b_f16 = pool.acquire((1080, 1920, 4), np.float16)
    b_f32 = pool.acquire((1080, 1920, 4), np.float32)
    b_small = pool.acquire((540, 960, 4), np.float16)

    pool.release(b_f16)
    pool.release(b_f32)
    pool.release(b_small)

    stats = pool.get_stats()
    assert stats["total_buffers"] == 3
    assert stats["active_keys"] == 3

    # Re-acquire with float32 should return b_f32
    re_f32 = pool.acquire((1080, 1920, 4), np.float32)
    assert re_f32 is b_f32

    # Re-acquire with small shape should return b_small
    re_small = pool.acquire((540, 960, 4), np.float16)
    assert re_small is b_small


def test_buffer_pool_max_capacity_bound():
    pool = FrameBufferPool.instance()
    pool.clear()
    pool.max_buffers_per_key = 3

    shape = (100, 100, 4)
    buffers = [pool.acquire(shape, np.float16) for _ in range(5)]

    for b in buffers:
        pool.release(b)

    stats = pool.get_stats()
    # Should be capped at max_buffers_per_key = 3
    assert stats["total_buffers"] == 3


def test_buffer_pool_disabled():
    pool = FrameBufferPool.instance()
    pool.enabled = False

    buf = pool.acquire((100, 100, 4), np.float16)
    released = pool.release(buf)
    assert released is False

    stats = pool.get_stats()
    assert stats["total_buffers"] == 0
    assert stats["hits"] == 0


def test_buffer_pool_thread_safety():
    pool = FrameBufferPool.instance()
    pool.clear()
    pool.max_buffers_per_key = 64

    shape = (256, 256, 4)
    errors = []

    def worker():
        try:
            for _ in range(50):
                arr = pool.acquire(shape, np.float16)
                arr.fill(1.0)
                pool.release(arr)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    stats = pool.get_stats()
    assert stats["hits"] > 0
    assert stats["releases"] > 0


def test_ensure_c_contiguous_rgba_with_pool():
    pool = FrameBufferPool.instance()
    pool.clear()
    pool.enabled = True

    # Allocate a non-contiguous slice
    large = np.zeros((100, 100, 4), dtype=np.float16)
    sliced = large[::2, ::2, :]
    assert not sliced.flags.c_contiguous

    # First call will acquire from pool (miss) and copy
    out1 = ensure_c_contiguous_rgba(sliced)
    assert out1.flags.c_contiguous
    assert out1.shape == sliced.shape

    # Release out1 back to pool
    pool.release(out1)

    # Second call should reuse the buffer
    out2 = ensure_c_contiguous_rgba(sliced)
    assert out2 is out1  # Zero-copy recycling!


def test_image_io_zero_copy_recycling(tmp_path):
    from openreview.core.image_io import write_exr, read_exr

    exr_file = str(tmp_path / "test_pool.exr")
    src = np.ones((64, 64, 4), dtype=np.float32)
    assert write_exr(exr_file, src) is True

    pool = FrameBufferPool.instance()
    pool.clear()
    pool.enabled = True

    # First read acquires from pool (miss)
    arr1 = read_exr(exr_file, as_f16=False)
    assert arr1 is not None
    assert arr1.shape == (64, 64, 4)
    addr1 = arr1.__array_interface__["data"][0]

    # Evict / release arr1 back to pool
    pool.release(arr1)
    assert pool.get_stats()["total_buffers"] == 1

    # Second read must recycle the EXACT same memory address (zero-copy)!
    arr2 = read_exr(exr_file, as_f16=False)
    assert arr2 is not None
    addr2 = arr2.__array_interface__["data"][0]

    assert addr1 == addr2
    assert pool.get_stats()["hits"] >= 1
