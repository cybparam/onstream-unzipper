print(">>> GTA V ZERO-SEEK CRC32-VERIFIED STREAM EXTRACTOR...", flush=True)

import bz2
from compression import zstd
from ftplib import FTP
import lzma
import os
import socket
import struct
import sys
import time
import zlib

# =====================================================================
# Fill Your Creds HEre From Cx File Explorer
# =====================================================================
FTP_HOST = "xx.xxx.xxx.xx"  # Your IP. after "ftp://"
FTP_PORT = 0000 #Enter your port here
FTP_USER = "xx" #username here
FTP_PASS = "0000000" #password here
ZIP_PATH = "xxxx/xxxx/xxxxxx.xxx" #file you are copying with path
# =====================================================================

DEST_DIR = r"D:\Extracted_Data" #destination folder
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB turbo buffer


def format_eta(seconds):
    if seconds <= 0 or seconds > 86400 * 7:
        return "calculating..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"


def get_ftp():
    print(f">>> Connecting to {FTP_HOST}:{FTP_PORT} as '{FTP_USER}'...", flush=True)
    ftp = FTP()
    ftp.connect(FTP_HOST, FTP_PORT, timeout=120)
    ftp.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, CHUNK_SIZE)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")  # Binary mode
    return ftp


def extract_stream():
    os.makedirs(DEST_DIR, exist_ok=True)
    ftp = get_ftp()

    total_zip_size = ftp.size(ZIP_PATH)
    total_gb = total_zip_size / (1024**3)
    print(f">>> Connected! Locked on: {ZIP_PATH} ({total_gb:.2f} GB)", flush=True)
    print(">>> Streaming sequentially from start (bypasses Cx 32-bit seek bug)...", flush=True)
    print("-" * 70, flush=True)

    # Do NOT pass rest=... because Cx File Explorer overflows 32-bit integers > 2 GB!
    with ftp.transfercmd(f"RETR {ZIP_PATH}") as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, CHUNK_SIZE)
        buf = bytearray()
        stream_pos = 0
        start_time = time.time()
        bytes_since_start = 0
        extracted_count = 0
        skipped_count = 0
        found_launchers = []

        def read_exact(n):
            nonlocal buf, stream_pos, bytes_since_start
            while len(buf) < n:
                chunk = sock.recv(max(CHUNK_SIZE, n - len(buf)))
                if not chunk:
                    raise EOFError("FTP connection dropped! Re-run script to continue.")
                buf.extend(chunk)
                bytes_since_start += len(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            stream_pos += n
            return out

        def drain_exact(n):
            """Drains n compressed bytes through RAM without touching the SSD."""
            remaining = n
            while remaining > 0:
                step = min(CHUNK_SIZE, remaining)
                read_exact(step)
                remaining -= step

        def print_progress(rel_path, status="Extracting"):
            elapsed = max(0.001, time.time() - start_time)
            speed_bps = bytes_since_start / elapsed
            speed_mb = speed_bps / (1024 * 1024)
            pct = (stream_pos / max(1, total_zip_size)) * 100
            done_gb = stream_pos / (1024**3)
            eta_str = format_eta((total_zip_size - stream_pos) / speed_bps if speed_bps > 0 else 0)
            short_name = rel_path[-25:] if len(rel_path) > 25 else rel_path
            sys.stdout.write(
                f"\r[{pct:5.1f}% | {done_gb:6.2f}/{total_gb:6.2f} GB] "
                f"{speed_mb:5.1f} MB/s | ETA: {eta_str:<9} | {status}: {short_name:<25}"
            )
            sys.stdout.flush()

        while True:
            sig = read_exact(4)

            # Reached Central Directory at the end of the ZIP -> All files extracted!
            if sig in (b"PK\x01\x02", b"PK\x05\x06", b"PK\x06\x06"):
                total_time = format_eta(time.time() - start_time)
                print("\n" + "=" * 70, flush=True)
                print(f"MISSION ACCOMPLISHED in {total_time}!", flush=True)
                print(f"Files Extracted & CRC32-Verified: {extracted_count} (Skipped existing: {skipped_count})", flush=True)
                print("Launchers / Executables unpacked in D:\\Extracted_Data:", flush=True)
                for l_file in found_launchers:
                    print(f"  --> {l_file}", flush=True)
                print("=" * 70, flush=True)
                break

            if sig != b"PK\x03\x04":
                raise ValueError(f"Unexpected ZIP header {sig} at byte {stream_pos - 4}")

            header = read_exact(26)
            version, flags, method, mtime, mdate, crc, comp_size, uncomp_size, name_len, extra_len = (
                struct.unpack("<HHHHHIIIHH", header)
            )

            filename = read_exact(name_len).decode("utf-8", errors="replace")
            extra = read_exact(extra_len)

            # Parse ZIP64 Extended Information Extra Field (0x0001)
            # In Local Headers, ZIP64 extra field contains BOTH uncompressed (8B) and compressed (8B) sizes
            if comp_size == 0xFFFFFFFF or uncomp_size == 0xFFFFFFFF:
                idx = 0
                while idx + 4 <= len(extra):
                    h_id, d_size = struct.unpack("<HH", extra[idx : idx + 4])
                    if h_id == 0x0001:
                        z64 = extra[idx + 4 : idx + 4 + d_size]
                        pos = 0
                        if uncomp_size == 0xFFFFFFFF and pos + 8 <= len(z64):
                            uncomp_size = struct.unpack("<Q", z64[pos : pos + 8])[0]
                            pos += 8
                        if comp_size == 0xFFFFFFFF and pos + 8 <= len(z64):
                            comp_size = struct.unpack("<Q", z64[pos : pos + 8])[0]
                            pos += 8
                        break
                    idx += 4 + d_size

            rel_path = filename.replace("/", os.sep)
            full_path = os.path.join(DEST_DIR, rel_path)
            temp_path = full_path + ".part"

            # Directory entry
            if filename.endswith("/"):
                os.makedirs(full_path, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # Track any launcher / executable / crack files so we can see them clearly
            if any(filename.lower().endswith(ext) for ext in (".exe", ".bat", ".cmd", ".dll", ".ini", ".mel")):
                found_launchers.append(rel_path)
                print(f"\n  [Launcher/Core File]: {rel_path} ({uncomp_size / (1024*1024):.2f} MB)", flush=True)

            has_dd = bool(flags & 0x08)

            # If this file was ALREADY extracted and matches the exact uncompressed size, skip SSD write!
            if (
                not has_dd
                and os.path.exists(full_path)
                and os.path.getsize(full_path) == uncomp_size
            ):
                print_progress(rel_path, status="Skipping")
                drain_exact(comp_size)
                skipped_count += 1
                continue

            calc_crc = 0
            written_bytes = 0

            try:
                with open(temp_path, "wb", buffering=CHUNK_SIZE) as f_out:
                    # Method 0: Stored (Uncompressed)
                    if method == 0 and not has_dd:
                        remaining = comp_size
                        while remaining > 0:
                            chunk = read_exact(min(CHUNK_SIZE, remaining))
                            calc_crc = zlib.crc32(chunk, calc_crc)
                            f_out.write(chunk)
                            written_bytes += len(chunk)
                            remaining -= len(chunk)
                            print_progress(rel_path)

                    # Method 93 / 20: Zstandard (Handles Single & Multi-Frame + large >4GB .rpf files!)
                    elif method in (93, 20) and not has_dd:
                        decompressor = zstd.ZstdDecompressor()
                        remaining = comp_size
                        pending = b""
                        while remaining > 0 or pending or not decompressor.needs_input:
                            if not pending and remaining > 0 and decompressor.needs_input:
                                step = min(CHUNK_SIZE, remaining)
                                pending = read_exact(step)
                                remaining -= len(pending)

                            if decompressor.eof:
                                if pending or remaining > 0:
                                    decompressor = zstd.ZstdDecompressor()
                                else:
                                    break

                            out_chunk = decompressor.decompress(pending, max_length=CHUNK_SIZE)
                            if out_chunk:
                                calc_crc = zlib.crc32(out_chunk, calc_crc)
                                f_out.write(out_chunk)
                                written_bytes += len(out_chunk)

                            pending = decompressor.unused_data
                            print_progress(rel_path)

                    # Method 8: Deflate
                    elif method == 8 and not has_dd:
                        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                        remaining = comp_size
                        while remaining > 0:
                            chunk = read_exact(min(CHUNK_SIZE, remaining))
                            out_chunk = decompressor.decompress(chunk)
                            if out_chunk:
                                calc_crc = zlib.crc32(out_chunk, calc_crc)
                                f_out.write(out_chunk)
                                written_bytes += len(out_chunk)
                            remaining -= len(chunk)
                            print_progress(rel_path)
                        tail_chunk = decompressor.flush()
                        if tail_chunk:
                            calc_crc = zlib.crc32(tail_chunk, calc_crc)
                            f_out.write(tail_chunk)
                            written_bytes += len(tail_chunk)

                    # Method 12 (BZIP2) or Method 14 (LZMA)
                    elif method in (12, 14) and not has_dd:
                        decompressor = bz2.BZ2Decompressor() if method == 12 else lzma.LZMADecompressor()
                        remaining = comp_size
                        while remaining > 0:
                            chunk = read_exact(min(CHUNK_SIZE, remaining))
                            out_chunk = decompressor.decompress(chunk)
                            if out_chunk:
                                calc_crc = zlib.crc32(out_chunk, calc_crc)
                                f_out.write(out_chunk)
                                written_bytes += len(out_chunk)
                            remaining -= len(chunk)
                            print_progress(rel_path)

                    else:
                        raise NotImplementedError(f"Unsupported method={method}, has_dd={has_dd}")

                # Verify exact uncompressed byte count & CRC32 hardware hash!
                if written_bytes != uncomp_size:
                    raise ValueError(
                        f"Size mismatch on {rel_path}: wrote {written_bytes} bytes, expected {uncomp_size} bytes!"
                    )
                if crc != 0 and (calc_crc & 0xFFFFFFFF) != crc:
                    raise ValueError(
                        f"CRC32 corruption detected on {rel_path}! Expected {crc:#x}, got {calc_crc & 0xFFFFFFFF:#x}"
                    )

                if os.path.exists(full_path):
                    os.remove(full_path)
                os.rename(temp_path, full_path)
                extracted_count += 1

            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                print(f"\n[ERROR on {rel_path}]: {e}", flush=True)
                raise e

    ftp.quit()


if __name__ == "__main__":
    try:
        extract_stream()
    except Exception as err:
        import traceback
        print("\n>>> ERROR DETAILS:", flush=True)
        traceback.print_exc()