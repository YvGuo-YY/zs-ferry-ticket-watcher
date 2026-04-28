import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.crawler.api_backend import ApiBackend
from src.database import Base
from src.models import FerryAccount, Order


class FakeApiBackend(ApiBackend):
    def __init__(self, list_payload, detail_payloads):
        super().__init__()
        self.list_payload = list_payload
        self.detail_payloads = detail_payloads
        self.calls = []
        self.ensure_calls = 0

    def ensure_logged_in(self, account, db):
        self.ensure_calls += 1
        return "ok"

    def _get(self, path: str, token, user_id, account=None, **kwargs):
        self.calls.append((path, kwargs))
        if path == "/user/order/list":
            return self.list_payload
        if path == "/user/order/detail":
            order_id = str((kwargs.get("params") or {}).get("orderId") or "")
            return self.detail_payloads[order_id]
        raise AssertionError(f"unexpected path: {path}")


class OrderSyncTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()
        self.account = FerryAccount(phone="13800138000", password_enc="dummy", local_storage_json='{"userId":867892,"token":"t"}')
        self.db.add(self.account)
        self.db.commit()
        self.db.refresh(self.account)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_sync_orders_creates_local_rows_from_remote_list_and_detail(self):
        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137340",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-04",
                        "sailTime": "15:50:00",
                        "shipName": "嵊翔16轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "expireTime": 1777331144681,
                    }
                ],
            },
            detail_payloads={
                "217773308444137340": {
                    "code": 200,
                    "data": {
                        "orderId": "217773308444137340",
                        "sailDate": "2026-05-04",
                        "sailTime": "15:50:00",
                        "shipName": "嵊翔16轮",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "clxm": "常规客船",
                        "createTime": "2026/04/28 07:00:44",
                        "payTime": "2026/04/28 07:03:05",
                        "expireTime": "2026/04/28 07:05:44",
                        "orderItemList": [
                            {"passName": "李广帅"},
                            {"passName": "周某某"},
                        ],
                    },
                }
            },
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["errors"], [])
        order = self.db.query(Order).one()
        self.assertEqual(order.account_id, self.account.id)
        self.assertEqual(order.order_id, "217773308444137340")
        self.assertEqual(order.departure_name, "枸杞")
        self.assertEqual(order.destination_name, "泗礁")
        self.assertEqual(order.travel_date, "2026-05-04")
        self.assertEqual(order.sail_time, "15:50:00")
        self.assertEqual(order.ship_name, "嵊翔16轮")
        self.assertEqual(order.ship_type, "常规客船")
        self.assertEqual(order.payment_expire_at, "2026/04/28 07:05:44")
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.passengers_json, '["李广帅", "周某某"]')
        self.assertEqual(order.remote_created_at.strftime("%Y-%m-%d %H:%M:%S"), "2026-04-28 07:00:44")
        self.assertEqual(order.order_items_json, '[{"passName": "李广帅"}, {"passName": "周某某"}]')

    def test_sync_orders_updates_existing_row_without_overwriting_task_link(self):
        existing = Order(
            task_id=9,
            account_id=self.account.id,
            order_id="217773308444137340",
            departure_name="旧出发",
            destination_name="旧到达",
            travel_date="2026-05-01",
            sail_time="08:00:00",
            ship_name="旧船名",
            passengers_json='["旧旅客"]',
            payment_expire_at="",
            status="pending_payment",
        )
        self.db.add(existing)
        self.db.commit()

        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137340",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-04",
                        "sailTime": "15:50:00",
                        "shipName": "嵊翔16轮",
                        "clxm": "常规客船",
                        "payTime": None,
                        "expireTime": 1777331144681,
                    }
                ],
            },
            detail_payloads={
                "217773308444137340": {
                    "code": 200,
                    "data": {
                        "orderId": "217773308444137340",
                        "sailDate": "2026-05-04",
                        "sailTime": "15:50:00",
                        "shipName": "嵊翔16轮",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "clxm": "常规客船",
                        "createTime": "2026/04/28 07:00:44",
                        "payTime": None,
                        "cancelTime": None,
                        "expireTime": "2026/04/28 07:05:44",
                        "orderItemList": [{"passName": "李广帅"}],
                    },
                }
            },
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        order = self.db.query(Order).one()
        self.assertEqual(order.task_id, 9)
        self.assertEqual(order.departure_name, "枸杞")
        self.assertEqual(order.destination_name, "泗礁")
        self.assertEqual(order.ship_type, "常规客船")
        self.assertEqual(order.status, "pending_payment")
        self.assertEqual(order.passengers_json, '["李广帅"]')
        self.assertEqual(order.order_items_json, '[{"passName": "李广帅"}]')

    def test_sync_orders_marks_cancelled_when_remote_has_cancel_time(self):
        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137341",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-05",
                        "sailTime": "09:30:00",
                        "shipName": "嵊翔17轮",
                        "clxm": "客滚船",
                        "cancelTime": "2026/04/28 08:00:00",
                    }
                ],
            },
            detail_payloads={
                "217773308444137341": {
                    "code": 200,
                    "data": {
                        "orderId": "217773308444137341",
                        "sailDate": "2026-05-05",
                        "sailTime": "09:30:00",
                        "shipName": "嵊翔17轮",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "clxm": "客滚船",
                        "createTime": "2026/04/28 07:01:44",
                        "cancelTime": "2026/04/28 08:00:00",
                        "orderItemList": [{"passName": "李广帅"}],
                    },
                }
            },
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["created"], 1)
        order = self.db.query(Order).filter_by(order_id="217773308444137341").one()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.ship_type, "客滚船")

    def test_sync_orders_reuses_existing_detail_without_refetching_each_order(self):
        existing = Order(
            account_id=self.account.id,
            order_id="217773308444137342",
            departure_name="旧出发",
            destination_name="旧到达",
            travel_date="2026-05-06",
            sail_time="13:30:00",
            ship_name="旧船",
            passengers_json='["甲"]',
            order_items_json='[{"passName":"甲","seatClassName":"上舱"}]',
            payment_expire_at="2026/04/28 07:05:44",
            status="paid",
        )
        self.db.add(existing)
        self.db.commit()

        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137342",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-06",
                        "sailTime": "13:30:00",
                        "shipName": "嵊翔18轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "createTime": "2026/04/28 07:00:44",
                        "orderItemList": [{"id": 1, "itemState": 2}],
                    }
                ],
            },
            detail_payloads={},
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(backend.ensure_calls, 1)
        self.assertEqual(len([c for c in backend.calls if c[0] == "/user/order/list"]), 1)
        self.assertEqual(len([c for c in backend.calls if c[0] == "/user/order/detail"]), 0)
        order = self.db.query(Order).filter_by(order_id="217773308444137342").one()
        self.assertEqual(order.passengers_json, '["甲"]')
        self.assertEqual(order.order_items_json, '[{"passName":"甲","seatClassName":"上舱"}]')

    def test_sync_orders_refetches_detail_when_existing_order_items_are_compact_list_data(self):
        existing = Order(
            account_id=self.account.id,
            order_id="217773308444137344",
            departure_name="旧出发",
            destination_name="旧到达",
            travel_date="2026-05-08",
            sail_time="18:10:00",
            ship_name="旧船",
            passengers_json='[]',
            order_items_json='[{"id":17753389,"itemState":2}]',
            payment_expire_at="2026/04/28 07:05:44",
            status="paid",
        )
        self.db.add(existing)
        self.db.commit()

        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137344",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-08",
                        "sailTime": "18:10:00",
                        "shipName": "嵊翔20轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "createTime": "2026/04/28 07:00:44",
                        "orderItemList": [{"id": 17753389, "itemState": 2}],
                    }
                ],
            },
            detail_payloads={
                "217773308444137344": {
                    "code": 200,
                    "data": {
                        "orderId": "217773308444137344",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-08",
                        "sailTime": "18:10:00",
                        "shipName": "嵊翔20轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "createTime": "2026/04/28 07:00:44",
                        "orderItemList": [
                            {
                                "id": 17753389,
                                "itemState": 2,
                                "passName": "李广帅",
                                "seatClassName": "上舱",
                                "credentialNum": "320323200003277016",
                            }
                        ],
                    },
                }
            },
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["errors"], [])
        self.assertEqual(len([c for c in backend.calls if c[0] == "/user/order/detail"]), 1)
        order = self.db.query(Order).filter_by(order_id="217773308444137344").one()
        self.assertEqual(order.passengers_json, '["李广帅"]')
        self.assertIn('"seatClassName": "上舱"', order.order_items_json)

    def test_sync_orders_accepts_integer_create_time_from_list(self):
        backend = FakeApiBackend(
            list_payload={
                "code": 200,
                "data": [
                    {
                        "orderId": "217773308444137343",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-07",
                        "sailTime": "16:00:00",
                        "shipName": "嵊翔19轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "createTime": 1777330844000,
                    }
                ],
            },
            detail_payloads={
                "217773308444137343": {
                    "code": 200,
                    "data": {
                        "orderId": "217773308444137343",
                        "startPortName": "枸杞",
                        "endPortName": "泗礁",
                        "sailDate": "2026-05-07",
                        "sailTime": "16:00:00",
                        "shipName": "嵊翔19轮",
                        "clxm": "常规客船",
                        "payTime": "2026/04/28 07:03:05",
                        "orderItemList": [{"passName": "测试旅客"}],
                    },
                }
            },
        )

        result = backend.sync_orders(self.account, self.db)

        self.assertEqual(result["errors"], [])
        order = self.db.query(Order).filter_by(order_id="217773308444137343").one()
        self.assertIsNotNone(order.remote_created_at)


if __name__ == "__main__":
    unittest.main()
