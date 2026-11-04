# Changelog

All notable changes to **OpenReview** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-11-04

### Architecture Milestone: Native Rust Engine
- **Native Rust Acceleration Engine (`crates/openreview-engine`)**: Major architecture transition incorporating compiled Rust extensions (`openreview_engine`) with PyO3, Rayon parallel work-stealing decoders, memory-mapped I/O (`memmap2`), and LZ4 NVMe cache streaming.
- **50% RAM Savings**: Native half-float (`f16`) RGBA frame buffers in memory reducing memory consumption from 31.6 MB/frame down to 15.8 MB/frame on 1080p HDR plates.

### Performance Benchmark Results (v2.0.0 Rust vs v1.3.0 Python)
| Performance Metric | v1.3.0 (Python Baseline) | v2.0.0 (Native Rust Engine) | Speedup / Improvement |
| :--- | :--- | :--- | :--- |
| **Multi-Channel Contact Sheet (8 passes)** | 63.05 ms / sheet | **12.14 ms / sheet** | **5.2x Faster** (Direct Rayon single-pass parse) |
| **VFX Sequence Discovery (1,200 frames)** | 6.18 ms | **1.91 ms** | **3.2x Faster** (Native parallel directory scanner) |
| **Frame Buffer Allocation Overhead** | 27.00 us / frame | **3.96 us / frame** | **7.0x Faster** (92.6% pool hit ratio, Zero GC) |
| **Async Scopes GUI Dispatch Stall** | 247.37 ms (GUI Frozen) | **0.11 ms** | **2,138x Latency Reduction** (Drop-free 60 FPS) |
| **Video Reverse Scrubbing (GOP Cache)** | 39.73 ms / frame | **0.11 ms / frame** | **355.9x Faster** (Instant in-memory GOP seek) |
| **GPU Texture Upload Dispatch (PBO DMA)** | 66.25 ms / frame | **3.09 us / frame** | **21,441x Latency Reduction** (Decoupled DMA) |
| **HDR Frame Memory Footprint (1080p)** | 31.6 MB / frame (f32) | **15.8 MB / frame (f16)** | **50% Memory Savings** |

### Added
- **Multi-Channel EXR Matrix & Routing (`hud_channel_matrix.py`, `exr_matrix.py`)**: Interactive matrix grid for routing arbitrary EXR channels (Color, Diffuse, Specular, Normals, Depth, Cryptomatte) to RGBA viewports with quick soloing presets.
- **Batch Media Export Queue & Deliverables Manager (`batch_export_dialog.py`, `batch_export.py`)**: Dedicated batch export manager supporting multi-job queues, Apple ProRes (422/4444), H.264, H.265, and image sequence deliverables with hardware acceleration autodetection (NVENC, QSV, AMF, VideoToolbox).
- **Studio Workspaces Manager (`workspaces.py`)**: Built-in production layout presets including "Review & Dailies", "Color & QC", "Comp Analysis", and "Multi-Channel EXR", with persistent custom workspace creation and hotkey switching.
- **Interactive Color Sliders Overlay (`color_sliders_overlay.py`)**: Dynamic viewport overlay with precision ruler sliders for Gain, Gamma, Saturation, and Hue, supporting live two-way synchronization with the top control bar.
- **Hierarchical Metadata Tree Inspector (`metadata_tree_widget.py`, `metadata_tree.py`)**: Unified multi-domain attribute inspector organizing production metadata into Pinned Key Attributes, OpenEXR Headers, ExifTool camera tags, Video Codecs, and OCIO transforms with instant text filtering.
- **Asynchronous Off-Thread Scopes Engine (`hud_scopes_async.py`)**: Decoupled background calculation for Waveform, Vectorscope, and Histogram monitors, completely eliminating main GUI thread stalls during 4K/8K playback.
- **Audio Micro-Scrubber with De-Clicking (`micro_scrubber.py`)**: Sub-frame accurate audio transport engine featuring cosine attack/decay envelopes to eliminate playback audio pop/click artifacts during scrub.
- **Zero-Copy Frame Buffer Pool (`buffer_pool.py`)**: High-throughput memory pool recycling large image array buffers across decode and playback cycles.
- **OCIO 3D LUT Baker (`ocio_baker.py`)**: High-speed LUT baking engine with analytical ACES support, multi-view prewarming, and LRU cache eviction.

---

## [1.3.0] - 2026-11-01

### Added
- **Live Real-Time Waveform Monitor (`hud_waveform.py`)**: Added dedicated Waveform HUD supporting RGB Overlay, RGB Parade, and Luma (Y) trace modes with phosphor intensity glow and broadcast graticule scale.
- **Live Real-Time Vectorscope Monitor (`hud_vectorscope.py`)**: Added dedicated Vectorscope HUD displaying chrominance (Cb, Cr) signals, Rec.709 75%/100% color targets, I-axis / skin-tone line, and trace accumulation.
- **Generic Standard Colorspaces Catalog (`standard_colorspaces.py`)**: Integrated Foundry Nuke-aligned transfer functions and curves for linear, sRGB, rec709, Cineon, Gamma 1.8, Gamma 2.2, Panalog, REDLog, ViperLog, REDSpace, and Raw.
- **Intelligent Auto Colorspace Detection**: Automatic identification of color spaces from file metadata, camera header tags (RED, ARRI, Sony S-Log), and container attributes with fallback heuristics.
- **Per-Clip Colorspace Assignment & Persistence**: Dropdown selector in Sources panel to override input color space per media item, persisted seamlessly across sessions.
- **Adaptive Playback & Scrubbing Engine**: Dynamic proxy downsampling (`InteractiveDecodeTask`) for butter-smooth, drop-free playback and scrubbing across 4K/8K media, instantly upgrading to pristine full-resolution upon pausing or resting.
- **Asynchronous Dual-Queue & Cooperative Cancellation**: Non-blocking reader engine with `CancellationToken` preventing queue backpressure during rapid JKL bidirectional transport.
- **Dedicated Frame Rate Dialog (`frame_rate_dialog.py`)**: Sleek keyboard-first floating modal for selecting playback FPS with standard VFX preset pills (23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60), direct numerical entry, and `Ctrl+Alt+R` hotkey.
- **Per-Media Premultiplied Alpha Control**: Per-clip premultiplication toggle with auto-detection for transparent PNG/EXR graphics.
- **Per-Media Annotation Stashing**: Vector drawings and notes are automatically stashed and restored per source clip across session switches.
- **Optimized PNG-8 Palette Export**: High-performance PNG export pipeline with PNG-8 quantized palette mode, configurable Floyd-Steinberg dithering, and multithreaded compression.
- **Automatic Downscaling (> Full HD)**: Delivery preference and export dialog option to automatically downscale 4K/8K images to Full HD (1920x1080) for rapid client previews and review sheets.
- **New Shortcuts & Navigation**: Added Export Annotations (`Ctrl+Shift+E`), Fit Width (`W` / `Shift+W` / `Alt+W`), and protected viewer zoom-scroll against accidental trackpad triggers.

### Changed
- **Default Image Export Format**: Set JPEG (85% quality) as the default format for single frame and image sequence exports with automatic Full HD downscaling support.
- **Session Window Title**: Enhanced window title bar with clean asterisk (`*`) indicators for unsaved changes and clear file paths.
- **Preferences Dialog**: Added new "Export & Delivery" configuration tab exposing default formats, PNG quantization parameters, and PDF report styling.

### Fixed
- **Scrubbing Bottlenecks**: Eliminated UI hangs during rapid scrub by canceling stale in-flight decode tasks before queuing new ones.
- **Viewer Trackpad Zoom Sensitivity**: Protected against inadvertent zoom spikes from high-precision trackpads and mouse wheel bounce.
- **Menu Hierarchy**: Streamlined Help and Window menus, removing duplicate action registrations.

---

## [1.2.0] - 2026-10-25

### Added
- **Draggable Scroll Area**: Added `DraggableScrollArea` widget allowing smooth middle-mouse and touch-drag canvas panning across multi-source panels and inspectors.
- **Floating HUD Opacity & Dynamic Resizing**: Interactive opacity control slider and 8-directional edge/corner resize handles for all 12 floating workstation HUDs.
- **System Utilities Module**: Added `system_utils.py` with cross-platform physical RAM detection, GPU driver inspection, and OS file manager reveal helpers.
- **Deep Source Media Metadata**: Full extraction of EXR channel topologies, video container streams, audio sampling rates, pixel aspect ratios, and color space tags.
- **Viewer Drag Overlay**: Visual drag-and-drop overlay indicator on the OpenGL viewport providing instant feedback during clip ingestion.
- **PDF Export Preferences & Layout**: Configurable page orientation, margin presets, DPI scaling, and multi-frame contact sheet report layouts.
- **Dynamic Stack Source Details**: Live metadata and resolution readouts dynamically linked to the active layer within composite stacks.

### Fixed
- **Cache Eviction**: Resolved ring buffer cache lockups during rapid JKL bidirectional scrubbing over high-bitrate video clips.
- **Timeline Transport Bounds**: Corrected frame clamp math when moving between sequences with disparate frame numbering (e.g. 1001-1150 vs 1-240).
- **Comp QC Softclip Knee**: Fixed highlight compression curve math in the `xtra-qc-Utils` diagnostic shader.
- **Annotation Frame-Matching**: Fixed stroke undo/redo history retention when scrubbing across non-annotated intermediate frames.
- **Sequence Parser**: Improved single-frame VFX plate detection and non-sequential frame range parsing.

---

## [1.1.0] - 2026-10-18

### Added
- **Built-in Comp QC Package (`xtra-qc-Utils`)**: Complete compositing quality control toolset with non-destructive diagnostic color adjustments, softclip knee highlight compression, and specialized inspection filters (Grain Check, High-Pass, Black Levels, NaN/Inf hunting, HSL soloing).
- **About & License Dialog**: Full interactive credits dialog with system specifications and searchable third-party open-source license browser (`licenses.py`).
- **Modernized Dock Title Bars**: Custom dark-themed dock headers with dedicated minimize, undock/float, and close controls.
- **Dynamic Two-Tier Caching**: High-speed RAM ring buffer coupled with persistent multi-gigabyte `DiskCache` prefetching.
- **Studio Feature Manual**: Exhaustive 12-domain technical documentation guide (`docs/features.md`).

---

## [1.0.1] - 2026-10-15

### Added
- **Multi-Part OpenEXR Contact Sheet**: Grid inspection and soloing of all sub-parts, multi-layer AOVs, and display windows.
- **Deep EXR Metadata Inspector**: Detailed header inspection, channel mappings, compression algorithms, and chromaticities.
- **Async Source Loader Engine**: Multi-threaded non-blocking loader with cancellation tokens for ultra-high-bitrate clips.
- **Persistent In/Out Auto-Snap & Range Memory**: Per-view In/Out mark retention and auto-fitting across session switches.
- **High-DPI Vector Icons & Branding**: Complete multi-resolution Windows ICO (16px to 256px), horizontal header logo, and scalable SVG suite.

---

## [1.0.0] - 2026-10-14

### Added
- **Initial Production Release**: Studio-grade VFX sequence playback, HDR color inspection, compositing comparison, and dailies review desktop workstation.
- **Hardware-Accelerated Viewport**: OpenGL 3.3+ Core Profile with double-buffered asynchronous PBO DMA texture streaming and native `RGBA16F`/`RGBA32F` float textures.
- **9-Stage GLSL Color Pipeline**: Seamless OpenColorIO v2 integration (ACES 1.3 / 2.0, Cineon, LogC, Rec.709, sRGB), Exposure, Gain, Gamma, ASC CDL, 3D LUTs, Invert, and Out-of-Range zebra warnings.
- **A/B Split Wipes & Difference QC**: Multi-angle split wipe controller with difference boost and plate-to-comp alignment tools.
- **Resolution-Independent Vector Markups**: Freehand pen, shapes, callouts, and frame annotations with export to CSV/JSON/burn-in video.
- **Sub-Frame Audio Engine & Dailies Sync**: Scrubbing audio bursts, drift resync, and multi-user TCP remote playback sync.
- **Distribution Packages**: 64-bit Windows Installer (.msi), Windows Portable (.zip), Linux Debian (.deb), and Linux RedHat (.rpm).
