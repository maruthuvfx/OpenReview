use std::fs;
use std::path::{Path, PathBuf};
use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use rayon::prelude::*;

#[pyclass]
#[derive(Clone, Debug)]
pub struct NativeSequenceInfo {
    #[pyo3(get)]
    pub directory: String,
    #[pyo3(get)]
    pub prefix: String,
    #[pyo3(get)]
    pub separator: String,
    #[pyo3(get)]
    pub padding: usize,
    #[pyo3(get)]
    pub extension: String,
    #[pyo3(get)]
    pub start_frame: i32,
    #[pyo3(get)]
    pub end_frame: i32,
    #[pyo3(get)]
    pub available_frames: Vec<i32>,
    #[pyo3(get)]
    pub missing_frames: Vec<i32>,
}

#[pymethods]
impl NativeSequenceInfo {
    /// Return pattern in printf format, e.g. /path/to/shot.%04d.exr
    #[getter]
    pub fn pattern(&self) -> String {
        let fmt = format!("%0{}d", self.padding);
        let filename = format!("{}{}{}{}", self.prefix, self.separator, fmt, self.extension);
        Path::new(&self.directory).join(filename).to_string_lossy().to_string()
    }

    /// Return pattern in hash format, e.g. /path/to/shot.####.exr
    #[getter]
    pub fn hash_pattern(&self) -> String {
        let hashes = "#".repeat(self.padding);
        let filename = format!("{}{}{}{}", self.prefix, self.separator, hashes, self.extension);
        Path::new(&self.directory).join(filename).to_string_lossy().to_string()
    }

    /// Format specific frame number into absolute path
    pub fn frame_path(&self, frame: i32) -> String {
        let formatted = format!("{:0width$}", frame, width = self.padding);
        let filename = format!("{}{}{}{}", self.prefix, self.separator, formatted, self.extension);
        Path::new(&self.directory).join(filename).to_string_lossy().to_string()
    }
}

/// Deconstruct a filename into (prefix, separator, digits_str, extension)
pub fn parse_sequence_filename(filename: &str) -> Option<(&str, &str, &str, &str)> {
    let dot_idx = filename.rfind('.')?;
    let ext = &filename[dot_idx..];
    let stem = &filename[..dot_idx];

    if stem.is_empty() {
        return None;
    }

    let bytes = stem.as_bytes();
    let mut digit_start = stem.len();
    while digit_start > 0 && bytes[digit_start - 1].is_ascii_digit() {
        digit_start -= 1;
    }

    if digit_start == stem.len() {
        // No digits at end of stem
        return None;
    }

    let digits = &stem[digit_start..];

    if digit_start == 0 {
        // Entire stem is digits, e.g. "1001.exr"
        return Some(("", "", digits, ext));
    }

    let sep_char = bytes[digit_start - 1] as char;
    if sep_char == '.' || sep_char == '_' || sep_char == '-' {
        let prefix = &stem[..digit_start - 1];
        let sep = &stem[digit_start - 1..digit_start];
        Some((prefix, sep, digits, ext))
    } else {
        let prefix = &stem[..digit_start];
        Some((prefix, "", digits, ext))
    }
}

pub fn scan_sequence_impl(target_path_str: &str) -> Result<Option<NativeSequenceInfo>, String> {
    let path = Path::new(target_path_str);
    if !path.exists() {
        return Ok(None);
    }

    let (dir_buf, sample_filename): (PathBuf, String) = if path.is_dir() {
        let mut sample = None;
        let rd = fs::read_dir(path).map_err(|e| e.to_string())?;
        for entry in rd {
            if let Ok(de) = entry {
                let name = de.file_name().to_string_lossy().to_string();
                if parse_sequence_filename(&name).is_some() {
                    sample = Some(name);
                    break;
                }
            }
        }
        match sample {
            Some(s) => (path.to_path_buf(), s),
            None => return Ok(None),
        }
    } else {
        let dir = path.parent().unwrap_or_else(|| Path::new("."));
        let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        (dir.to_path_buf(), name.to_string())
    };

    let (prefix, sep, digits, ext) = match parse_sequence_filename(&sample_filename) {
        Some(res) => res,
        None => return Ok(None),
    };

    let padding = digits.len();
    let prefix_owned = prefix.to_string();
    let sep_owned = sep.to_string();
    let ext_owned = ext.to_string();
    let prefix_sep = format!("{}{}", prefix_owned, sep_owned);
    let ext_lower = ext_owned.to_lowercase();

    // Read all filenames in directory without additional stat calls
    let read_dir = fs::read_dir(&dir_buf).map_err(|e| e.to_string())?;
    let filenames: Vec<String> = read_dir
        .filter_map(|entry| {
            entry.ok().and_then(|de| {
                if de.file_type().map(|ft| ft.is_file()).unwrap_or(false) {
                    de.file_name().into_string().ok()
                } else {
                    None
                }
            })
        })
        .collect();

    // Parallel match & parse using Rayon
    let matched_frames: Vec<i32> = filenames
        .par_iter()
        .filter_map(|name| {
            if !name.starts_with(&prefix_sep) {
                return None;
            }
            if !name.to_lowercase().ends_with(&ext_lower) {
                return None;
            }
            let middle = &name[prefix_sep.len()..name.len() - ext_owned.len()];
            if middle.is_empty() || !middle.chars().all(|c| c.is_ascii_digit()) {
                return None;
            }
            middle.parse::<i32>().ok()
        })
        .collect();

    if matched_frames.is_empty() {
        return Ok(None);
    }

    // Sort and deduplicate frames
    let mut sorted_frames = matched_frames;
    sorted_frames.par_sort_unstable();
    sorted_frames.dedup();

    let start_frame = sorted_frames[0];
    let end_frame = sorted_frames[sorted_frames.len() - 1];

    // Compute missing frame gaps in O(N) linear scan
    let mut missing_frames = Vec::new();
    let mut expected = start_frame;
    for &f in &sorted_frames {
        while expected < f {
            missing_frames.push(expected);
            expected += 1;
        }
        expected = f + 1;
    }

    Ok(Some(NativeSequenceInfo {
        directory: dir_buf.to_string_lossy().to_string(),
        prefix: prefix_owned,
        separator: sep_owned,
        padding,
        extension: ext_owned,
        start_frame,
        end_frame,
        available_frames: sorted_frames,
        missing_frames,
    }))
}

/// PyO3 standalone function to scan sequences in parallel
#[pyfunction]
pub fn scan_sequence(py: Python<'_>, target_path: &str) -> PyResult<Option<NativeSequenceInfo>> {
    py.allow_threads(|| {
        scan_sequence_impl(target_path).map_err(|e| PyRuntimeError::new_err(e))
    })
}
