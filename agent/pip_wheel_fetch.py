#!/usr/bin/env python3
"""
pip_wheel_fetch.py <package> <version> <output_dir> [index_base_url]

Resolves the best-matching wheel (or sdist) URL for <package>==<version> from
a PEP 691 JSON "simple" index (pypi.org by default; pass index_base_url to
target another index, e.g. https://download.pytorch.org/whl/cpu for the
CPU-only torch build), then downloads it using HTTP Range requests so a
mid-transfer connection drop only costs the not-yet-written tail of the
current chunk, not the whole file. This build environment reproducibly
breaks plain long-running HTTPS downloads after a few hundred KB to a few MB
(same package, different versions, same failure, and it also hits the index
metadata request itself for packages with long release histories) —
resuming in small chunks routes around that instead of gambling on one
unbroken stream. Prints the local file path on success; exits non-zero on
failure.
"""
import json
import os
import re
import sys
import urllib.request

CHUNK = 65536
DEFAULT_INDEX = "https://pypi.org/simple"
# Some CDNs (seen on PyTorch's index) reject requests without a recognizable
# User-Agent — urllib's default ("Python-urllib/x.y") gets a 403. Send
# something that looks like a normal pip install.
DEFAULT_HEADERS = {"User-Agent": "pip/24.0"}


def normalize(name):
    # PEP 503 normalization
    return re.sub(r"[-_.]+", "-", name).lower()


def get_total_size(headers, status):
    if status == 206:
        content_range = headers.get("Content-Range", "")
        if "/" in content_range:
            try:
                return int(content_range.rsplit("/", 1)[1])
            except ValueError:
                pass
    content_length = headers.get("Content-Length")
    return int(content_length) if content_length else None


def download_resume(url, out_path, headers=None, attempts=80, timeout=20):
    """Fetch url to out_path, resuming via HTTP Range on any failure."""
    expected = None
    for attempt in range(1, attempts + 1):
        existing = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if expected is not None and existing >= expected:
            return True

        req = urllib.request.Request(url, headers={**DEFAULT_HEADERS, **(headers or {})})
        if existing:
            req.add_header("Range", f"bytes={existing}-")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            sys.stderr.write(f"[attempt {attempt}] connect failed: {e}\n")
            continue

        total = get_total_size(resp.headers, resp.status)
        if total is not None:
            expected = total
        # Server may ignore our Range header and send the full file (200) —
        # detect that and restart the local file instead of appending.
        append = bool(existing) and resp.status == 206
        try:
            with open(out_path, "ab" if append else "wb") as f:
                while True:
                    try:
                        chunk = resp.read(CHUNK)
                    except Exception as e:
                        sys.stderr.write(f"[attempt {attempt}] read failed: {e}\n")
                        break
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            resp.close()

        size_now = os.path.getsize(out_path)
        if expected is not None and size_now >= expected:
            return True
        sys.stderr.write(f"[attempt {attempt}] {size_now}/{expected} bytes, resuming...\n")
    return False


def fetch_json_resume(url, tmp_path, headers=None):
    if not download_resume(url, tmp_path, headers=headers):
        sys.exit(f"failed to fully download index metadata from {url}")
    with open(tmp_path, "rb") as f:
        return json.load(f)


def pick_url(files):
    # Use pip's own vendored tag-compatibility logic (packaging.tags) rather
    # than guessing by filename substring — this is what decides which wheel
    # pip itself would have picked for the interpreter actually running this
    # script, so it can't drift from the real target platform/ABI.
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import InvalidWheelFilename, parse_wheel_filename

    tag_rank = {tag: i for i, tag in enumerate(sys_tags())}

    best, best_rank, sdist = None, None, None
    for f in files:
        fn = f["filename"]
        if fn.endswith((".tar.gz", ".zip")):
            sdist = sdist or f
            continue
        if not fn.endswith(".whl"):
            continue
        try:
            _, _, _, tags = parse_wheel_filename(fn)
        except InvalidWheelFilename:
            continue
        for tag in tags:
            rank = tag_rank.get(tag)
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank, best = rank, f
    return best or sdist


def resolve_via_json_index(pkg, ver, index_base, outdir):
    """PEP 691 JSON simple index — fast (one small request), but not every
    third-party index supports it (e.g. download.pytorch.org only serves the
    classic HTML format)."""
    index_url = f"{index_base.rstrip('/')}/{normalize(pkg)}/"
    meta_tmp = os.path.join(outdir, f".{normalize(pkg)}-index.json")
    data = fetch_json_resume(
        index_url, meta_tmp, headers={"Accept": "application/vnd.pypi.simple.v1+json"}
    )

    files = [f for f in data.get("files", []) if f.get("version") == ver]
    # Some indexes (e.g. simple JSON without per-file "version") don't tag
    # files with a version field — fall back to matching it in the filename.
    if not files:
        files = [
            f
            for f in data.get("files", [])
            if f"-{ver}-" in f["filename"] or f["filename"].startswith(f"{pkg}-{ver}")
        ]

    chosen = pick_url(files)
    return (chosen["url"], chosen["filename"]) if chosen else (None, None)


def resolve_via_html_index(pkg, ver, index_base, outdir):
    """Fallback for indexes that don't speak PEP 691 JSON (e.g. PyTorch's
    index only serves the classic PEP 503 HTML format). This is what pip
    itself parses to find a download URL cheaply (a small page of <a> links,
    no per-file metadata fetch needed) — a --dry-run/--report resolve isn't
    equivalent here since some indexes have no PEP 658 metadata files, which
    makes pip fall back to downloading the *entire* wheel just to read its
    metadata (torch is ~190MB, exactly the file we're trying to avoid
    gambling on)."""
    from urllib.parse import urljoin

    index_url = f"{index_base.rstrip('/')}/{normalize(pkg)}/"
    meta_tmp = os.path.join(outdir, f".{normalize(pkg)}-index.html")
    if not download_resume(index_url, meta_tmp):
        return None, None
    with open(meta_tmp, encoding="utf-8", errors="replace") as f:
        html = f.read()

    files = []
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', html, re.IGNORECASE):
        href, text = m.group(1), m.group(2).strip()
        files.append({"filename": text, "url": urljoin(index_url, href)})

    files = [f for f in files if f"-{ver}-" in f["filename"] or f["filename"].startswith(f"{pkg}-{ver}")]
    chosen = pick_url(files)
    return (chosen["url"], chosen["filename"]) if chosen else (None, None)


def main():
    pkg, ver, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    index_base = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_INDEX

    os.makedirs(outdir, exist_ok=True)

    try:
        url, filename = resolve_via_json_index(pkg, ver, index_base, outdir)
    except Exception as e:
        sys.stderr.write(f"JSON index resolution failed ({e}), trying HTML index...\n")
        url, filename = None, None

    if not url:
        url, filename = resolve_via_html_index(pkg, ver, index_base, outdir)

    if not url:
        sys.exit(f"no matching release file found for {pkg}=={ver} via {index_base}")

    out_path = os.path.join(outdir, filename)
    if not download_resume(url, out_path):
        sys.exit(f"failed to fully download {filename}")

    print(out_path)


if __name__ == "__main__":
    main()
