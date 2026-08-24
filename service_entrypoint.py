import sys
from pathlib import Path

from service_logging import configure_service_logging


LOG_DIRECTORY = Path.home() / "Library" / "Logs" / "AiYoutuber"


# main.pyの読込失敗も残せるよう、アプリ本体を読み込む前にログを設定します。
sys.stdout, sys.stderr = configure_service_logging(LOG_DIRECTORY)

from main import main  # noqa: E402


if __name__ == "__main__":
    main()
