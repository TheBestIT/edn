from typing_extensions import Self

import pymongo, datetime, uuid
from api.db.database import Database, Collection
from api.misc.logger import Logger

from api.db.models import APIToken

class Auth:
    instance = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        self.logger = Logger("database.auth")
        self.collection = Database().get_collection("auth")
        self.logger.log("Got reference to 'auth' Collection")

    def generate_api_token(self) -> APIToken | None:
        token_generation = APIToken(
            token=str(uuid.uuid4()),
            created_at=datetime.datetime.now().timestamp(),
            last_used=0
        )

        self.logger.log(token_generation.to_dict())
        query = self.collection.insert_one(token_generation)

        if query.acknowledged:
            self.logger.log(f"Inserted new token ('{token_generation.token}') successfully")
            return token_generation

        self.logger.log("Failed to insert new token")
        return None