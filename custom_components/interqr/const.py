"""Constants for the InterQR integration."""

DOMAIN = "interqr"

# ── API Base URLs ─────────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://www.interqr.com/api"
DEV_BASE_URL = "https://dev.interqr.com/api"

# ── API Endpoint Paths ───────────────────────────────────────────────
ENDPOINT_INIT = "/init"
ENDPOINT_LOGIN = "/login"
ENDPOINT_LOGOUT = "/logout"
ENDPOINT_TWOFA_START = "/twofa/start"
ENDPOINT_TWOFA_VERIFY = "/twofa/verify"
ENDPOINT_USER_DETAILS = "/resource/user/details"
ENDPOINT_UNLOCK = "/locks/{uuid}/unlock"
ENDPOINT_UNLOCK_LONG = "/locks/{uuid}/unlock-long"

# ── Device Registration Info ─────────────────────────────────────────
APP_VERSION = "3.5.8"
DEVICE_MANUFACTURER = "HomeAssistant"
DEVICE_MODEL = "Integration"
DEVICE_PLATFORM = "HomeAssistant"

# ── Config Entry Data Keys ───────────────────────────────────────────
CONF_BASE_URL = "base_url"
CONF_TOKEN = "token"
CONF_DEVICE_UUID = "device_uuid"
CONF_USER_UUID = "user_uuid"
CONF_PHONE = "phone"

# ── Server Choices ───────────────────────────────────────────────────
SERVER_PRODUCTION = "production"
SERVER_DEVELOPMENT = "development"
SERVER_CUSTOM = "custom"

SERVER_URLS = {
    SERVER_PRODUCTION: DEFAULT_BASE_URL,
    SERVER_DEVELOPMENT: DEV_BASE_URL,
}

# ── Coordinator ──────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL = 300  # seconds (5 minutes)

# ── Lock Behaviour ───────────────────────────────────────────────────
RELOCK_DELAY = 5  # seconds – time the lock stays "unlocked" before auto-relocking

# ── Security ─────────────────────────────────────────────────────
API_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1_048_576  # 1 MB
MAX_2FA_ATTEMPTS = 5
PHONE_PATTERN = r"^\+[1-9]\d{6,14}$"  # E.164 format
VERIFICATION_CODE_PATTERN = r"^\d{4,8}$"  # 4-8 digit numeric code
