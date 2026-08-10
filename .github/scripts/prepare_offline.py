#!/usr/bin/env python3
"""Build the static GitHub Pages artifact and its offline caches."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit


SOURCE = Path.cwd()
DESTINATION = SOURCE / "_site"
EXCLUDED_TOP_LEVEL = {".git", ".github", "_site"}
TEXT_SUFFIXES = {".html", ".htm", ".js", ".mjs", ".css", ".json"}
ASSET_REFERENCE = re.compile(
    r"(?P<url>(?:\.\.?/)?[A-Za-z0-9_.@+%/-]+\."
    r"(?:html?|m?js|css|json|png|jpe?g|gif|svg|webp|ico|bin|elf|wasm)"
    r"(?:\?[^\"'\s<>)]*)?)",
    re.IGNORECASE,
)


def copy_site() -> None:
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    DESTINATION.mkdir()

    for item in SOURCE.iterdir():
        if item.name in EXCLUDED_TOP_LEVEL:
            continue
        target = DESTINATION / item.name
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, target)


def relative_files() -> list[str]:
    return sorted(
        path.relative_to(DESTINATION).as_posix()
        for path in DESTINATION.rglob("*")
        if path.is_file() and path.name not in {"cache.appcache", "service-worker.js"}
    )


def build_service_worker(files: list[str], version: str) -> None:
    urls = ["./"] + ["./" + quote(path, safe="/@:+,=-._~") for path in files]
    worker = f"""'use strict';

const CACHE_NAME = {json.dumps('slopkit-' + version)};
const PRECACHE_URLS = {json.dumps(urls, indent=2)};

self.addEventListener('install', event => {{
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
}});

self.addEventListener('activate', event => {{
  event.waitUntil(
    caches.keys()
      .then(names => Promise.all(names.filter(name => name !== CACHE_NAME).map(name => caches.delete(name))))
      .then(() => self.clients.claim())
  );
}});

self.addEventListener('fetch', event => {{
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.match(event.request, {{ignoreSearch: true}}).then(cached => {{
      if (cached) return cached;
      return fetch(event.request).then(response => {{
        if (response && response.ok && new URL(event.request.url).origin === self.location.origin) {{
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        }}
        return response;
      }}).catch(() => {{
        if (event.request.mode === 'navigate') return caches.match('./index.html');
        throw new Error('Offline asset is not cached');
      }});
    }})
  );
}});
"""
    (DESTINATION / "service-worker.js").write_text(worker, encoding="utf-8")


def query_variants() -> set[str]:
    variants: set[str] = set()
    for source_file in DESTINATION.rglob("*"):
        if not source_file.is_file() or source_file.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = source_file.read_text(encoding="utf-8", errors="ignore")
        source_dir = source_file.relative_to(DESTINATION).parent.as_posix()
        for match in ASSET_REFERENCE.finditer(text):
            raw_url = match.group("url")
            parsed = urlsplit(raw_url)
            if not parsed.query or parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
                continue
            normalized = posixpath.normpath(posixpath.join(source_dir, parsed.path))
            if normalized == ".." or normalized.startswith("../"):
                continue
            if (DESTINATION / normalized).is_file():
                variants.add("./" + quote(normalized, safe="/@:+,=-._~") + "?" + parsed.query)
    return variants


def build_appcache(files: list[str], version: str) -> None:
    entries = {"./", "./index.html", "./service-worker.js"}
    entries.update("./" + quote(path, safe="/@:+,=-._~") for path in files)
    entries.update(query_variants())

    manifest = [
        "CACHE MANIFEST",
        "# build " + version,
        "",
        "CACHE:",
        *sorted(entries),
        "",
        "NETWORK:",
        "*",
        "",
    ]
    (DESTINATION / "cache.appcache").write_text("\n".join(manifest), encoding="utf-8")


def validate_output() -> None:
    required = [
        DESTINATION / "index.html",
        DESTINATION / "slopkit" / "poops.html",
        DESTINATION / "service-worker.js",
        DESTINATION / "cache.appcache",
    ]
    missing = [str(path.relative_to(DESTINATION)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Missing required Pages files: " + ", ".join(missing))

    index = (DESTINATION / "index.html").read_text(encoding="utf-8")
    if 'manifest="cache.appcache"' not in index:
        raise SystemExit("index.html does not reference cache.appcache")
    if "trigger=netcontrol" not in index or "payload=1" not in index:
        raise SystemExit("index.html no longer launches the intended jailbreak and ELF loader")


def main() -> None:
    version = os.environ.get("GITHUB_SHA", "local-build")[:12]
    copy_site()
    files = relative_files()
    build_service_worker(files, version)
    build_appcache(files, version)
    validate_output()
    print(f"Prepared {len(files)} source files for offline use (build {version}).")


if __name__ == "__main__":
    main()
