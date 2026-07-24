from dataclasses import dataclass

@dataclass
class ResponseCodes:
    INTERNAL_SERVER_ERROR = 500
    SUCCESS = 200
    CREATED = 201
    MALFORMED = 400
    LIMITED = 429
    TOO_LARGE = 413
    INSUFFICIENT_STORAGE = 507