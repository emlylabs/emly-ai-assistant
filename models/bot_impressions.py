import uuid
from datetime import datetime
from typing import List, Optional

import peewee as pw
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB
from models.bots import Bot


class BotImpressions(pw.Model):
    id = pw.CharField(max_length=255, primary_key=True)
    bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="impressions")
    impression_type = pw.CharField(null=False, max_length=255)
    created_on = pw.DateTimeField()
    updated_on = pw.DateTimeField()

    class Meta:
        database = DB
        table_name = "bot_impressions"


class BotImpressionsModel(BaseModel):
    id: str
    bot_id: str
    impression_type: str
    created_on: datetime
    updated_on: datetime


def _row_to_model(row: BotImpressions) -> BotImpressionsModel:
    data = model_to_dict(row, recurse=False)
    return BotImpressionsModel(
        id=data["id"],
        bot_id=data["bot"],
        impression_type=data["impression_type"],
        created_on=data["created_on"],
        updated_on=data["updated_on"],
    )


class BotImpressionsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([BotImpressions])

    def insert_impression(self, bot_id: str, impression_type: str) -> Optional[BotImpressionsModel]:
        now = datetime.now()
        row = BotImpressions.create(
            id=str(uuid.uuid4()),
            bot=bot_id,
            impression_type=impression_type,
            created_on=now,
            updated_on=now,
        )
        return _row_to_model(row)

    def get_impressions(
        self,
        bot_id: str,
        from_timestamp: Optional[datetime] = None,
        to_timestamp: Optional[datetime] = None,
    ) -> List[BotImpressionsModel]:
        try:
            from_date = from_timestamp if from_timestamp else datetime.min
            to_date = to_timestamp if to_timestamp else datetime.max
            query = BotImpressions.select().where(
                (BotImpressions.bot == bot_id)
                & (BotImpressions.created_on >= from_date)
                & (BotImpressions.created_on <= to_date)
            )
            return [_row_to_model(r) for r in query]
        except pw.DoesNotExist:
            return []
        except Exception as e:
            raise Exception(f"An error occurred during the update: {str(e)}")


Bot_Impressions = BotImpressionsTable(DB)
