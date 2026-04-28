import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.orders import get_order_detail, list_orders
from src.database import Base
from src.models import Order, SystemUser


class OrdersApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = TestingSessionLocal()
        self.user = SystemUser(username="tester", password_hash="x", role="admin", is_active=True)
        self.db.add(self.user)
        self.db.add_all([
            Order(
                account_id=1,
                order_id="p-new",
                departure_name="A",
                destination_name="B",
                travel_date="2026-05-09",
                sail_time="10:00:00",
                ship_name="船1",
                ship_type="常规客船",
                passengers_json='["甲"]',
                payment_expire_at="2099/04/28 07:05:44",
                status="pending_payment",
                remote_created_at=datetime(2026, 4, 28, 7, 2, 0),
                order_items_json='[{"passName":"甲","seatClassName":"上舱","seatNumber":"12","realFee":90.0,"clxm":"常规客船","hxlxm":"正常航班","credentialNum":"123","lineName":"A至B","createTime":"2026/04/28 07:00:44"}]',
            ),
            Order(
                account_id=1,
                order_id="p-old",
                departure_name="A",
                destination_name="B",
                travel_date="2026-05-01",
                sail_time="09:00:00",
                ship_name="船2",
                ship_type="常规客船",
                passengers_json='["乙"]',
                payment_expire_at="2099/04/28 07:05:44",
                status="pending_payment",
                remote_created_at=datetime(2026, 4, 28, 7, 1, 0),
                order_items_json='[]',
            ),
            Order(
                account_id=1,
                order_id="paid-newer",
                departure_name="C",
                destination_name="D",
                travel_date="2026-05-10",
                sail_time="11:00:00",
                ship_name="船3",
                ship_type="客滚船",
                passengers_json='["丙"]',
                payment_expire_at="2026/04/28 07:05:44",
                status="paid",
                remote_created_at=datetime(2026, 4, 28, 7, 3, 0),
                order_items_json='[{"passName":"丙","seatClassName":"中舱","seatNumber":"21","realFee":120.0,"clxm":"常规客船","hxlxm":"正常航班","credentialNum":"456","lineName":"C至D","createTime":"2026/04/28 07:03:44"}]',
            ),
            Order(
                account_id=1,
                order_id="cancelled",
                departure_name="E",
                destination_name="F",
                travel_date="2026-05-20",
                sail_time="12:00:00",
                ship_name="船4",
                ship_type="常规客船",
                passengers_json='["丁"]',
                payment_expire_at="",
                status="cancelled",
                remote_created_at=datetime(2026, 4, 28, 7, 4, 0),
                order_items_json='[{"passName":"丁","seatClassName":"下前舱","seatNumber":"31","realFee":80.0,"clxm":"常规客船","hxlxm":"正常航班","credentialNum":"789","lineName":"E至F","createTime":"2026/04/28 07:04:44"}]',
            ),
            Order(
                account_id=2,
                order_id="other-account",
                departure_name="X",
                destination_name="Y",
                travel_date="2026-05-11",
                sail_time="18:00:00",
                ship_name="船5",
                ship_type="常规客船",
                passengers_json='["戊"]',
                payment_expire_at="2099/04/28 07:05:44",
                status="pending_payment",
                remote_created_at=datetime(2026, 4, 28, 7, 5, 0),
                order_items_json='[]',
            ),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_list_orders_hides_cancelled_by_default_and_sorts_pending_first(self):
        rows = list_orders(db=self.db, _=self.user)

        self.assertEqual([row["order_id"] for row in rows], ["other-account", "p-new", "p-old", "paid-newer"])
        self.assertTrue(all(row["status"] != "cancelled" for row in rows))
        self.assertEqual(rows[0]["can_pay"], True)
        self.assertEqual(rows[0]["can_view_detail"], False)
        self.assertEqual(rows[0]["clxm"], "常规客船")
        self.assertEqual(rows[-1]["can_view_detail"], True)

    def test_list_orders_can_filter_all_and_cancelled(self):
        all_rows = list_orders(status_filter="all", db=self.db, _=self.user)
        cancelled_rows = list_orders(status_filter="cancelled", db=self.db, _=self.user)

        self.assertEqual([row["order_id"] for row in all_rows], ["other-account", "p-new", "p-old", "paid-newer", "cancelled"])
        self.assertEqual([row["order_id"] for row in cancelled_rows], ["cancelled"])

    def test_list_orders_can_filter_by_account(self):
        rows = list_orders(account_id=1, db=self.db, _=self.user)

        self.assertEqual([row["order_id"] for row in rows], ["p-new", "p-old", "paid-newer"])
        self.assertTrue(all(row["account_id"] == 1 for row in rows))

    def test_get_order_detail_returns_required_item_fields(self):
        paid = self.db.query(Order).filter_by(order_id="paid-newer").one()

        detail = get_order_detail(paid.id, db=self.db, _=self.user)

        self.assertEqual(detail["order_id"], "paid-newer")
        self.assertEqual(len(detail["order_items"]), 1)
        item = detail["order_items"][0]
        self.assertEqual(
            sorted(item.keys()),
            sorted(["seatClassName", "seatNumber", "realFee", "clxm", "hxlxm", "credentialNum", "passName", "lineName", "createTime"]),
        )
        self.assertEqual(item["seatClassName"], "中舱")


if __name__ == "__main__":
    unittest.main()
