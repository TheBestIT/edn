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
        )

        self.logger.log(token_generation.to_dict())
        query = self.collection.insert_one(token_generation)

        if query.acknowledged:
            self.logger.log(f"Inserted new token ('{token_generation.token}') successfully")
            return token_generation

        self.logger.log("Failed to insert new token")
        return None

    def get_APIToken_from_token_string(self, token: str) -> APIToken | None:
        self.logger.log(f"Requested APIToken object from token '{token}'")
        query = self.collection.find_one({"token": token})
        return APIToken().from_dict(query) if query is not None else None

    def get_APIToken_from_META_headers(self, META) -> APIToken | None:
        self.logger.log("Requested APIToken from META Headers")
        authorization_header = META.get('HTTP_AUTHORIZATION')
        if authorization_header is None: return None
        token = authorization_header.replace("Bearer ", "")
        query = self.get_APIToken_from_token_string(token)
        return query
