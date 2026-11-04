use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use crossbeam_channel::{unbounded, Sender, Receiver};
use crate::cache::TieredCache;
use crate::decoder::ExrDecoder;
use crate::dpx::DpxDecoder;
use crate::tiff::TiffDecoder;

pub struct PrefetchTask {
    pub source_id: String,
    pub layer: String,
    pub frame: i32,
    pub path: PathBuf,
    pub generation: u64,
    pub proxy_scale: u32,
}

pub struct PrefetchScheduler {
    generation: Arc<AtomicU64>,
    _cache: Arc<TieredCache>,
    task_sender: Sender<PrefetchTask>,
    is_cancelled: Arc<AtomicBool>,
}

impl PrefetchScheduler {
    pub fn new(cache: Arc<TieredCache>, worker_count: usize) -> Self {
        let (task_sender, task_receiver): (Sender<PrefetchTask>, Receiver<PrefetchTask>) = unbounded();
        let is_cancelled = Arc::new(AtomicBool::new(false));
        let generation = Arc::new(AtomicU64::new(0));

        let num_threads = if worker_count == 0 {
            std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4).saturating_sub(2).max(1)
        } else {
            worker_count
        };

        for _ in 0..num_threads {
            let rx = task_receiver.clone();
            let cache_ref = Arc::clone(&cache);
            let cancel_ref = Arc::clone(&is_cancelled);
            let gen_ref = Arc::clone(&generation);

            std::thread::Builder::new()
                .name("openreview_prefetch_worker".into())
                .spawn(move || {
                    #[cfg(windows)]
                    unsafe {
                        #[link(name = "kernel32")]
                        extern "system" {
                            fn GetCurrentThread() -> *mut std::ffi::c_void;
                            fn SetThreadPriority(hThread: *mut std::ffi::c_void, nPriority: i32) -> i32;
                        }
                        // THREAD_PRIORITY_BELOW_NORMAL (-1) yields full CPU priority to Qt GUI thread
                        SetThreadPriority(GetCurrentThread(), -1);
                    }

                    while let Ok(task) = rx.recv() {
                        let cur_gen = gen_ref.load(Ordering::Relaxed);
                        // Instant task drop if generation is stale or scrub cancellation is active
                        if task.generation < cur_gen || cancel_ref.load(Ordering::Relaxed) {
                            continue;
                        }

                        // Check if already in cache
                        if task.proxy_scale >= 2 {
                            if cache_ref.get_proxy_frame(&task.source_id, &task.layer, task.frame).is_some() {
                                continue;
                            }
                        } else if cache_ref.get_frame(&task.source_id, &task.layer, task.frame).is_some() {
                            continue;
                        }

                        let ext = task
                            .path
                            .extension()
                            .and_then(|s| s.to_str())
                            .unwrap_or("")
                            .to_lowercase();

                        let frame_opt = if ext == "exr" {
                            let lyr = if task.layer.is_empty() { "beauty" } else { &task.layer };
                            ExrDecoder::read_channels(&task.path, Some(lyr), None, None, None, None, false, false, None, true).ok()
                        } else if ext == "dpx" {
                            DpxDecoder::read_dpx(&task.path, true).ok()
                        } else if ext == "tif" || ext == "tiff" {
                            TiffDecoder::read_tiff(&task.path, true).ok()
                        } else {
                            None
                        };

                        // Check generation again before storing into cache
                        if gen_ref.load(Ordering::Relaxed) > task.generation || cancel_ref.load(Ordering::Relaxed) {
                            continue;
                        }

                        if let Some(frame) = frame_opt {
                            let scale = if task.proxy_scale >= 2 { task.proxy_scale } else { 2 };
                            if let Ok(proxy_frame) = frame.downsample(scale) {
                                cache_ref.put_proxy_frame(&task.source_id, &task.layer, task.frame, Arc::new(proxy_frame));
                            }
                            // Store full frame in RAM if there is comfortable headroom (under 75% limit)
                            let max_bytes = cache_ref.get_ram_limit_bytes();
                            let cur_bytes = cache_ref.get_used_ram_bytes();
                            if cur_bytes < (max_bytes * 3) / 4 {
                                cache_ref.put_frame(&task.source_id, &task.layer, task.frame, Arc::new(frame));
                            }
                        }
                    }
                })
                .expect("Failed to spawn prefetch worker thread");
        }

        Self {
            generation,
            _cache: cache,
            task_sender,
            is_cancelled,
        }
    }

    pub fn queue_prefetch(&self, items: Vec<(String, String, i32, PathBuf)>, proxy_scale: u32) {
        self.is_cancelled.store(false, Ordering::Relaxed);
        let gen = self.generation.load(Ordering::Relaxed);

        for (source_id, layer, frame, path) in items {
            let task = PrefetchTask {
                source_id,
                layer,
                frame,
                path,
                generation: gen,
                proxy_scale,
            };
            let _ = self.task_sender.send(task);
        }
    }

    pub fn cancel_all(&self) {
        self.generation.fetch_add(1, Ordering::Relaxed);
        self.is_cancelled.store(true, Ordering::Relaxed);
    }
}
