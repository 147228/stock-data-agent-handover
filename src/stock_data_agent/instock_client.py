from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


OADATE_RE = re.compile(r"^/OADate\((-?\d+(?:\.\d+)?)\)/$")


@dataclass(frozen=True)
class InStockConfig:
    base_url: str = "http://127.0.0.1:9988"
    timeout_seconds: float = 10.0
    max_response_bytes: int = 20_000_000
    allow_remote: bool = False
    allowed_modules: tuple[str, ...] = ()


class InStockClient:
    def __init__(self, config: InStockConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/") + "/"
        self._validate_base_url()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "stock-data-agent/0.1 read-only"})

    def _validate_base_url(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        if not self.config.allow_remote and parsed.hostname not in loopback_hosts:
            raise ValueError("remote InStock URL is blocked; use SSH tunnel or explicit allow_remote")

    def _get(self, path: str, *, params: dict | None = None) -> requests.Response:
        if not path.startswith("/"):
            path = "/" + path
        url = urljoin(self.base_url, path.lstrip("/"))
        response = self.session.get(
            url,
            params=params,
            timeout=self.config.timeout_seconds,
            stream=True,
            allow_redirects=False,
        )
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > self.config.max_response_bytes:
            response.close()
            raise ValueError("response exceeds configured max_response_bytes")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > self.config.max_response_bytes:
                response.close()
                raise ValueError("streamed response exceeds configured max_response_bytes")
            chunks.append(chunk)
        response._content = b"".join(chunks)
        response._content_consumed = True
        return response

    def health(self) -> dict:
        response = self._get("/")
        return {
            "ok": response.status_code == 200,
            "status": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "bytes": len(response.content),
            "url": self.base_url,
        }

    def fetch_module(self, module: str, *, date_value: str | None = None) -> tuple[list, dict]:
        if not self.config.allowed_modules:
            raise PermissionError("allowed_modules is empty; fetch-module is blocked")
        if module not in self.config.allowed_modules:
            raise PermissionError(f"module is not approved: {module}")
        params = {"name": module}
        if date_value:
            params["date"] = date_value
        response = self._get("/instock/api_data", params=params)
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise ValueError(f"expected JSON response, got {content_type!r}")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"expected top-level list, got {type(payload).__name__}")
        decoded = _decode_oadates(payload)
        metadata = {
            "fetched_at": datetime.now().astimezone().isoformat(),
            "source": "instock",
            "endpoint": "/instock/api_data",
            "params": {"name": module, "date": date_value},
            "http_status": response.status_code,
            "content_type": content_type,
            "bytes": len(response.content),
            "row_count": len(decoded),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        return decoded, metadata

    def save_module_snapshot(
        self,
        module: str,
        *,
        output: str | Path,
        date_value: str | None = None,
    ) -> tuple[Path, Path]:
        payload, metadata = self.fetch_module(module, date_value=date_value)
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        metadata_path = output_path.with_suffix(output_path.suffix + ".meta.json")
        metadata["normalized_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path, metadata_path


def _decode_oadates(value):
    if isinstance(value, dict):
        return {key: _decode_oadates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_oadates(item) for item in value]
    if isinstance(value, str):
        match = OADATE_RE.match(value)
        if match:
            origin = datetime(1899, 12, 30)
            return (origin + timedelta(days=float(match.group(1)))).date().isoformat()
    return value
