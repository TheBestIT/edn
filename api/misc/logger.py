import datetime

class Logger:
    def __init__(self, module_name: str) -> None:
        self.name = module_name
        pass

    def log(self, message):
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ({self.name}): {message}")

    def fail(self, message):
        self.log(message)
        exit(1)