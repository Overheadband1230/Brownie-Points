import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./brownie.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-random-string")

# Required to sign up — share it with friends you actually want in the economy.
INVITE_CODE = os.environ.get("INVITE_CODE", "brownie-batch")

# Session cookies expire after 30 days of inactivity.
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

DEFAULT_CATEGORIES = ["favor", "chore", "apology", "gift", "bet", "other"]
