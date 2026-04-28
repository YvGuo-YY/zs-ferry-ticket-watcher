import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models import FerryAccount, SystemUser, Task
from src.scheduler import build_no_trip_log_message, decide_start_action, _trigger_split_tasks


class DecideStartActionTests(unittest.TestCase):
    def test_poll_before_sale_day_schedules_next_day_check(self):
        now = datetime(2026, 4, 28, 10, 0, 0)

        action = decide_start_action("poll", "", "2026-05-20", now)

        self.assertEqual(action.action, "schedule_next_day_0640")

    def test_poll_before_sale_time_on_sale_day_schedules_today(self):
        now = datetime(2026, 4, 28, 6, 30, 0)

        action = decide_start_action("poll", "", "2026-05-04", now)

        self.assertEqual(action.action, "schedule_today_0640")

    def test_poll_after_sale_time_polls_immediately(self):
        now = datetime(2026, 4, 28, 6, 50, 0)

        action = decide_start_action("poll", "", "2026-05-04", now)

        self.assertEqual(action.action, "poll_now")

    def test_schedule_future_time_delays_until_trigger_value(self):
        now = datetime(2026, 4, 28, 10, 0, 0)
        trigger_value = "2026-04-28T10:05:00"

        action = decide_start_action("schedule", trigger_value, "2026-05-04", now)

        self.assertEqual(action.action, "delay_until_trigger_value")
        self.assertEqual(action.run_at, datetime(2026, 4, 28, 10, 5, 0))

    def test_schedule_after_trigger_before_sale_day_uses_existing_window_rules(self):
        now = datetime(2026, 4, 28, 10, 6, 0)
        trigger_value = "2026-04-28T10:05:00"

        action = decide_start_action("schedule", trigger_value, "2026-05-20", now)

        self.assertEqual(action.action, "schedule_next_day_0640")

    def test_schedule_after_trigger_in_sale_window_polls_immediately(self):
        now = datetime(2026, 4, 28, 10, 6, 0)
        trigger_value = "2026-04-28T10:05:00"

        action = decide_start_action("schedule", trigger_value, "2026-05-04", now)

        self.assertEqual(action.action, "poll_now")


class NoTripLogMessageTests(unittest.TestCase):
    def test_build_no_trip_log_message_includes_trip_and_seat_info(self):
        trips = [
            {
                "sailTime": "15:40",
                "shipName": "舟桥7轮",
                "lineName": "沈家湾至泗礁",
                "onSale": 1,
                "seatClasses": [
                    {"className": "上舱", "pubCurrentCount": 0},
                    {"className": "下舱", "pubCurrentCount": 3},
                ],
                "driverSeatClass": [
                    {"className": "小车", "pubCurrentCount": 0},
                ],
            }
        ]

        message = build_no_trip_log_message(
            trips,
            preferred_seats=["上舱"],
            sail_time_from="15:00",
            sail_time_to="16:00",
            require_vehicle=True,
        )

        self.assertIn("15:40", message)
        self.assertIn("舟桥7轮", message)
        self.assertIn("客舱:上舱×0、下舱×3", message)
        self.assertIn("车位:小车×0", message)
        self.assertIn("舱位偏好:上舱", message)

    def test_build_no_trip_log_message_reports_empty_time_window(self):
        trips = [{"sailTime": "15:40", "shipName": "舟桥7轮", "seatClasses": []}]

        message = build_no_trip_log_message(
            trips,
            preferred_seats=[],
            sail_time_from="18:00",
            sail_time_to="19:00",
            require_vehicle=False,
        )

        self.assertIn("18:00~19:00", message)
        self.assertIn("无班次", message)


class TriggerSplitTasksTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()
        self.db.add_all([
            SystemUser(username="tester", password_hash="x", role="admin", is_active=True),
            FerryAccount(phone="13800138000", password_enc="dummy"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_trigger_split_tasks_triggers_waiting_child(self):
        main = Task(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-03",
            ticket_type="小客车及随车人员",
            vehicle_id=2,
            driver_passenger_id=5,
            passenger_ids="[5]",
            trigger_type="poll",
            trigger_value="",
            status="booked",
        )
        split = Task(
            account_id=1,
            departure_num=1,
            departure_name="A",
            destination_num=2,
            destination_name="B",
            travel_date="2026-05-03",
            ticket_type="旅客",
            passenger_ids="[1,2,4]",
            trigger_type="poll",
            trigger_value="",
            status="waiting",
            parent_task_id=1,
            split_source_task_id=1,
        )
        self.db.add_all([main, split])
        self.db.commit()
        self.db.refresh(main)
        split.parent_task_id = main.id
        split.split_source_task_id = main.id
        self.db.commit()
        self.db.refresh(split)

        started = []
        trip = {"lineName": "测试航线", "sailTime": "17:30", "shipName": "测试船"}

        with patch("src.scheduler.start_task", side_effect=lambda task_id, bypass_schedule=False: started.append(task_id)):
            _trigger_split_tasks(self.db, main.id, trip, lambda level, msg: None)

        self.db.refresh(split)
        self.assertEqual(started, [split.id])
        self.assertEqual(split.status, "pending")
        self.assertIn("测试航线", split.linked_trip_json)


if __name__ == "__main__":
    unittest.main()
