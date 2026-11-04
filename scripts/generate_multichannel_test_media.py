#!/usr/bin/env python3
"""
Generate synthetic production-grade Multi-Channel OpenEXR files for testing OpenReview.
Outputs:
- tests/sample_media/multichannel_exr/multichannel_test.exr (standalone)
- tests/sample_media/multichannel_exr/seq/multichannel_seq.0001.exr .. .0010.exr (10-frame sequence)
"""
import os
import sys
import numpy as np
import OpenEXR
import Imath

def create_multichannel_frame(width=256, height=144, frame_idx=1):
    # Coordinate grids
    y, x = np.mgrid[0:height, 0:width]
    nx = x / float(width)
    ny = y / float(height)

    # Animated center for sequence
    cx = 0.5 + 0.1 * np.sin(frame_idx * 0.5)
    cy = 0.5 + 0.1 * np.cos(frame_idx * 0.5)
    dist = np.sqrt((nx - cx)**2 + (ny - cy)**2)
    sphere_mask = (dist < 0.25).astype(np.float32)

    # 1. Beauty (RGBA) - HDR gradient + sphere
    r = (nx * 0.8 + sphere_mask * 0.8).astype(np.float32)
    g = (ny * 0.6 + sphere_mask * 0.3).astype(np.float32)
    b = (0.3 + sphere_mask * 1.2).astype(np.float32)  # HDR value > 1.0
    a = np.ones((height, width), dtype=np.float32)

    # 2. Diffuse (RGB)
    diff_r = (nx * 0.7 + sphere_mask * 0.5).astype(np.float32)
    diff_g = (ny * 0.5 + sphere_mask * 0.2).astype(np.float32)
    diff_b = (0.2 + sphere_mask * 0.3).astype(np.float32)

    # 3. Specular (RGB)
    spec_mask = np.clip(1.0 - (dist / 0.1), 0.0, 1.0) ** 4
    spec_r = (spec_mask * 2.5).astype(np.float32)
    spec_g = (spec_mask * 2.5).astype(np.float32)
    spec_b = (spec_mask * 2.5).astype(np.float32)

    # 4. Normals (N.X, N.Y, N.Z) - signed [-1.0, 1.0]
    norm_x = np.where(sphere_mask > 0, (nx - cx) / 0.25, 0.0).astype(np.float32)
    norm_y = np.where(sphere_mask > 0, (ny - cy) / 0.25, 0.0).astype(np.float32)
    norm_z_sq = np.clip(1.0 - norm_x**2 - norm_y**2, 0.0, 1.0)
    norm_z = np.where(sphere_mask > 0, np.sqrt(norm_z_sq), 1.0).astype(np.float32)

    # 5. Depth (depth.Z) - distance from 15.0 (sphere) to 200.0 (background)
    depth_z = np.where(sphere_mask > 0, 15.0 + dist * 40.0, 200.0 + nx * 50.0).astype(np.float32)

    # 6. Motion (motion.U, motion.V)
    motion_u = (np.cos(frame_idx * 0.5) * 8.0 * sphere_mask).astype(np.float32)
    motion_v = (-np.sin(frame_idx * 0.5) * 8.0 * sphere_mask).astype(np.float32)

    # 7. Cryptomatte (crypto_object00)
    # Object 1 ID: 0x3F8000 (sphere), Object 2 ID: 0x42A000 (left box), 0: background
    id_sphere = np.uint32(0x3F8000).view(np.float32)
    id_box = np.uint32(0x42A000).view(np.float32)
    box_mask = ((nx > 0.05) & (nx < 0.25) & (ny > 0.05) & (ny < 0.35)).astype(np.float32)

    crypto_id = np.zeros((height, width), dtype=np.float32)
    crypto_id[sphere_mask > 0] = id_sphere
    crypto_id[box_mask > 0] = id_box

    crypto_cov = np.zeros((height, width), dtype=np.float32)
    crypto_cov[sphere_mask > 0] = 1.0
    crypto_cov[box_mask > 0] = 1.0

    # 8. Ambient Occlusion (AO)
    ao = np.clip(1.0 - sphere_mask * 0.4 - box_mask * 0.3, 0.0, 1.0).astype(np.float32)

    channels_data = {
        "R": r, "G": g, "B": b, "A": a,
        "diffuse.R": diff_r, "diffuse.G": diff_g, "diffuse.B": diff_b,
        "specular.R": spec_r, "specular.G": spec_g, "specular.B": spec_b,
        "N.X": norm_x, "N.Y": norm_y, "N.Z": norm_z,
        "depth.Z": depth_z,
        "motion.U": motion_u, "motion.V": motion_v,
        "crypto_object00.R": crypto_id, "crypto_object00.G": crypto_cov,
        "crypto_object00.B": np.zeros_like(r), "crypto_object00.A": np.zeros_like(r),
        "AO.R": ao, "AO.G": ao, "AO.B": ao,
    }

    return width, height, channels_data

def save_multichannel_exr(filepath, width, height, channels_data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    header = OpenEXR.Header(width, height)
    header["compression"] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)
    
    ch_dict = {}
    pt_float = Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
    for k in channels_data.keys():
        ch_dict[k] = pt_float
    header["channels"] = ch_dict

    out = OpenEXR.OutputFile(filepath, header)
    pixels_dict = {k: v.tobytes() for k, v in channels_data.items()}
    out.writePixels(pixels_dict)
    out.close()

def main():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "sample_media", "multichannel_exr"))
    os.makedirs(base_dir, exist_ok=True)
    
    # 1. Standalone test file
    standalone_path = os.path.join(base_dir, "multichannel_test.exr")
    w, h, data = create_multichannel_frame(256, 144, frame_idx=1)
    save_multichannel_exr(standalone_path, w, h, data)
    print(f"Generated standalone: {standalone_path} ({os.path.getsize(standalone_path)} bytes)")

    # 2. 10-frame sequence
    seq_dir = os.path.join(base_dir, "seq")
    for i in range(1, 11):
        fpath = os.path.join(seq_dir, f"multichannel_seq.{i:04d}.exr")
        w, h, data = create_multichannel_frame(256, 144, frame_idx=i)
        save_multichannel_exr(fpath, w, h, data)
        print(f"Generated seq frame {i}: {fpath}")

    print("Multi-channel EXR test data generated successfully!")

if __name__ == "__main__":
    main()
