use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use exr::prelude::*;
use half::f16;
use crate::buffer::NativeFrame;

#[derive(Debug, Clone)]
pub struct ExrChannelMeta {
    pub name: String,
    pub pixel_type: String, // "F16", "F32", "U32"
}

#[derive(Debug, Clone)]
pub struct ExrMetadataInfo {
    pub width: u32,
    pub height: u32,
    pub compression: String,
    pub all_channels: Vec<ExrChannelMeta>,
    pub layers: HashMap<String, Vec<String>>,
    pub cryptomatte_layers: Vec<String>,
}

pub struct ExrDecoder;

impl ExrDecoder {
    /// Inspect EXR header and channel information without decoding full pixel data.
    pub fn inspect_metadata<P: AsRef<Path>>(path: P) -> std::result::Result<ExrMetadataInfo, String> {
        let path_ref = path.as_ref();
        let meta = exr::meta::MetaData::read_from_file(path_ref, false)
            .map_err(|e| format!("Failed to read EXR metadata {}: {:?}", path_ref.display(), e))?;

        let first_header = meta.headers.first()
            .ok_or_else(|| format!("EXR {} contains no headers", path_ref.display()))?;

        let size = first_header.layer_size;
        let width = size.width() as u32;
        let height = size.height() as u32;

        let compression = format!("{:?}", first_header.compression);

        let mut all_channels = Vec::new();
        let mut layers: HashMap<String, Vec<String>> = HashMap::new();
        let mut crypto_set = std::collections::BTreeSet::new();

        for header in &meta.headers {
            for ch in &header.channels.list {
                let name = ch.name.to_string();
                let ptype = match ch.sample_type {
                    SampleType::F16 => "F16",
                    SampleType::F32 => "F32",
                    SampleType::U32 => "U32",
                }.to_string();

                all_channels.push(ExrChannelMeta {
                    name: name.clone(),
                    pixel_type: ptype,
                });

                // Classify layer
                if name.contains('.') {
                    let parts: Vec<&str> = name.split('.').collect();
                    let layer_name = parts[..parts.len() - 1].join(".");
                    let layer_lower = layer_name.to_lowercase();
                    if layer_lower.contains("crypto") {
                        let base_crypto = layer_name.trim_end_matches(|c: char| c.is_ascii_digit()).to_string();
                        crypto_set.insert(base_crypto);
                    }
                    layers.entry(layer_name).or_default().push(name);
                } else {
                    layers.entry("beauty".to_string()).or_default().push(name);
                }
            }
        }

        // Ensure "beauty" layer is recorded if standard R, G, B channels exist
        if !layers.contains_key("beauty") {
            let beauty_chans: Vec<String> = all_channels.iter()
                .filter(|c| ["R", "G", "B", "A"].contains(&c.name.as_str()))
                .map(|c| c.name.clone())
                .collect();
            if !beauty_chans.is_empty() {
                layers.insert("beauty".to_string(), beauty_chans);
            }
        }

        Ok(ExrMetadataInfo {
            width,
            height,
            compression,
            all_channels,
            layers,
            cryptomatte_layers: crypto_set.into_iter().collect(),
        })
    }

    /// Internal helper to extract routed channels from an in-memory sample map.
    fn extract_channels_from_map(
        chan_map: &HashMap<String, &FlatSamples>,
        chan_lower_map: &HashMap<String, String>,
        width: u32,
        height: u32,
        num_pixels: usize,
        layer: Option<&str>,
        r_ch: Option<&str>,
        g_ch: Option<&str>,
        b_ch: Option<&str>,
        a_ch: Option<&str>,
        auto_normalize_depth: bool,
        remap_normals: bool,
        crypto_layer: Option<&str>,
        as_f16: bool,
    ) -> std::result::Result<NativeFrame, String> {
        let sample_f32 = |samples: &FlatSamples, idx: usize| -> f32 {
            match samples {
                FlatSamples::F32(v) => v[idx],
                FlatSamples::F16(v) => v[idx].to_f32(),
                FlatSamples::U32(v) => v[idx] as f32,
            }
        };

        let mut final_r: Option<&FlatSamples> = None;
        let mut final_g: Option<&FlatSamples> = None;
        let mut final_b: Option<&FlatSamples> = None;
        let mut final_a: Option<&FlatSamples> = None;

        let mut do_crypto = false;
        let mut crypto_id_samples: Option<&FlatSamples> = None;
        let mut crypto_cov_samples: Option<&FlatSamples> = None;

        let mut do_depth_norm = auto_normalize_depth;
        let mut do_normals_remap = remap_normals;

        if let Some(c_prefix) = crypto_layer {
            do_crypto = true;
            let c_prefix_lower = c_prefix.to_lowercase();
            for (k, v) in chan_map {
                let kl = k.to_lowercase();
                if kl.starts_with(&c_prefix_lower) {
                    if kl.ends_with(".r") || kl.ends_with("00.r") {
                        crypto_id_samples = Some(*v);
                    } else if kl.ends_with(".g") || kl.ends_with("00.g") {
                        crypto_cov_samples = Some(*v);
                    }
                }
            }
        } else if let Some(lyr) = layer {
            let lyr_lower = lyr.to_lowercase();
            if lyr_lower.contains("crypto") {
                do_crypto = true;
                for (k, v) in chan_map {
                    let kl = k.to_lowercase();
                    if kl.starts_with(&lyr_lower) {
                        if kl.ends_with(".r") || kl.ends_with("00.r") {
                            crypto_id_samples = Some(*v);
                        } else if kl.ends_with(".g") || kl.ends_with("00.g") {
                            crypto_cov_samples = Some(*v);
                        }
                    }
                }
            } else if lyr_lower != "beauty" {
                let prefix = format!("{}.", lyr_lower);
                let mut layer_chans: Vec<(&String, &&FlatSamples)> = chan_map.iter()
                    .filter(|(k, _)| k.to_lowercase().starts_with(&prefix))
                    .collect();
                layer_chans.sort_by_key(|(k, _)| (*k).clone());

                if !layer_chans.is_empty() {
                    for (k, s) in &layer_chans {
                        let kl = k.to_lowercase();
                        if kl.ends_with(".r") || kl.ends_with(".x") || kl.ends_with(".u") || kl.ends_with(".red") {
                            final_r = Some(**s);
                        } else if kl.ends_with(".g") || kl.ends_with(".y") || kl.ends_with(".v") || kl.ends_with(".green") {
                            final_g = Some(**s);
                        } else if kl.ends_with(".b") || kl.ends_with(".z") || kl.ends_with(".blue") {
                            final_b = Some(**s);
                        } else if kl.ends_with(".a") || kl.ends_with(".alpha") || kl.ends_with(".w") {
                            final_a = Some(**s);
                        }
                    }

                    // Single-channel pass (e.g. depth.Z or AO)
                    if final_r.is_none() && final_g.is_none() && final_b.is_none() && layer_chans.len() == 1 {
                        let s = *layer_chans[0].1;
                        final_r = Some(s);
                        final_g = Some(s);
                        final_b = Some(s);
                        if lyr_lower.contains("depth") || lyr_lower.contains("z") {
                            do_depth_norm = true;
                        }
                    }

                    if lyr_lower.contains("norm") {
                        do_normals_remap = true;
                    }
                }
            }
        }

        // If explicit channels were provided, override
        if let Some(ch) = r_ch {
            if let Some(real_name) = chan_lower_map.get(&ch.to_lowercase()) {
                final_r = chan_map.get(real_name).copied();
            }
        }
        if let Some(ch) = g_ch {
            if let Some(real_name) = chan_lower_map.get(&ch.to_lowercase()) {
                final_g = chan_map.get(real_name).copied();
            }
        }
        if let Some(ch) = b_ch {
            if let Some(real_name) = chan_lower_map.get(&ch.to_lowercase()) {
                final_b = chan_map.get(real_name).copied();
            }
        }
        if let Some(ch) = a_ch {
            if let Some(real_name) = chan_lower_map.get(&ch.to_lowercase()) {
                final_a = chan_map.get(real_name).copied();
            }
        }

        // Default Beauty fallback if neither crypto nor layer matched
        if !do_crypto && final_r.is_none() && final_g.is_none() && final_b.is_none() {
            if let Some(real_name) = chan_lower_map.get("r").or_else(|| chan_lower_map.get("red")) {
                final_r = chan_map.get(real_name).copied();
            }
            if let Some(real_name) = chan_lower_map.get("g").or_else(|| chan_lower_map.get("green")) {
                final_g = chan_map.get(real_name).copied();
            }
            if let Some(real_name) = chan_lower_map.get("b").or_else(|| chan_lower_map.get("blue")) {
                final_b = chan_map.get(real_name).copied();
            }
            if let Some(real_name) = chan_lower_map.get("a").or_else(|| chan_lower_map.get("alpha")) {
                final_a = chan_map.get(real_name).copied();
            }

            // Check for Y (luminance)
            if final_r.is_none() {
                if let Some(real_name) = chan_lower_map.get("y").or_else(|| chan_lower_map.get("luma")) {
                    let y_samp = chan_map.get(real_name).copied();
                    final_r = y_samp;
                    final_g = y_samp;
                    final_b = y_samp;
                }
            }
        }

        // Compute Depth Normalization range if requested
        let mut depth_min = 0.0f32;
        let mut depth_max = 1.0f32;
        if do_depth_norm && final_r.is_some() {
            let r_samp = final_r.unwrap();
            let mut dmin = f32::INFINITY;
            let mut dmax = f32::NEG_INFINITY;
            for i in 0..num_pixels {
                let v = sample_f32(r_samp, i);
                if v.is_finite() && v > 0.0 {
                    if v < dmin { dmin = v; }
                    if v > dmax { dmax = v; }
                }
            }
            if dmax > dmin {
                depth_min = dmin;
                depth_max = dmax;
            }
        }

        // Allocate output buffer and write interleaved RGBA
        if as_f16 {
            let mut out = vec![f16::ZERO; num_pixels * 4];

            if do_crypto {
                let cov_samp = crypto_cov_samples;
                if let Some(id_samp) = crypto_id_samples {
                    for i in 0..num_pixels {
                        let fval = sample_f32(id_samp, i);
                        let id_bits = fval.to_bits();
                        let idx = i * 4;
                        if id_bits == 0 {
                            out[idx] = f16::ZERO;
                            out[idx + 1] = f16::ZERO;
                            out[idx + 2] = f16::ZERO;
                            out[idx + 3] = f16::ZERO;
                        } else {
                            let r = ((id_bits & 0x00FF0000) >> 16) as f32 / 255.0;
                            let g = ((id_bits & 0x0000FF00) >> 8) as f32 / 255.0;
                            let b = (id_bits & 0x000000FF) as f32 / 255.0;
                            let a = cov_samp.map(|cs| sample_f32(cs, i).clamp(0.0, 1.0)).unwrap_or(1.0);

                            out[idx] = f16::from_f32(r);
                            out[idx + 1] = f16::from_f32(g);
                            out[idx + 2] = f16::from_f32(b);
                            out[idx + 3] = f16::from_f32(a);
                        }
                    }
                }
            } else {
                for i in 0..num_pixels {
                    let mut r = final_r.map(|s| sample_f32(s, i)).unwrap_or(0.0);
                    let mut g = final_g.map(|s| sample_f32(s, i)).unwrap_or(r);
                    let mut b = final_b.map(|s| sample_f32(s, i)).unwrap_or(r);
                    let a = final_a.map(|s| sample_f32(s, i)).unwrap_or(1.0);

                    if do_depth_norm && depth_max > depth_min {
                        let norm = ((r - depth_min) / (depth_max - depth_min)).clamp(0.0, 1.0);
                        r = norm;
                        g = norm;
                        b = norm;
                    } else if do_normals_remap {
                        r = (r * 0.5 + 0.5).clamp(0.0, 1.0);
                        g = (g * 0.5 + 0.5).clamp(0.0, 1.0);
                        b = (b * 0.5 + 0.5).clamp(0.0, 1.0);
                    }

                    let idx = i * 4;
                    out[idx] = f16::from_f32(r);
                    out[idx + 1] = f16::from_f32(g);
                    out[idx + 2] = f16::from_f32(b);
                    out[idx + 3] = f16::from_f32(a);
                }
            }

            let byte_len = out.len() * std::mem::size_of::<f16>();
            let ptr = out.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };
            Ok(NativeFrame {
                width,
                height,
                channels: 4,
                is_f16: true,
                data: Arc::new(byte_slice.to_vec()),
            })
        } else {
            let mut out = vec![0.0f32; num_pixels * 4];

            if do_crypto {
                let cov_samp = crypto_cov_samples;
                if let Some(id_samp) = crypto_id_samples {
                    for i in 0..num_pixels {
                        let fval = sample_f32(id_samp, i);
                        let id_bits = fval.to_bits();
                        let idx = i * 4;
                        if id_bits == 0 {
                            out[idx] = 0.0;
                            out[idx + 1] = 0.0;
                            out[idx + 2] = 0.0;
                            out[idx + 3] = 0.0;
                        } else {
                            let r = ((id_bits & 0x00FF0000) >> 16) as f32 / 255.0;
                            let g = ((id_bits & 0x0000FF00) >> 8) as f32 / 255.0;
                            let b = (id_bits & 0x000000FF) as f32 / 255.0;
                            let a = cov_samp.map(|cs| sample_f32(cs, i).clamp(0.0, 1.0)).unwrap_or(1.0);

                            out[idx] = r;
                            out[idx + 1] = g;
                            out[idx + 2] = b;
                            out[idx + 3] = a;
                        }
                    }
                }
            } else {
                for i in 0..num_pixels {
                    let mut r = final_r.map(|s| sample_f32(s, i)).unwrap_or(0.0);
                    let mut g = final_g.map(|s| sample_f32(s, i)).unwrap_or(r);
                    let mut b = final_b.map(|s| sample_f32(s, i)).unwrap_or(r);
                    let a = final_a.map(|s| sample_f32(s, i)).unwrap_or(1.0);

                    if do_depth_norm && depth_max > depth_min {
                        let norm = ((r - depth_min) / (depth_max - depth_min)).clamp(0.0, 1.0);
                        r = norm;
                        g = norm;
                        b = norm;
                    } else if do_normals_remap {
                        r = (r * 0.5 + 0.5).clamp(0.0, 1.0);
                        g = (g * 0.5 + 0.5).clamp(0.0, 1.0);
                        b = (b * 0.5 + 0.5).clamp(0.0, 1.0);
                    }

                    let idx = i * 4;
                    out[idx] = r;
                    out[idx + 1] = g;
                    out[idx + 2] = b;
                    out[idx + 3] = a;
                }
            }

            let byte_len = out.len() * std::mem::size_of::<f32>();
            let ptr = out.as_ptr() as *const u8;
            let byte_slice = unsafe { std::slice::from_raw_parts(ptr, byte_len) };
            Ok(NativeFrame {
                width,
                height,
                channels: 4,
                is_f16: false,
                data: Arc::new(byte_slice.to_vec()),
            })
        }
    }

    /// Read arbitrary channels or layers from OpenEXR into a NativeFrame buffer.
    pub fn read_channels<P: AsRef<Path>>(
        path: P,
        layer: Option<&str>,
        r_ch: Option<&str>,
        g_ch: Option<&str>,
        b_ch: Option<&str>,
        a_ch: Option<&str>,
        auto_normalize_depth: bool,
        remap_normals: bool,
        crypto_layer: Option<&str>,
        as_f16: bool,
    ) -> std::result::Result<NativeFrame, String> {
        let path_ref = path.as_ref();
        let image = read()
            .no_deep_data()
            .largest_resolution_level()
            .all_channels()
            .all_layers()
            .all_attributes()
            .from_file(path_ref)
            .map_err(|e| format!("Failed to read EXR {}: {:?}", path_ref.display(), e))?;

        let first_layer = image.layer_data.first()
            .ok_or_else(|| format!("EXR {} contains no layers", path_ref.display()))?;

        let width = first_layer.size.width() as u32;
        let height = first_layer.size.height() as u32;
        let num_pixels = (width * height) as usize;

        let mut chan_map: HashMap<String, &FlatSamples> = HashMap::new();
        let mut chan_lower_map: HashMap<String, String> = HashMap::new();

        for l in &image.layer_data {
            for ch in &l.channel_data.list {
                let name = ch.name.to_string();
                let lower = name.to_lowercase();
                chan_lower_map.insert(lower.clone(), name.clone());
                chan_map.insert(name, &ch.sample_data);
            }
        }

        Self::extract_channels_from_map(
            &chan_map,
            &chan_lower_map,
            width,
            height,
            num_pixels,
            layer,
            r_ch,
            g_ch,
            b_ch,
            a_ch,
            auto_normalize_depth,
            remap_normals,
            crypto_layer,
            as_f16,
        )
    }

    /// Read all detected layers from an OpenEXR file in a single fast, parallel parse.
    pub fn read_all_layers<P: AsRef<Path>>(
        path: P,
        as_f16: bool,
    ) -> std::result::Result<HashMap<String, NativeFrame>, String> {
        let path_ref = path.as_ref();
        let image = read()
            .no_deep_data()
            .largest_resolution_level()
            .all_channels()
            .all_layers()
            .all_attributes()
            .from_file(path_ref)
            .map_err(|e| format!("Failed to read EXR {}: {:?}", path_ref.display(), e))?;

        let first_layer = image.layer_data.first()
            .ok_or_else(|| format!("EXR {} contains no layers", path_ref.display()))?;

        let width = first_layer.size.width() as u32;
        let height = first_layer.size.height() as u32;
        let num_pixels = (width * height) as usize;

        let mut chan_map: HashMap<String, &FlatSamples> = HashMap::new();
        let mut chan_lower_map: HashMap<String, String> = HashMap::new();
        let mut layers: HashMap<String, Vec<String>> = HashMap::new();
        let mut crypto_layers = std::collections::BTreeSet::new();

        for l in &image.layer_data {
            for ch in &l.channel_data.list {
                let name = ch.name.to_string();
                let lower = name.to_lowercase();
                chan_lower_map.insert(lower.clone(), name.clone());
                if name.contains('.') {
                    let parts: Vec<&str> = name.split('.').collect();
                    let layer_name = parts[..parts.len() - 1].join(".");
                    if layer_name.to_lowercase().contains("crypto") {
                        let base_crypto = layer_name.trim_end_matches(|c: char| c.is_ascii_digit()).to_string();
                        crypto_layers.insert(base_crypto);
                    }
                    layers.entry(layer_name).or_default().push(name.clone());
                } else {
                    layers.entry("beauty".to_string()).or_default().push(name.clone());
                }
                chan_map.insert(name, &ch.sample_data);
            }
        }

        if !layers.contains_key("beauty") {
            let bchans: Vec<String> = chan_map.keys()
                .filter(|k| ["R", "G", "B", "A"].contains(&k.as_str()))
                .cloned()
                .collect();
            if !bchans.is_empty() {
                layers.insert("beauty".to_string(), bchans);
            }
        }

        for cl in crypto_layers {
            if !layers.contains_key(&cl) {
                layers.insert(cl, Vec::new());
            }
        }

        let mut results = HashMap::new();
        for (layer_name, _) in layers {
            let is_crypto = layer_name.to_lowercase().contains("crypto");
            let frame_res = if is_crypto {
                Self::extract_channels_from_map(
                    &chan_map,
                    &chan_lower_map,
                    width,
                    height,
                    num_pixels,
                    None,
                    None,
                    None,
                    None,
                    None,
                    false,
                    false,
                    Some(&layer_name),
                    as_f16,
                )
            } else {
                Self::extract_channels_from_map(
                    &chan_map,
                    &chan_lower_map,
                    width,
                    height,
                    num_pixels,
                    Some(&layer_name),
                    None,
                    None,
                    None,
                    None,
                    false,
                    false,
                    None,
                    as_f16,
                )
            };

            if let Ok(frame) = frame_res {
                results.insert(layer_name, frame);
            }
        }

        Ok(results)
    }

    /// Read OpenEXR image as RGBA f16 (half float) buffer (legacy convenience method).
    pub fn read_rgba_f16<P: AsRef<Path>>(path: P) -> std::result::Result<NativeFrame, String> {
        Self::read_channels(path, Some("beauty"), None, None, None, None, false, false, None, true)
    }

    /// Read OpenEXR image as RGBA f32 buffer (legacy convenience method).
    pub fn read_rgba_f32<P: AsRef<Path>>(path: P) -> std::result::Result<NativeFrame, String> {
        Self::read_channels(path, Some("beauty"), None, None, None, None, false, false, None, false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_multichannel_passes() {
        let p = std::path::Path::new("../../tests/sample_media/multichannel_exr/multichannel_test.exr");
        if !p.exists() {
            return;
        }

        // 1. Inspect metadata
        let meta = ExrDecoder::inspect_metadata(p).unwrap();
        assert_eq!(meta.width, 256);
        assert_eq!(meta.height, 144);
        assert!(meta.layers.contains_key("beauty"));
        assert!(meta.layers.contains_key("diffuse"));
        assert!(meta.layers.contains_key("specular"));
        assert!(meta.layers.contains_key("N"));
        assert!(meta.layers.contains_key("depth"));
        assert!(meta.cryptomatte_layers.contains(&"crypto_object".to_string()));

        // 2. Read Beauty (f32)
        let f_beauty = ExrDecoder::read_channels(p, Some("beauty"), None, None, None, None, false, false, None, false).unwrap();
        assert_eq!(f_beauty.width, 256);
        assert_eq!(f_beauty.height, 144);
        assert!(!f_beauty.is_f16);

        // 3. Read Diffuse (f16)
        let f_diff = ExrDecoder::read_channels(p, Some("diffuse"), None, None, None, None, false, false, None, true).unwrap();
        assert_eq!(f_diff.width, 256);
        assert!(f_diff.is_f16);

        // 4. Read Normals with remap
        let f_norm = ExrDecoder::read_channels(p, Some("N"), None, None, None, None, false, true, None, false).unwrap();
        let norm_slice: &[f32] = unsafe {
            std::slice::from_raw_parts(f_norm.data.as_ptr() as *const f32, f_norm.data.len() / 4)
        };
        for v in norm_slice {
            assert!(*v >= 0.0 && *v <= 1.0001, "Normal value {} out of range", v);
        }

        // 5. Read Depth with auto-normalization
        let f_depth = ExrDecoder::read_channels(p, Some("depth"), None, None, None, None, true, false, None, false).unwrap();
        let depth_slice: &[f32] = unsafe {
            std::slice::from_raw_parts(f_depth.data.as_ptr() as *const f32, f_depth.data.len() / 4)
        };
        for v in depth_slice {
            assert!(*v >= 0.0 && *v <= 1.0001, "Normalized depth value {} out of range", v);
        }

        // 6. Read Cryptomatte
        let f_crypto = ExrDecoder::read_channels(p, None, None, None, None, None, false, false, Some("crypto_object"), false).unwrap();
        assert_eq!(f_crypto.width, 256);

        // 7. Read arbitrary custom routing
        let f_custom = ExrDecoder::read_channels(p, None, Some("specular.R"), Some("diffuse.G"), Some("depth.Z"), Some("A"), false, false, None, false).unwrap();
        assert_eq!(f_custom.width, 256);

        // 8. Test read_all_layers
        let all_layers = ExrDecoder::read_all_layers(p, false).unwrap();
        assert!(all_layers.len() >= 6);
        assert!(all_layers.contains_key("beauty"));
        assert!(all_layers.contains_key("diffuse"));
        assert!(all_layers.contains_key("specular"));
        assert!(all_layers.contains_key("N"));
        assert!(all_layers.contains_key("depth"));
    }
}
