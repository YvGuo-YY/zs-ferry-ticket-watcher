import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.tasks import create_task, update_task
from src.database import Base
from src.models import FerryAccount, SystemUser, Task
from src.schemas import TaskCreate, TaskUpdate


class SplitTaskTests(unittest.TestCase):
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

    def _task(self, task_id: int) -> Task:
        return self.db.query(Task).get(task_id)

    def _tasks(self) -> list[Task]:
        return self.db.query(Task).order_by(Task.id.asc()).all()

    def test_vehicle_task_without_manual_split_keeps_all_passengers(self):
        body = TaskCreate(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-20",
            ticket_type="小客车及随车人员",
            vehicle_id=9,
            driver_passenger_id=11,
            passenger_ids=[11, 12, 13, 14],
            linked_passenger_task={"enabled": False, "same_trip": True, "seat_classes": []},
        )

        result = create_task(body, db=self.db, user=self.user)

        tasks = self._tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(result.id, tasks[0].id)
        self.assertEqual(json.loads(tasks[0].passenger_ids), [11, 12, 13, 14])

    def test_vehicle_task_same_trip_split_creates_waiting_child(self):
        body = TaskCreate(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-20",
            ticket_type="小客车及随车人员",
            vehicle_id=9,
            driver_passenger_id=11,
            passenger_ids=[11, 12, 13, 14],
            linked_passenger_task={"enabled": True, "same_trip": True, "seat_classes": ["上舱"]},
        )

        result = create_task(body, db=self.db, user=self.user)

        tasks = self._tasks()
        self.assertEqual(len(tasks), 2)
        main = self._task(result.id)
        child = next(t for t in tasks if t.id != main.id)
        self.assertEqual(json.loads(main.passenger_ids), [11])
        self.assertEqual(child.parent_task_id, main.id)
        self.assertEqual(child.split_source_task_id, main.id)
        self.assertEqual(child.status, "waiting")
        self.assertEqual(json.loads(child.passenger_ids), [12, 13, 14])

    def test_vehicle_task_split_always_creates_waiting_child(self):
        body = TaskCreate(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-20",
            ticket_type="小客车及随车人员",
            vehicle_id=9,
            driver_passenger_id=11,
            passenger_ids=[11, 12, 13, 14],
            linked_passenger_task={"enabled": True, "same_trip": False, "seat_classes": ["上舱"]},
        )

        result = create_task(body, db=self.db, user=self.user)

        tasks = self._tasks()
        self.assertEqual(len(tasks), 2)
        main = self._task(result.id)
        child = next(t for t in tasks if t.id != main.id)
        self.assertEqual(json.loads(main.passenger_ids), [11])
        self.assertEqual(child.parent_task_id, main.id)
        self.assertEqual(child.split_source_task_id, main.id)
        self.assertEqual(child.status, "waiting")
        self.assertEqual(child.ticket_type, "旅客")
        self.assertEqual(json.loads(child.passenger_ids), [12, 13, 14])

    def test_update_main_task_keeps_child_mode_even_if_same_trip_false(self):
        created = create_task(
            TaskCreate(
                account_id=1,
                departure_num=1,
                departure_name="A",
                destination_num=2,
                destination_name="B",
                travel_date="2026-05-20",
                ticket_type="小客车及随车人员",
                vehicle_id=9,
                driver_passenger_id=11,
                passenger_ids=[11, 12, 13, 14],
                linked_passenger_task={"enabled": True, "same_trip": True, "seat_classes": ["上舱"]},
            ),
            db=self.db,
            user=self.user,
        )

        updated = update_task(
            created.id,
            TaskUpdate(
                passenger_ids=[11, 12, 13, 14],
                driver_passenger_id=11,
                linked_passenger_task={"enabled": True, "same_trip": False, "seat_classes": ["中舱"]},
            ),
            db=self.db,
            _=self.user,
        )

        tasks = self._tasks()
        self.assertEqual(len(tasks), 2)
        main = self._task(updated.id)
        child = next(t for t in tasks if t.id != main.id)
        self.assertEqual(json.loads(main.passenger_ids), [11])
        self.assertEqual(child.parent_task_id, main.id)
        self.assertEqual(child.split_source_task_id, main.id)
        self.assertEqual(child.status, "waiting")
        self.assertEqual(child.seat_class, "中舱")

    def test_update_main_task_disabling_split_removes_existing_split_task(self):
        created = create_task(
            TaskCreate(
                account_id=1,
                departure_num=1,
                departure_name="A",
                destination_num=2,
                destination_name="B",
                travel_date="2026-05-20",
                ticket_type="小客车及随车人员",
                vehicle_id=9,
                driver_passenger_id=11,
                passenger_ids=[11, 12, 13, 14],
                linked_passenger_task={"enabled": True, "same_trip": False, "seat_classes": ["上舱"]},
            ),
            db=self.db,
            user=self.user,
        )

        updated = update_task(
            created.id,
            TaskUpdate(
                passenger_ids=[11, 12, 13, 14],
                driver_passenger_id=11,
                linked_passenger_task={"enabled": False, "same_trip": True, "seat_classes": []},
            ),
            db=self.db,
            _=self.user,
        )

        tasks = self._tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(updated.id, tasks[0].id)
        self.assertEqual(json.loads(tasks[0].passenger_ids), [11, 12, 13, 14])


if __name__ == "__main__":
    unittest.main()
