use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use parking_lot::RwLock;
use lz4_flex::{compress_prepend_size, decompress_size_prepended};
use crate::buffer::NativeFrame;

#[derive(Clone)]
struct CachedMetadata {
    width: u32,
    height: u32,
    channels: u32,
    is_f16: bool,
}

pub struct TieredCache {
    ram_limit_bytes: AtomicUsize,
    used_ram_bytes: AtomicUsize,
    ram_entries: RwLock<HashMap<(String, String, i32), (Arc<NativeFrame>, std::time::Instant)>>,
    proxy_entries: RwLock<HashMap<(String, String, i32), Arc<NativeFrame>>>,
    disk_entries: RwLock<HashMap<(String, String, i32), (PathBuf, CachedMetadata)>>,
    disk_dir: PathBuf,
}

impl TieredCache {
    pub fn new(ram_limit_mb: usize, disk_cache_dir: Option<PathBuf>) -> Self {
        let default_dir = std::env::temp_dir().join(format!("openreview_rust_cache_{}", std::process::id()));
        let dir = disk_cache_dir.unwrap_or(default_dir);
        let _ = fs::create_dir_all(&dir);

        Self {
            ram_limit_bytes: AtomicUsize::new(ram_limit_mb * 1024 * 1024),
            used_ram_bytes: AtomicUsize::new(0),
            ram_entries: RwLock::new(HashMap::new()),
            proxy_entries: RwLock::new(HashMap::new()),
            disk_entries: RwLock::new(HashMap::new()),
            disk_dir: dir,
        }
    }

    pub fn set_ram_limit_mb(&self, mb: usize) {
        self.ram_limit_bytes.store(mb * 1024 * 1024, Ordering::Relaxed);
        self.evict_if_needed(None);
    }

    pub fn get_used_ram_bytes(&self) -> usize {
        self.used_ram_bytes.load(Ordering::Relaxed)
    }

    pub fn get_ram_limit_bytes(&self) -> usize {
        self.ram_limit_bytes.load(Ordering::Relaxed)
    }

    pub fn get_frame(&self, source_id: &str, layer: &str, frame: i32) -> Option<Arc<NativeFrame>> {
        let key = (source_id.to_string(), layer.to_string(), frame);

        // 1. Check RAM cache first
        {
            let mut ram = self.ram_entries.write();
            if let Some(entry) = ram.get_mut(&key) {
                entry.1 = std::time::Instant::now();
                return Some(Arc::clone(&entry.0));
            }
        }

        // 2. Check Disk Cache
        let disk_hit = {
            let disk = self.disk_entries.read();
            disk.get(&key).cloned()
        };

        if let Some((path, meta)) = disk_hit {
            if let Ok(mut file) = File::open(&path) {
                let mut compressed = Vec::new();
                if file.read_to_end(&mut compressed).is_ok() {
                    if let Ok(decompressed) = decompress_size_prepended(&compressed) {
                        let native_frame = Arc::new(NativeFrame {
                            width: meta.width,
                            height: meta.height,
                            channels: meta.channels,
                            is_f16: meta.is_f16,
                            data: Arc::new(decompressed),
                        });

                        // Promote back into RAM
                        self.put_ram(source_id, layer, frame, Arc::clone(&native_frame));
                        return Some(native_frame);
                    }
                }
            }
        }

        None
    }

    /// Retrieve a fast downsampled proxy frame (1/2 or 1/4 resolution) for instant scrub.
    pub fn get_proxy_frame(&self, source_id: &str, layer: &str, frame: i32) -> Option<Arc<NativeFrame>> {
        let key = (source_id.to_string(), layer.to_string(), frame);
        let proxy_map = self.proxy_entries.read();
        proxy_map.get(&key).cloned()
    }

    /// Store a downsampled proxy frame into the proxy cache.
    pub fn put_proxy_frame(&self, source_id: &str, layer: &str, frame: i32, frame_data: Arc<NativeFrame>) {
        let key = (source_id.to_string(), layer.to_string(), frame);
        let mut proxy_map = self.proxy_entries.write();
        proxy_map.insert(key, frame_data);
    }

    pub fn put_frame(&self, source_id: &str, layer: &str, frame: i32, frame_data: Arc<NativeFrame>) {
        self.put_ram(source_id, layer, frame, frame_data);
    }

    fn put_ram(&self, source_id: &str, layer: &str, frame: i32, frame_data: Arc<NativeFrame>) {
        let key = (source_id.to_string(), layer.to_string(), frame);
        let frame_bytes = frame_data.data.len();

        {
            let mut ram = self.ram_entries.write();
            if let Some(old) = ram.insert(key.clone(), (Arc::clone(&frame_data), std::time::Instant::now())) {
                let old_len = old.0.data.len();
                if frame_bytes >= old_len {
                    self.used_ram_bytes.fetch_add(frame_bytes - old_len, Ordering::Relaxed);
                } else {
                    self.used_ram_bytes.fetch_sub(old_len - frame_bytes, Ordering::Relaxed);
                }
            } else {
                self.used_ram_bytes.fetch_add(frame_bytes, Ordering::Relaxed);
            }
        }

        self.evict_if_needed(Some(frame));
    }

    pub fn evict_if_needed(&self, current_playhead: Option<i32>) {
        let max_bytes = self.ram_limit_bytes.load(Ordering::Relaxed);
        let mut cur_bytes = self.used_ram_bytes.load(Ordering::Relaxed);

        if cur_bytes <= max_bytes {
            return;
        }

        let mut ram = self.ram_entries.write();
        let mut keys: Vec<((String, String, i32), usize, i32, std::time::Instant)> = ram
            .iter()
            .map(|(k, v)| (k.clone(), v.0.data.len(), k.2, v.1))
            .collect();

        // Sort by distance from playhead (descending) or LRU
        if let Some(playhead) = current_playhead {
            keys.sort_by_key(|item| std::cmp::Reverse((item.2 - playhead).abs()));
        } else {
            keys.sort_by_key(|item| item.3);
        }

        for (k, byte_len, _, _) in keys {
            if cur_bytes <= max_bytes {
                break;
            }

            if let Some((frame_obj, _)) = ram.remove(&k) {
                cur_bytes = self.used_ram_bytes.fetch_sub(byte_len, Ordering::Relaxed) - byte_len;

                // Spill to LZ4 compressed disk cache
                let meta = CachedMetadata {
                    width: frame_obj.width,
                    height: frame_obj.height,
                    channels: frame_obj.channels,
                    is_f16: frame_obj.is_f16,
                };
                let safe_sid = k.0.replace(|c: char| !c.is_alphanumeric(), "_");
                let safe_lyr = k.1.replace(|c: char| !c.is_alphanumeric(), "_");
                let disk_path = self.disk_dir.join(format!("{}_{}_{}.lz4", safe_sid, safe_lyr, k.2));

                let compressed = compress_prepend_size(&frame_obj.data);
                if let Ok(mut f) = OpenOptions::new().write(true).create(true).truncate(true).open(&disk_path) {
                    let _ = f.write_all(&compressed);
                    self.disk_entries.write().insert(k, (disk_path, meta));
                }
            }
        }
    }

    pub fn get_cached_frames(&self, source_id: &str, layer: &str) -> Vec<i32> {
        let mut frames = std::collections::BTreeSet::new();

        {
            let ram = self.ram_entries.read();
            for (k, _) in ram.iter() {
                if k.0 == source_id && (layer.is_empty() || k.1 == layer) {
                    frames.insert(k.2);
                }
            }
        }

        {
            let disk = self.disk_entries.read();
            for (k, _) in disk.iter() {
                if k.0 == source_id && (layer.is_empty() || k.1 == layer) {
                    frames.insert(k.2);
                }
            }
        }

        frames.into_iter().collect()
    }

    pub fn get_cached_proxy_frames(&self, source_id: &str, layer: &str) -> Vec<i32> {
        let proxy_map = self.proxy_entries.read();
        let mut frames = std::collections::BTreeSet::new();
        for (k, _) in proxy_map.iter() {
            if k.0 == source_id && (layer.is_empty() || k.1 == layer) {
                frames.insert(k.2);
            }
        }
        frames.into_iter().collect()
    }

    pub fn clear(&self) {
        self.ram_entries.write().clear();
        self.proxy_entries.write().clear();
        let mut disk = self.disk_entries.write();
        for (path, _) in disk.values() {
            let _ = fs::remove_file(path);
        }
        disk.clear();
        self.used_ram_bytes.store(0, Ordering::Relaxed);
    }
}
