class TradebotError(Exception):
    """Base class for all application errors.

    ``category`` is the error class (risk_rejected, broker_error, ...); ``code`` is a more
    specific machine readable reason (order_too_large, credentials_missing, ...)."""

    category = "error"

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.category
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": self.category, "code": self.code, "message": self.message, "details": self.details}


class ConfigError(TradebotError):
    category = "config_error"


class SymbolError(TradebotError):
    category = "symbol_error"


class DataError(TradebotError):
    category = "data_error"


class RiskRejected(TradebotError):
    category = "risk_rejected"


class BrokerError(TradebotError):
    category = "broker_error"


class NotFound(TradebotError):
    category = "not_found"
