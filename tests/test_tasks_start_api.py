import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.tasks import start_task as api_start_task
from src.database import Base
from src.models import FerryAccount, SystemUser, Task


class TaskStartApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()
        self.user = SystemUser(username="tester", password_hash="x", role="admin", is_active=True)
        self.account = FerryAccount(phone="13800138000", password_enc="dummy", local_storage_json="{}")
        self.db.add_all([self.user, self.account])
        self.db.commit()
        self.db.refresh(self.account)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _task(self, status="pending"):
        task = Task(
            account_id=self.account.id,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-03",
            ticket_type="旅客",
            trigger_type="poll",
            trigger_value="",
            status=status,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def test_start_api_preserves_scheduler_written_status(self):
        task = self._task()

        def fake_sched_start(task_id: int):
            started = self.db.query(Task).get(task_id)
            started.status = "running"
            self.db.commit()

        with patch("src.scheduler.start_task", fake_sched_start):
            result = api_start_task(task.id, db=self.db, _=self.user)

        self.assertEqual(result["message"], "任务已启动")
        refreshed = self.db.query(Task).get(task.id)
        self.assertEqual(refreshed.status, "running")

    def test_start_api_rejects_waiting_task(self):
        task = self._task(status="waiting")

        with self.assertRaises(HTTPException) as ctx:
            api_start_task(task.id, db=self.db, _=self.user)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_start_api_normalizes_legacy_independent_split_to_waiting_child(self):
        main_task = self._task()
        split_task = Task(
            account_id=self.account.id,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-03",
            ticket_type="旅客",
            trigger_type="poll",
            trigger_value="",
            status="running",
            split_source_task_id=main_task.id,
        )
        self.db.add(split_task)
        self.db.commit()
        self.db.refresh(split_task)

        started_ids = []
        stopped_ids = []

        def fake_sched_start(task_id: int):
            started_ids.append(task_id)
            started = self.db.query(Task).get(task_id)
            started.status = "running"
            self.db.commit()

        def fake_sched_stop(task_id: int):
            stopped_ids.append(task_id)

        with patch("src.scheduler.start_task", fake_sched_start):
            with patch("src.scheduler.stop_task", fake_sched_stop):
                api_start_task(main_task.id, db=self.db, _=self.user)

        self.db.refresh(split_task)
        self.assertEqual(started_ids, [main_task.id])
        self.assertEqual(stopped_ids, [split_task.id])
        self.assertEqual(split_task.parent_task_id, main_task.id)
        self.assertEqual(split_task.status, "waiting")


if __name__ == "__main__":
    unittest.main()
