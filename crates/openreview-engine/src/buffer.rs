use pyo3::prelude::*;
use pyo3::ffi;
use pyo3::types::PyMemoryView;
use std::os::raw::{c_char, c_int};
use std::sync::Arc;
use half::f16;
use rayon::prelude::*;

/// A contiguous RGBA frame buffer stored in native memory.
/// Can be exposed directly to Python as a zero-copy PyMemoryView.
#[pyclass]
pub struct NativeFrame {
    pub width: u32,
    pub height: u32,
    pub channels: u32,
    pub is_f16: bool, // true if f16 (2 bytes per channel), false if f32 (4 bytes)
    pub data: Arc<Vec<u8>>,
}

#[pymethods]
impl NativeFrame {
    #[getter]
    pub fn width(&self) -> u32 {
        self.width
    }

    #[getter]
    pub fn height(&self) -> u32 {
        self.height
    }

    #[getter]
    pub fn channels(&self) -> u32 {
        self.channels
    }

    #[getter]
    pub fn is_f16(&self) -> bool {
        self.is_f16
    }

    #[getter]
    pub fn byte_len(&self) -> usize {
        self.data.len()
    }

    #[getter]
    pub fn data_ptr(&self) -> usize {
        self.data.as_ptr() as usize
    }

    /// Expose copy as Python bytes managed by Python GC
    pub fn to_bytes<'py>(&self, py: Python<'py>) -> Bound<'py, pyo3::types::PyBytes> {
        pyo3::types::PyBytes::new_bound(py, &self.data)
    }

    /// Expose raw frame memory directly to Python as a zero-copy PyMemoryView.
    pub fn as_memoryview<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyMemoryView>> {
        unsafe {
            let ptr = self.data.as_ptr() as *mut c_char;
            let len = self.data.len() as isize;
            let raw_mv = ffi::PyMemoryView_FromMemory(ptr, len, ffi::PyBUF_READ as c_int);
            if raw_mv.is_null() {
                return Err(pyo3::PyErr::fetch(py));
            }
            Ok(Bound::from_owned_ptr(py, raw_mv).downcast_into_unchecked())
        }
    }

    /// Create downsampled proxy frame (scale=2 for 1/2 res, scale=4 for 1/4 res).
    /// Highly optimized parallel area box-filter averaging across rows.
    pub fn downsample(&self, scale: u32) -> PyResult<NativeFrame> {
        if scale <= 1 {
            return Ok(NativeFrame {
                width: self.width,
                height: self.height,
                channels: self.channels,
                is_f16: self.is_f16,
                data: Arc::clone(&self.data),
            });
        }

        let out_w = (self.width / scale).max(1);
        let out_h = (self.height / scale).max(1);
        let scale_usize = scale as usize;

        if self.is_f16 {
            let src_slice: &[f16] = unsafe {
                std::slice::from_raw_parts(self.data.as_ptr() as *const f16, self.data.len() / 2)
            };
            let mut dst: Vec<f16> = vec![f16::ZERO; (out_w * out_h * 4) as usize];
            let inv_count = 1.0f32 / (scale * scale) as f32;

            dst.par_chunks_exact_mut(4 * out_w as usize)
                .enumerate()
                .for_each(|(out_y, row_out)| {
                    let in_y_base = out_y * scale_usize;
                    for out_x in 0..out_w as usize {
                        let in_x_base = out_x * scale_usize;
                        let mut sum_r = 0.0f32;
                        let mut sum_g = 0.0f32;
                        let mut sum_b = 0.0f32;
                        let mut sum_a = 0.0f32;

                        for dy in 0..scale_usize {
                            let in_y = (in_y_base + dy).min(self.height as usize - 1);
                            for dx in 0..scale_usize {
                                let in_x = (in_x_base + dx).min(self.width as usize - 1);
                                let idx = (in_y * self.width as usize + in_x) * 4;
                                sum_r += src_slice[idx].to_f32();
                                sum_g += src_slice[idx + 1].to_f32();
                                sum_b += src_slice[idx + 2].to_f32();
                                sum_a += src_slice[idx + 3].to_f32();
                            }
                        }

                        let out_idx = out_x * 4;
                        row_out[out_idx] = f16::from_f32(sum_r * inv_count);
                        row_out[out_idx + 1] = f16::from_f32(sum_g * inv_count);
                        row_out[out_idx + 2] = f16::from_f32(sum_b * inv_count);
                        row_out[out_idx + 3] = f16::from_f32(sum_a * inv_count);
                    }
                });

            let byte_len = dst.len() * std::mem::size_of::<f16>();
            let ptr = dst.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };

            Ok(NativeFrame {
                width: out_w,
                height: out_h,
                channels: 4,
                is_f16: true,
                data: Arc::new(byte_slice.to_vec()),
            })
        } else {
            let src_slice: &[f32] = unsafe {
                std::slice::from_raw_parts(self.data.as_ptr() as *const f32, self.data.len() / 4)
            };
            let mut dst: Vec<f32> = vec![0.0f32; (out_w * out_h * 4) as usize];
            let inv_count = 1.0f32 / (scale * scale) as f32;

            dst.par_chunks_exact_mut(4 * out_w as usize)
                .enumerate()
                .for_each(|(out_y, row_out)| {
                    let in_y_base = out_y * scale_usize;
                    for out_x in 0..out_w as usize {
                        let in_x_base = out_x * scale_usize;
                        let mut sum_r = 0.0f32;
                        let mut sum_g = 0.0f32;
                        let mut sum_b = 0.0f32;
                        let mut sum_a = 0.0f32;

                        for dy in 0..scale_usize {
                            let in_y = (in_y_base + dy).min(self.height as usize - 1);
                            for dx in 0..scale_usize {
                                let in_x = (in_x_base + dx).min(self.width as usize - 1);
                                let idx = (in_y * self.width as usize + in_x) * 4;
                                sum_r += src_slice[idx];
                                sum_g += src_slice[idx + 1];
                                sum_b += src_slice[idx + 2];
                                sum_a += src_slice[idx + 3];
                            }
                        }

                        let out_idx = out_x * 4;
                        row_out[out_idx] = sum_r * inv_count;
                        row_out[out_idx + 1] = sum_g * inv_count;
                        row_out[out_idx + 2] = sum_b * inv_count;
                        row_out[out_idx + 3] = sum_a * inv_count;
                    }
                });

            let byte_len = dst.len() * std::mem::size_of::<f32>();
            let ptr = dst.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };

            Ok(NativeFrame {
                width: out_w,
                height: out_h,
                channels: 4,
                is_f16: false,
                data: Arc::new(byte_slice.to_vec()),
            })
        }
    }
}
