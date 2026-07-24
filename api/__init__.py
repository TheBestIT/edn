from typing_extensions import Self
import threading, time

from api.misc.logger import Logger
from api.db.filesystem import Filesystem

class Scheduler:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance == None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.elapsed = 0
        self.terminate = False

        self.logger = Logger("init.scheduler")
        self.logger.log("Initialized Scheduler")

        self.scheduler_thread = threading.Thread(target=self.__tick, daemon=True)
        self.scheduler_thread.start()

    def __del__(self) -> None:
        self.terminate = True
        self.logger.log("Set self.terminate flag to True. Waiting for scheduler_thread to stop execution")
        self.scheduler_thread.join()

    def __tick(self) -> None:
        while True:
            if self.terminate: break

            time.sleep(1)
            self.elapsed += 1

            if self.elapsed % 10 == 0:
                Filesystem().check_nodes()
            