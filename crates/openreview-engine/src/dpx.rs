use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::Arc;
use half::f16;
use rayon::prelude::*;
use crate::buffer::NativeFrame;

pub struct DpxDecoder;

impl DpxDecoder {
    pub fn read_dpx<P: AsRef<Path>>(path: P, as_f16: bool) -> std::result::Result<NativeFrame, String> {
        let path_ref = path.as_ref();
        let mut file = File::open(path_ref).map_err(|e| format!("Failed to open DPX {:?}: {}", path_ref, e))?;

        let mut header = [0u8; 2048];
        file.read_exact(&mut header).map_err(|e| format!("Failed to read DPX header: {}", e))?;

        let magic = &header[0..4];
        let is_be = if magic == b"SDPX" {
            true
        } else if magic == b"XPDS" {
            false
        } else {
            return Err(format!("Invalid DPX magic {:?} in {:?}", magic, path_ref));
        };

        let read_u16 = |buf: &[u8]| -> u16 {
            if is_be {
                u16::from_be_bytes([buf[0], buf[1]])
            } else {
                u16::from_le_bytes([buf[0], buf[1]])
            }
        };

        let read_u32 = |buf: &[u8]| -> u32 {
            if is_be {
                u32::from_be_bytes([buf[0], buf[1], buf[2], buf[3]])
            } else {
                u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]])
            }
        };

        let data_offset = read_u32(&header[4..8]);
        let width = read_u32(&header[772..776]);
        let height = read_u32(&header[776..780]);

        if width == 0 || height == 0 {
            return Err(format!("Invalid DPX dimensions {}x{}", width, height));
        }

        let bit_depth = header[803];
        let packing = read_u16(&header[804..806]);
        let elem_offset = read_u32(&header[808..812]);

        let actual_offset = if elem_offset >= 2048 && elem_offset < 100_000_000 {
            elem_offset
        } else {
            data_offset
        };

        file.seek(SeekFrom::Start(actual_offset as u64))
            .map_err(|e| format!("Seek failed in DPX: {}", e))?;

        let total_pixels = (width * height) as usize;

        // 1. 10-Bit DPX (Most common VFX master scan format)
        if bit_depth == 10 {
            let num_bytes = total_pixels * 4;
            let mut raw_data = vec![0u8; num_bytes];
            file.read_exact(&mut raw_data)
                .map_err(|e| format!("Truncated 10-bit DPX data: {}", e))?;

            // Convert to u32 words
            let words: Vec<u32> = if is_be {
                raw_data
                    .chunks_exact(4)
                    .map(|c| u32::from_be_bytes([c[0], c[1], c[2], c[3]]))
                    .collect()
            } else {
                raw_data
                    .chunks_exact(4)
                    .map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                    .collect()
            };

            let scale = 1.0f32 / 1023.0f32;

            if as_f16 {
                let mut out_f16: Vec<f16> = vec![f16::ZERO; total_pixels * 4];
                out_f16
                    .par_chunks_exact_mut(4)
                    .zip(words.par_iter())
                    .for_each(|(px, &w)| {
                        let (r, g, b) = if packing == 2 {
                            // Method B (LSB aligned)
                            (w & 0x3FF, (w >> 10) & 0x3FF, (w >> 20) & 0x3FF)
                        } else {
                            // Method A (SMPTE 268M standard MSB aligned)
                            ((w >> 22) & 0x3FF, (w >> 12) & 0x3FF, (w >> 2) & 0x3FF)
                        };
                        px[0] = f16::from_f32(r as f32 * scale);
                        px[1] = f16::from_f32(g as f32 * scale);
                        px[2] = f16::from_f32(b as f32 * scale);
                        px[3] = f16::ONE;
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
                let mut out_f32: Vec<f32> = vec![0.0f32; total_pixels * 4];
                out_f32
                    .par_chunks_exact_mut(4)
                    .zip(words.par_iter())
                    .for_each(|(px, &w)| {
                        let (r, g, b) = if packing == 2 {
                            (w & 0x3FF, (w >> 10) & 0x3FF, (w >> 20) & 0x3FF)
                        } else {
                            ((w >> 22) & 0x3FF, (w >> 12) & 0x3FF, (w >> 2) & 0x3FF)
                        };
                        px[0] = r as f32 * scale;
                        px[1] = g as f32 * scale;
                        px[2] = b as f32 * scale;
                        px[3] = 1.0f32;
                    });

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

        // 2. 16-Bit DPX
        if bit_depth == 16 {
            let num_bytes = total_pixels * 3 * 2;
            let mut raw_data = vec![0u8; num_bytes];
            file.read_exact(&mut raw_data)
                .map_err(|e| format!("Truncated 16-bit DPX data: {}", e))?;

            let scale = 1.0f32 / 65535.0f32;
            let u16_words: Vec<u16> = if is_be {
                raw_data.chunks_exact(2).map(|c| u16::from_be_bytes([c[0], c[1]])).collect()
            } else {
                raw_data.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect()
            };

            if as_f16 {
                let mut out_f16: Vec<f16> = vec![f16::ZERO; total_pixels * 4];
                out_f16
                    .par_chunks_exact_mut(4)
                    .zip(u16_words.par_chunks_exact(3))
                    .for_each(|(px, rgb)| {
                        px[0] = f16::from_f32(rgb[0] as f32 * scale);
                        px[1] = f16::from_f32(rgb[1] as f32 * scale);
                        px[2] = f16::from_f32(rgb[2] as f32 * scale);
                        px[3] = f16::ONE;
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
                let mut out_f32: Vec<f32> = vec![0.0f32; total_pixels * 4];
                out_f32
                    .par_chunks_exact_mut(4)
                    .zip(u16_words.par_chunks_exact(3))
                    .for_each(|(px, rgb)| {
                        px[0] = rgb[0] as f32 * scale;
                        px[1] = rgb[1] as f32 * scale;
                        px[2] = rgb[2] as f32 * scale;
                        px[3] = 1.0f32;
                    });
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

        // 3. 8-Bit DPX
        if bit_depth == 8 {
            let num_bytes = total_pixels * 3;
            let mut raw_data = vec![0u8; num_bytes];
            file.read_exact(&mut raw_data)
                .map_err(|e| format!("Truncated 8-bit DPX data: {}", e))?;

            let scale = 1.0f32 / 255.0f32;

            if as_f16 {
                let mut out_f16: Vec<f16> = vec![f16::ZERO; total_pixels * 4];
                out_f16
                    .par_chunks_exact_mut(4)
                    .zip(raw_data.par_chunks_exact(3))
                    .for_each(|(px, rgb)| {
                        px[0] = f16::from_f32(rgb[0] as f32 * scale);
                        px[1] = f16::from_f32(rgb[1] as f32 * scale);
                        px[2] = f16::from_f32(rgb[2] as f32 * scale);
                        px[3] = f16::ONE;
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
                let mut out_f32: Vec<f32> = vec![0.0f32; total_pixels * 4];
                out_f32
                    .par_chunks_exact_mut(4)
                    .zip(raw_data.par_chunks_exact(3))
                    .for_each(|(px, rgb)| {
                        px[0] = rgb[0] as f32 * scale;
                        px[1] = rgb[1] as f32 * scale;
                        px[2] = rgb[2] as f32 * scale;
                        px[3] = 1.0f32;
                    });
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

        Err(format!("Unsupported DPX bit depth: {}", bit_depth))
    }
}
