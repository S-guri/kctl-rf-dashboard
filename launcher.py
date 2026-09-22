"""KCTL RF Inspector 실행 파일(.exe) 진입점.

PyInstaller 로 패키징된 상태에서 Streamlit 서버를 직접 부팅하고
기본 브라우저를 열어준다. (streamlit CLI 를 거치지 않으므로
최초 실행 시 이메일 입력 프롬프트가 뜨지 않는다.)
"""

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def bundle_dir() -> Path:
    """번들 내부 리소스(app.py, 자산) 위치."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    """실행 파일이 놓인 폴더 (.env 등 사용자 편집 파일 위치)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def free_port(preferred: int = 8501) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return 0  # OS 자동 할당


def main() -> None:
    # 1) 실행 파일 옆의 .env 를 먼저 로드 (재빌드 없이 API 키 교체 가능)
    try:
        from dotenv import load_dotenv

        for candidate in (exe_dir() / ".env", bundle_dir() / ".env"):
            if candidate.exists():
                load_dotenv(candidate, override=True)
                print(f"[설정] .env 로드: {candidate}")
                break
        else:
            print("[설정] .env 없음 - 사이드바에서 API Key 를 직접 입력하세요.")
    except Exception as e:  # noqa: BLE001
        print(f"[경고] .env 로드 실패: {e}")

    app_path = bundle_dir() / "app.py"
    if not app_path.exists():
        print(f"[오류] app.py 를 찾을 수 없습니다: {app_path}")
        input("엔터를 누르면 종료합니다...")
        return

    # 자산(img_*.png, report_template.docx) 탐색이 번들 기준으로 되도록
    os.chdir(bundle_dir())

    port = free_port()
    url = f"http://localhost:{port}"

    from streamlit import config as st_config
    from streamlit.web import bootstrap

    st_config.set_option("server.port", port)
    st_config.set_option("server.headless", True)          # CLI 이메일 프롬프트 회피
    st_config.set_option("server.fileWatcherType", "none")  # 번들 환경에서 불필요
    st_config.set_option("browser.gatherUsageStats", False)
    st_config.set_option("global.developmentMode", False)

    print("=" * 60)
    print(" Eurofins KCTL RF 계측 자동 분석 대시보드")
    print("=" * 60)
    print(f" 주소: {url}")
    print(" 종료: 이 창을 닫거나 Ctrl+C")
    print("=" * 60)

    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    try:
        bootstrap.run(str(app_path), False, [], {})
    except KeyboardInterrupt:
        print("\n종료합니다.")
    except Exception as e:  # noqa: BLE001
        print(f"[오류] 서버 실행 실패: {e}")
        input("엔터를 누르면 종료합니다...")


if __name__ == "__main__":
    main()
