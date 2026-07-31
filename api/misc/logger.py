import datetime, os
from enum import Enum

class LoggerLevel(int, Enum):
    VERBOSE = 0
    DATABASE = 1
    ENDPOINT = 2

class Logger:
    def __init__(self, module_name: str, logger_level: LoggerLevel = LoggerLevel.VERBOSE) -> None:
        self.name = module_name
        self.level = logger_level
        pass

    def log(self, message):
        if int(os.environ.get("MIN_LOGGER_LEVEL", 0)) > self.level.value: return
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ({self.name}): {message}")

    def fail(self, message):
        self.log(message)
        exit(1)