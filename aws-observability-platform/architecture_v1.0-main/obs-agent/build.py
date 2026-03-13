"""
Lambda 배포 패키지 생성 (Windows / macOS / Linux 공통).
zip 명령 없이 Python만으로 dist/telemetry.zip 을 만든다.

사용법:
  cd obs-agent
  pip install -r requirements.txt -t package   # 최초 1회 또는 의존성 변경 시
  python build.py
"""
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
PACKAGE_DIR = ROOT / "package"
APP_DIR = ROOT / "app"
OUTPUT_ZIP = DIST / "telemetry.zip"


def add_dir_to_zip(zf: zipfile.ZipFile, src: Path, arc_prefix: str = "") -> None:
    """디렉터리 내용을 zip에 추가. arc_prefix 는 zip 내부 경로 접두사."""
    if not src.is_dir():
        return
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
            arcname = arc_prefix + f.relative_to(src).as_posix()
            zf.write(f, arcname)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)

    if not PACKAGE_DIR.is_dir():
        raise SystemExit(
            "package/ 폴더가 없습니다. 먼저 실행하세요:\n"
            "  pip install -r requirements.txt -t package"
        )
    if not APP_DIR.is_dir():
        raise SystemExit("app/ 폴더가 없습니다.")

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        add_dir_to_zip(zf, PACKAGE_DIR, "")
        add_dir_to_zip(zf, APP_DIR, "app/")

    print(f"Created: {OUTPUT_ZIP}")


if __name__ == "__main__":
    main()
