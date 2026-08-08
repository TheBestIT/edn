from typing_extensions import Self
import threading, time

from api.misc.logger import Logger, LoggerLevel
import attrs
from typing import Callable, List

@attrs.define(kw_only=True)
class Event:
    method: Callable
    timer: int
    repeating: bool = True

    _itimer: int = 0
    def tick(self) -> bool:
        self._itimer += 1
        if self._itimer == self.timer:
            self.method()
            self._itimer = 0
            return True
        return False

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
        self.events: List[Event] = []

        self.logger = Logger("init.scheduler", LoggerLevel.ENDPOINT)
        self.logger.log("Initialized Scheduler")

        self.scheduler_thread = threading.Thread(target=self.__tick, daemon=True)
        self.scheduler_thread.start()

    def __del__(self) -> None:
        self.terminate = True
        self.logger.log("Set self.terminate flag to True. Waiting for scheduler_thread to stop execution")
        self.scheduler_thread.join()
        self.events.clear()

    def schedule_event(self, event: Event) -> int:
        if any(e.method == event.method for e in self.events): return -1
        self.logger.log(f"Scheduled new {event=}")
        self.events.append(event)
        return len(self.events)-1

    def __tick(self) -> None:
        while True:
            if self.terminate:
                self.events.clear()
                break

            time.sleep(1)
            self.elapsed += 1

            for event in self.events:
                result = event.tick()
                if result == True and event.repeating == False:
                    self.logger.log(f"EXPIRED: {event=}")
                    self.events.remove(event)
            