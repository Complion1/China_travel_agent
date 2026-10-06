"""Explicit web settings; importing this module never reads environment or secrets."""
from __future__ import annotations
import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .errors import WebApplicationConfigurationError

class WebApplicationSettings(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    public_origin: str
    port: int = Field(default=8080, ge=1, le=65535)
    cookie_name: str = Field(default="cta_visitor", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    visitor_ttl_seconds: int = Field(default=2592000, ge=2592000, le=2592000)
    daily_global_limit: int = Field(default=200, ge=1, le=2**53-1)
    daily_visitor_limit: int = Field(default=20, ge=1, le=2**53-1)
    hourly_ip_limit: int = Field(default=5, ge=1, le=2**53-1)
    min_free_disk_bytes: int = Field(default=5368709120, ge=0, le=2**53-1)
    pause_key_file: Path = Field(repr=False)
    data_volume_path: Path
    artifact_max_count: int = Field(default=200000, ge=1, le=2**53-1)
    artifact_max_total_bytes: int = Field(default=4294967296, ge=1048576, le=2**53-1)

    @model_validator(mode="after")
    def validate_paths_origin(self):
        url = urlsplit(self.public_origin)
        try:
            if (url.scheme != "http" or url.path or url.query or url.fragment or
                url.username is not None or url.password is not None or not url.hostname or
                self.public_origin != "http://" + url.netloc or url.netloc != url.netloc.lower()):
                raise ValueError("invalid origin")
            hostname = url.hostname
            if hostname not in {"localhost", "127.0.0.1"}:
                address = ipaddress.ip_address(hostname)
                if getattr(address, "scope_id", None) is not None:
                    raise ValueError("invalid IP scope")
                hostname = address.compressed
            browser_host = f"[{hostname}]" if ":" in hostname else hostname
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("invalid port")
            canonical_netloc = browser_host + (f":{url.port}" if url.port is not None and url.port != 80 else "")
            if url.netloc != canonical_netloc:
                raise ValueError("browser canonical origin required")
        except Exception:
            raise ValueError("invalid origin") from None
        if not self.pause_key_file.is_absolute() or not self.data_volume_path.is_absolute():
            raise ValueError("absolute paths required")
        return self

    @property
    def host(self) -> str:
        return urlsplit(self.public_origin).netloc

def load_web_application_settings(*, environ: Mapping[str, str] | None = None) -> WebApplicationSettings:
    source = os.environ if environ is None else environ
    names = {
        "WEB_PUBLIC_ORIGIN": "public_origin", "WEB_PORT": "port", "WEB_COOKIE_NAME": "cookie_name",
        "WEB_VISITOR_TTL_SECONDS": "visitor_ttl_seconds", "WEB_DAILY_GLOBAL_LIMIT": "daily_global_limit",
        "WEB_DAILY_VISITOR_LIMIT": "daily_visitor_limit", "WEB_HOURLY_IP_LIMIT": "hourly_ip_limit",
        "WEB_MIN_FREE_DISK_BYTES": "min_free_disk_bytes", "WEB_PAUSE_KEY_FILE": "pause_key_file",
        "WEB_DATA_VOLUME_PATH": "data_volume_path", "WEB_ARTIFACT_MAX_COUNT": "artifact_max_count",
        "WEB_ARTIFACT_MAX_TOTAL_BYTES": "artifact_max_total_bytes"}
    try:
        if any(name.startswith("WEB_") and name not in names for name in source):
            raise ValueError()
        values = {}
        for name, field in names.items():
            if name not in source:
                continue
            raw = source[name]
            if type(raw) is not str or not raw:
                raise ValueError()
            if field in {"pause_key_file", "data_volume_path"}:
                values[field] = Path(raw)
            elif field in {"public_origin", "cookie_name"}:
                values[field] = raw
            else:
                if re.fullmatch(r"[0-9]+", raw) is None:
                    raise ValueError()
                values[field] = int(raw)
        return WebApplicationSettings(**values)
    except Exception:
        raise WebApplicationConfigurationError() from None
