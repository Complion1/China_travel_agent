"""Explicit, exclusive key creation. Startup never calls this utility."""
import argparse
import base64
import os
import secrets
from pathlib import Path
from .errors import WebApplicationConfigurationError
from .security import KEY_VERSION, canonical_json

def create_key(*, output: Path) -> None:
    if not output.is_absolute():
        raise WebApplicationConfigurationError()
    try:
        with output.open("xb") as stream:
            os.chmod(output, 0o600)
            stream.write(canonical_json({"version": KEY_VERSION, "key": base64.b64encode(secrets.token_bytes(32)).decode("ascii")}))
    except Exception:
        raise WebApplicationConfigurationError() from None

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_key(output=args.output)
    except WebApplicationConfigurationError:
        print("无法创建密钥文件：要求绝对路径且文件不能已存在")
        return 2
    print("已创建")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
