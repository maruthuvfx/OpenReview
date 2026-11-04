use std::fs::File;
use std::path::Path;
use std::sync::Arc;
use half::f16;
use rayon::prelude::*;
use tiff::decoder::{Decoder, DecodingResult};
use tiff::ColorType;
use crate::buffer::NativeFrame;

pub struct TiffDecoder;

impl TiffDecoder {
    pub fn read_tiff<P: AsRef<Path>>(path: P, as_f16: bool) -> std::result::Result<NativeFrame, String> {
        let path_ref = path.as_ref();
        let file = File::open(path_ref).map_err(|e| format!("Failed to open TIFF {:?}: {}", path_ref, e))?;
        let mut decoder = Decoder::new(file).map_err(|e| format!("TIFF decode error: {:?}", e))?;

        let (width, height) = decoder.dimensions().map_err(|e| format!("Failed to get dimensions: {:?}", e))?;
        let colortype = decoder.colortype().map_err(|e| format!("Failed to get colortype: {:?}", e))?;
        let img_result = decoder.read_image().map_err(|e| format!("Failed to read image data: {:?}", e))?;

        let total_pixels = (width * height) as usize;

        let mut out_f32: Vec<f32> = vec![0.0f32; total_pixels * 4];

        match (img_result, colortype) {
            (DecodingResult::U8(buf), ColorType::RGB(8)) => {
                let scale = 1.0f32 / 255.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_chunks_exact(3)).for_each(|(px, rgb)| {
                    px[0] = rgb[0] as f32 * scale;
                    px[1] = rgb[1] as f32 * scale;
                    px[2] = rgb[2] as f32 * scale;
                    px[3] = 1.0f32;
                });
            }
            (DecodingResult::U8(buf), ColorType::RGBA(8)) => {
                let scale = 1.0f32 / 255.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_chunks_exact(4)).for_each(|(px, rgba)| {
                    px[0] = rgba[0] as f32 * scale;
                    px[1] = rgba[1] as f32 * scale;
                    px[2] = rgba[2] as f32 * scale;
                    px[3] = rgba[3] as f32 * scale;
                });
            }
            (DecodingResult::U16(buf), ColorType::RGB(16)) => {
                let scale = 1.0f32 / 65535.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_chunks_exact(3)).for_each(|(px, rgb)| {
                    px[0] = rgb[0] as f32 * scale;
                    px[1] = rgb[1] as f32 * scale;
                    px[2] = rgb[2] as f32 * scale;
                    px[3] = 1.0f32;
                });
            }
            (DecodingResult::U16(buf), ColorType::RGBA(16)) => {
                let scale = 1.0f32 / 65535.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_chunks_exact(4)).for_each(|(px, rgba)| {
                    px[0] = rgba[0] as f32 * scale;
                    px[1] = rgba[1] as f32 * scale;
                    px[2] = rgba[2] as f32 * scale;
                    px[3] = rgba[3] as f32 * scale;
                });
            }
            (DecodingResult::F32(buf), ColorType::RGB(32)) => {
                out_f32.par_chunks_exact_mut(4).zip(buf.par_chunks_exact(3)).for_each(|(px, rgb)| {
                    px[0] = rgb[0];
                    px[1] = rgb[1];
                    px[2] = rgb[2];
                    px[3] = 1.0f32;
                });
            }
            (DecodingResult::F32(buf), ColorType::RGBA(32)) => {
                out_f32.copy_from_slice(&buf);
            }
            (DecodingResult::U8(buf), ColorType::Gray(8)) => {
                let scale = 1.0f32 / 255.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_iter()).for_each(|(px, &g)| {
                    let val = g as f32 * scale;
                    px[0] = val;
                    px[1] = val;
                    px[2] = val;
                    px[3] = 1.0f32;
                });
            }
            (DecodingResult::U16(buf), ColorType::Gray(16)) => {
                let scale = 1.0f32 / 65535.0f32;
                out_f32.par_chunks_exact_mut(4).zip(buf.par_iter()).for_each(|(px, &g)| {
                    let val = g as f32 * scale;
                    px[0] = val;
                    px[1] = val;
                    px[2] = val;
                    px[3] = 1.0f32;
                });
            }
            _ => return Err(format!("Unsupported TIFF colortype: {:?}", colortype)),
        }

        if as_f16 {
            let mut out_f16: Vec<f16> = vec![f16::ZERO; total_pixels * 4];
            out_f16.par_iter_mut().zip(out_f32.par_iter()).for_each(|(dst, &src)| {
                *dst = f16::from_f32(src);
            });
            let byte_len = out_f16.len() * std::mem::size_of::<f16>();
            let ptr = out_f16.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };
            return Ok(NativeFrame {
                width,
                height,
                channels: 4,
                is_f16: true,
                data: Arc::new(byte_slice.to_vec()),
            });
        } else {
            let byte_len = out_f32.len() * std::mem::size_of::<f32>();
            let ptr = out_f32.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };
            return Ok(NativeFrame {
                width,
                height,
                channels: 4,
                is_f16: false,
                data: Arc::new(byte_slice.to_vec()),
            });
        }
    }
}
