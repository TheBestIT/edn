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
    NOT_FOUND = 404
    NOT_IMPLEMENTED = 501
    UNAVAILABLE = 503
    FORBIDDEN = 403
    CONFLICT = 409