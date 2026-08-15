import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")


def get_current_datetime_context(now=None):
    # 秒は渡さず、日時を話題に必要な精度だけで短く伝えます。
    timezone_name = os.getenv("AITUBER_TIMEZONE", "Asia/Tokyo").strip()
    if not timezone_name:
        raise RuntimeError("AITUBER_TIMEZONEが空です。")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "AITUBER_TIMEZONEに有効なタイムゾーン名を設定してください。"
            f" value={timezone_name}"
        ) from exc

    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    else:
        current = current.astimezone(timezone)
    weekday = WEEKDAYS_JA[current.weekday()]
    return (
        f"現在: {current:%Y-%m-%d}({weekday}) {current:%H:%M} "
        f"{timezone_name}。日時は関連する場合だけ言及する。"
    )
