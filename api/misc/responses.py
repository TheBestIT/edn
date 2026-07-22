from dataclasses import dataclass

@dataclass
class ResponseCodes:
    INTERNAL_SERVER_ERROR = 500,
    SUCCESS = 200,
    CREATED = 201