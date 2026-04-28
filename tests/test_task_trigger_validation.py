import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.tasks import create_task
from src.database import Base
from src.models import FerryAccount, SystemUser
from src.schemas import TaskCreate


class TaskTriggerValidationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()
        self.user = SystemUser(username="tester", password_hash="x", role="admin", is_active=True)
        self.db.add(self.user)
        self.db.add(FerryAccount(phone="13800138000", password_enc="dummy"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_schedule_requires_trigger_value(self):
        body = TaskCreate(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-20",
            passenger_ids=[1],
            trigger_type="schedule",
            trigger_value="",
        )

        with self.assertRaises(HTTPException) as ctx:
            create_task(body, db=self.db, user=self.user)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_schedule_requires_iso_datetime_trigger_value(self):
        body = TaskCreate(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-20",
            passenger_ids=[1],
            trigger_type="schedule",
            trigger_value="2026/05/20 06:58",
        )

        with self.assertRaises(HTTPException) as ctx:
            create_task(body, db=self.db, user=self.user)

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
