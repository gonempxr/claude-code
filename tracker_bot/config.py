import os
from dataclasses import dataclass


@dataclass
class Config:
    telegram_token: str | None
    telegram_user_id: int | None
    discord_token: str | None
    discord_user_id: int | None
    tz: str
    db_path: str
    claude_home: str


def _int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    return int(v) if v else None


def load() -> Config:
    cfg = Config(
        telegram_token=os.environ.get("TELEGRAM_TOKEN", "").strip() or None,
        telegram_user_id=_int("TELEGRAM_USER_ID"),
        discord_token=os.environ.get("DISCORD_TOKEN", "").strip() or None,
        discord_user_id=_int("DISCORD_USER_ID"),
        tz=os.environ.get("TZ_NAME", "UTC"),
        db_path=os.environ.get("DB_PATH", "tracker.db"),
        claude_home=os.environ.get("CLAUDE_HOME", "").strip() or os.path.expanduser("~/.claude"),
    )
    if not (cfg.telegram_token or cfg.discord_token):
        raise SystemExit("Set TELEGRAM_TOKEN and/or DISCORD_TOKEN (see .env.example)")
    # Without an owner id the bot would accept commands from anyone who finds it.
    if cfg.telegram_token and cfg.telegram_user_id is None:
        raise SystemExit("TELEGRAM_TOKEN is set but TELEGRAM_USER_ID is not; refusing to run an open bot")
    if cfg.discord_token and cfg.discord_user_id is None:
        raise SystemExit("DISCORD_TOKEN is set but DISCORD_USER_ID is not; refusing to run an open bot")
    return cfg
