pub mod buffer;
pub mod decoder;
pub mod cache;
pub mod scheduler;
pub mod dpx;
pub mod tiff;
pub mod sequence;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use pyo3::types::PyDict;

use crate::buffer::NativeFrame;
use crate::cache::TieredCache;
use crate::decoder::ExrDecoder;
use crate::dpx::DpxDecoder;
use crate::tiff::TiffDecoder;
use crate::scheduler::PrefetchScheduler;
use crate::sequence::{NativeSequenceInfo, scan_sequence, scan_sequence_impl};

#[pyclass]
pub struct OpenReviewEngine {
    cache: Arc<TieredCache>,
    scheduler: PrefetchScheduler,
}

#[pymethods]
impl OpenReviewEngine {
    #[new]
    #[pyo3(signature = (ram_limit_mb=8192, worker_threads=0))]
    pub fn new(ram_limit_mb: usize, worker_threads: usize) -> Self {
        let cache = Arc::new(TieredCache::new(ram_limit_mb, None));
        let scheduler = PrefetchScheduler::new(Arc::clone(&cache), worker_threads);

        Self { cache, scheduler }
    }

    /// Read an OpenEXR file synchronously and return a zero-copy NativeFrame.
    /// Supports arbitrary layers, channels, auto depth normalization, normals remap, and Cryptomatte.
    #[pyo3(signature = (
        path,
        as_f16=true,
        layer=None,
        r_ch=None,
        g_ch=None,
        b_ch=None,
        a_ch=None,
        auto_normalize_depth=false,
        remap_normals=false,
        crypto_layer=None
    ))]
    pub fn read_exr(
        &self,
        py: Python<'_>,
        path: &str,
        as_f16: bool,
        layer: Option<&str>,
        r_ch: Option<&str>,
        g_ch: Option<&str>,
        b_ch: Option<&str>,
        a_ch: Option<&str>,
        auto_normalize_depth: bool,
        remap_normals: bool,
        crypto_layer: Option<&str>,
    ) -> PyResult<NativeFrame> {
        py.allow_threads(|| {
            ExrDecoder::read_channels(
                path,
                layer,
                r_ch,
                g_ch,
                b_ch,
                a_ch,
                auto_normalize_depth,
                remap_normals,
                crypto_layer,
                as_f16,
            ).map_err(|e| PyRuntimeError::new_err(e))
        })
    }

    /// Fast inspect OpenEXR header and channels without reading full pixels.
    pub fn inspect_exr(&self, py: Python<'_>, path: &str) -> PyResult<PyObject> {
        let meta = ExrDecoder::inspect_metadata(path)
            .map_err(|e| PyRuntimeError::new_err(e))?;

        let dict = PyDict::new_bound(py);
        dict.set_item("width", meta.width)?;
        dict.set_item("height", meta.height)?;
        dict.set_item("compression", meta.compression)?;

        let all_chans: Vec<String> = meta.all_channels.iter().map(|c| c.name.clone()).collect();
        dict.set_item("all_channels", all_chans)?;

        let layers_dict = PyDict::new_bound(py);
        for (k, v) in meta.layers {
            layers_dict.set_item(k, v)?;
        }
        dict.set_item("layers", layers_dict)?;
        dict.set_item("cryptomatte_layers", meta.cryptomatte_layers)?;
        dict.set_item("views", Vec::<String>::new())?;

        Ok(dict.into())
    }

    /// Read all detected layers from an OpenEXR file in a single fast, parallel parse.
    /// Returns a dict: { layer_name: NativeFrame }
    #[pyo3(signature = (path, as_f16=true))]
    pub fn read_all_exr_layers(&self, py: Python<'_>, path: &str, as_f16: bool) -> PyResult<PyObject> {
        let layers = py.allow_threads(|| {
            ExrDecoder::read_all_layers(path, as_f16).map_err(|e| PyRuntimeError::new_err(e))
        })?;

        let dict = PyDict::new_bound(py);
        for (name, frame) in layers {
            let py_frame = Bound::new(py, frame)?;
            dict.set_item(name, py_frame)?;
        }
        Ok(dict.into())
    }

    /// Read a SMPTE 268M DPX file synchronously (10-bit, 12-bit, 16-bit, 8-bit).
    #[pyo3(signature = (path, as_f16=true))]
    pub fn read_dpx(&self, py: Python<'_>, path: &str, as_f16: bool) -> PyResult<NativeFrame> {
        py.allow_threads(|| {
            DpxDecoder::read_dpx(path, as_f16).map_err(|e| PyRuntimeError::new_err(e))
        })
    }

    /// Read a TIFF (.tif, .tiff) file synchronously (8-bit, 16-bit, 32-bit float).
    #[pyo3(signature = (path, as_f16=true))]
    pub fn read_tiff(&self, py: Python<'_>, path: &str, as_f16: bool) -> PyResult<NativeFrame> {
        py.allow_threads(|| {
            TiffDecoder::read_tiff(path, as_f16).map_err(|e| PyRuntimeError::new_err(e))
        })
    }

    /// Unified media reader automatically selecting the fastest native decoder (EXR, DPX, TIFF).
    #[pyo3(signature = (path, as_f16=true, layer=None))]
    pub fn read_media(&self, py: Python<'_>, path: &str, as_f16: bool, layer: Option<&str>) -> PyResult<NativeFrame> {
        let ext = Path::new(path)
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_lowercase();

        if ext == "exr" {
            self.read_exr(py, path, as_f16, layer, None, None, None, None, false, false, None)
        } else if ext == "dpx" {
            self.read_dpx(py, path, as_f16)
        } else if ext == "tif" || ext == "tiff" {
            self.read_tiff(py, path, as_f16)
        } else {
            Err(PyRuntimeError::new_err(format!("Unsupported format: .{}", ext)))
        }
    }

    /// Downsample any NativeFrame (scale=2 for 1/2 res, scale=4 for 1/4 res).
    pub fn downsample_frame(&self, frame_obj: &NativeFrame, scale: u32) -> PyResult<NativeFrame> {
        frame_obj.downsample(scale)
    }

    /// Retrieve a full-resolution frame from the high-speed Tiered Cache (RAM or fast mmap disk).
    #[pyo3(signature = (source_id, frame, layer="beauty"))]
    pub fn get_frame(&self, source_id: &str, frame: i32, layer: &str) -> Option<NativeFrame> {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        self.cache.get_frame(source_id, lyr, frame).map(|arc_frame| NativeFrame {
            width: arc_frame.width,
            height: arc_frame.height,
            channels: arc_frame.channels,
            is_f16: arc_frame.is_f16,
            data: Arc::clone(&arc_frame.data),
        })
    }

    /// Retrieve a downsampled proxy frame (1/2 or 1/4 res) for ultra-fast 60 FPS scrub.
    #[pyo3(signature = (source_id, frame, layer="beauty"))]
    pub fn get_proxy_frame(&self, source_id: &str, frame: i32, layer: &str) -> Option<NativeFrame> {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        self.cache.get_proxy_frame(source_id, lyr, frame).map(|arc_frame| NativeFrame {
            width: arc_frame.width,
            height: arc_frame.height,
            channels: arc_frame.channels,
            is_f16: arc_frame.is_f16,
            data: Arc::clone(&arc_frame.data),
        })
    }

    /// Store a full-resolution decoded frame into the cache.
    #[pyo3(signature = (source_id, frame, frame_obj, layer="beauty"))]
    pub fn put_frame(&self, source_id: &str, frame: i32, frame_obj: &NativeFrame, layer: &str) {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        let native = Arc::new(NativeFrame {
            width: frame_obj.width,
            height: frame_obj.height,
            channels: frame_obj.channels,
            is_f16: frame_obj.is_f16,
            data: Arc::clone(&frame_obj.data),
        });
        self.cache.put_frame(source_id, lyr, frame, native);
    }

    /// Store a downsampled proxy frame into the proxy cache.
    #[pyo3(signature = (source_id, frame, frame_obj, layer="beauty"))]
    pub fn put_proxy_frame(&self, source_id: &str, frame: i32, frame_obj: &NativeFrame, layer: &str) {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        let native = Arc::new(NativeFrame {
            width: frame_obj.width,
            height: frame_obj.height,
            channels: frame_obj.channels,
            is_f16: frame_obj.is_f16,
            data: Arc::clone(&frame_obj.data),
        });
        self.cache.put_proxy_frame(source_id, lyr, frame, native);
    }

    /// Asynchronously prefetch a batch of sequence frames on native worker threads.
    /// If proxy_scale >= 2, decodes and caches at downsampled resolution for massive sequences.
    #[pyo3(signature = (items, proxy_scale=0, layer="beauty"))]
    pub fn prefetch_batch(&self, py: Python<'_>, items: Vec<(String, i32, String)>, proxy_scale: u32, layer: &str) {
        let lyr = if layer.is_empty() { "beauty".to_string() } else { layer.to_string() };
        py.allow_threads(|| {
            let task_items: Vec<(String, String, i32, PathBuf)> = items
                .into_iter()
                .map(|(sid, frm, path)| (sid, lyr.clone(), frm, PathBuf::from(path)))
                .collect();
            self.scheduler.queue_prefetch(task_items, proxy_scale);
        });
    }

    /// Instantly cancel all pending prefetch decodes (e.g. when scrubbing).
    pub fn cancel_prefetch(&self) {
        self.scheduler.cancel_all();
    }

    /// Query which full-res frames are currently cached for a given source.
    #[pyo3(signature = (source_id, layer="beauty"))]
    pub fn get_cached_frames(&self, source_id: &str, layer: &str) -> Vec<i32> {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        self.cache.get_cached_frames(source_id, lyr)
    }

    /// Query which proxy frames are currently cached for a given source.
    #[pyo3(signature = (source_id, layer="beauty"))]
    pub fn get_cached_proxy_frames(&self, source_id: &str, layer: &str) -> Vec<i32> {
        let lyr = if layer.is_empty() { "beauty" } else { layer };
        self.cache.get_cached_proxy_frames(source_id, lyr)
    }

    /// Update dynamic RAM cache budget in MB.
    pub fn set_ram_limit_mb(&self, mb: usize) {
        self.cache.set_ram_limit_mb(mb);
    }

    /// Query current RAM used by the native tiered cache in bytes.
    pub fn get_used_ram_bytes(&self) -> usize {
        self.cache.get_used_ram_bytes()
    }

    /// Evict cached frames based on current playhead position.
    pub fn update_playhead(&self, current_frame: i32) {
        self.cache.evict_if_needed(Some(current_frame));
    }

    /// Clear all RAM, proxy, and disk cached frames.
    pub fn clear_cache(&self) {
        self.cache.clear();
    }

    /// Parallel sequence scanner using Rayon (detects frame ranges, gaps, and naming pattern).
    pub fn scan_sequence(&self, py: Python<'_>, target_path: &str) -> PyResult<Option<NativeSequenceInfo>> {
        py.allow_threads(|| {
            scan_sequence_impl(target_path).map_err(|e| PyRuntimeError::new_err(e))
        })
    }
}

#[pymodule]
fn openreview_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeFrame>()?;
    m.add_class::<NativeSequenceInfo>()?;
    m.add_class::<OpenReviewEngine>()?;
    m.add_function(wrap_pyfunction!(scan_sequence, m)?)?;
    Ok(())
}
