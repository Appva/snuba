import pytz
import simplejson as json
from datetime import datetime

from tests.base import BaseDatasetTest
from snuba.clickhouse.native import ClickhousePool
from snuba.consumer import KafkaMessageMetadata
from snuba.datasets.cdc.groupedmessage_processor import GroupedMessageProcessor, GroupedMessageRow


class TestGroupedMessage(BaseDatasetTest):

    def setup_method(self, test_method):
        super(TestGroupedMessage, self).setup_method(
            test_method,
            'groupedmessage',
        )

    BEGIN_MSG = '{"event":"begin","xid":2380836}'
    COMMIT_MSG = '{"event":"commit"}'
    UPDATE_MSG = (
        '{"event":"change","xid":2380866,"kind":"update","schema":"public",'
        '"table":"sentry_groupedmessage","columnnames":["id","logger","level","message",'
        '"view","status","times_seen","last_seen","first_seen","data","score","project_id",'
        '"time_spent_total","time_spent_count","resolved_at","active_at","is_public","platform",'
        '"num_comments","first_release_id","short_id"],"columntypes":["bigint","character varying(64)",'
        '"integer","text","character varying(200)","integer","integer","timestamp with time zone",'
        '"timestamp with time zone","text","integer","bigint","integer","integer",'
        '"timestamp with time zone","timestamp with time zone","boolean","character varying(64)","integer",'
        '"bigint","bigint"],"columnvalues":[74,"",40,'
        '"<module> ZeroDivisionError integer division or modulo by zero client3.py __main__ in <module>",'
        '"__main__ in <module>",0,2,"2019-06-19 06:46:28+00","2019-06-19 06:45:32+00",'
        '"eJyT7tuwzAM3PkV2pzJiO34VRSdmvxAgA5dCtViDAGyJEi0AffrSxrZOlSTjrzj3Z1MrOBekCWHBcQaPj4xhXe72WyDv6YU0ouynnDGpMxzrEJSSzCrC+p7Vz8sgNhAvhdOZ/pKOKHd0PC5C9yqtjuPddcPQ9n0w8hPiLRHsWvZGsWD/91xI'
        'ya2IFxz7vJWfTUlHHnwSCEBUkbTZrxCCcOf2baY/XTU1VJm9cjHL4JriHPYvOnliyP0Jt2q4SpLkz7v6owW9E9rEOvl0PawczxcvkLIWppxg==",'
        '1560926969,2,0,0,null,"2019-06-19 06:45:32+00",false,"python",0,null,20],'
        '"oldkeys":{"keynames":["id"],"keytypes":["bigint"],"keyvalues":[74]}}'
    )

    DELETE_MSG = (
        '{"event":"change","xid":2380866,"kind":"delete","schema":"public",'
        '"table": "sentry_groupedmessage",'
        '"oldkeys":{"keynames":["id", "project_id"],"keytypes":["bigint", "bigint"],"keyvalues":[74, 2]}}'
    )

    INSERT_MSG = (
        '{"event":"change","xid":2380866,"kind":"insert","schema":"public",'
        '"table":"sentry_groupedmessage","columnnames":["id","logger","level","message",'
        '"view","status","times_seen","last_seen","first_seen","data","score","project_id",'
        '"time_spent_total","time_spent_count","resolved_at","active_at","is_public","platform",'
        '"num_comments","first_release_id","short_id"],"columntypes":["bigint","character varying(64)",'
        '"integer","text","character varying(200)","integer","integer","timestamp with time zone",'
        '"timestamp with time zone","text","integer","bigint","integer","integer",'
        '"timestamp with time zone","timestamp with time zone","boolean","character varying(64)","integer",'
        '"bigint","bigint"],"columnvalues":[74,"",40,'
        '"<module> ZeroDivisionError integer division or modulo by zero client3.py __main__ in <module>",'
        '"__main__ in <module>",0,2,"2019-06-19 06:46:28+00","2019-06-19 06:45:32+00",'
        '"eJyT7tuwzAM3PkV2pzJiO34VRSdmvxAgA5dCtViDAGyJEi0AffrSxrZOlSTjrzj3Z1MrOBekCWHBcQaPj4xhXe72WyDv6YU0ouynnDGpMxzrEJSSzCrC+p7Vz8sgNhAvhdOZ/pKOKHd0PC5C9yqtjuPddcPQ9n0w8hPiLRHsWvZGsWD/91xI'
        'ya2IFxz7vJWfTUlHHnwSCEBUkbTZrxCCcOf2baY/XTU1VJm9cjHL4JriHPYvOnliyP0Jt2q4SpLkz7v6owW9E9rEOvl0PawczxcvkLIWppxg==",'
        '1560926969,2,0,0,null,"2019-06-19 06:45:32+00",false,"python",0,null,20]'
        '}'
    )

    PROCESSED = {
        'offset': 42,
        'project_id': 2,
        'id': 74,
        'record_deleted': 0,
        'status': 0,
        'last_seen': datetime(2019, 6, 19, 6, 46, 28, tzinfo=pytz.UTC),
        'first_seen': datetime(2019, 6, 19, 6, 45, 32, tzinfo=pytz.UTC),
        'active_at': datetime(2019, 6, 19, 6, 45, 32, tzinfo=pytz.UTC),
        'first_release_id': None,
    }

    DELETED = {
        'offset': 42,
        'project_id': 2,
        'id': 74,
        'record_deleted': 1,
        'status': None,
        'last_seen': None,
        'first_seen': None,
        'active_at': None,
        'first_release_id': None,
    }

    def test_messages(self):
        processor = GroupedMessageProcessor('sentry_groupedmessage')

        metadata = KafkaMessageMetadata(
            offset=42,
            partition=0,
        )

        begin_msg = json.loads(self.BEGIN_MSG)
        ret = processor.process_message(begin_msg, metadata)
        assert ret is None

        commit_msg = json.loads(self.COMMIT_MSG)
        ret = processor.process_message(commit_msg, metadata)
        assert ret is None

        insert_msg = json.loads(self.INSERT_MSG)
        ret = processor.process_message(insert_msg, metadata)
        assert ret.data == [self.PROCESSED]
        self.write_processed_records(ret.data)
        cp = ClickhousePool()
        ret = cp.execute("SELECT * FROM test_groupedmessage_local;")
        assert ret[0] == (
            42,  # offset
            0,  # deleted
            2,  # project_id
            74,  # id
            0,  # status
            datetime(2019, 6, 19, 6, 46, 28),
            datetime(2019, 6, 19, 6, 45, 32),
            datetime(2019, 6, 19, 6, 45, 32),
            None,
        )

        update_msg = json.loads(self.UPDATE_MSG)
        ret = processor.process_message(update_msg, metadata)
        assert ret.data == [self.PROCESSED]

        delete_msg = json.loads(self.DELETE_MSG)
        ret = processor.process_message(delete_msg, metadata)
        assert ret.data == [self.DELETED]

    def test_bulk_load(self):
        row = GroupedMessageRow.from_bulk({
            'project_id': '2',
            'id': '10',
            'status': '0',
            'last_seen': '2019-06-28 17:57:32+00',
            'first_seen': '2019-06-28 06:40:17+00',
            'active_at': '2019-06-28 06:40:17+00',
            'first_release_id': '26',
        })
        self.write_processed_records(row.to_clickhouse())
        cp = ClickhousePool()
        ret = cp.execute("SELECT * FROM test_groupedmessage_local;")
        assert ret[0] == (
            0,  # offset
            0,  # deleted
            2,  # project_id
            10,  # id
            0,  # status
            datetime(2019, 6, 28, 17, 57, 32),
            datetime(2019, 6, 28, 6, 40, 17),
            datetime(2019, 6, 28, 6, 40, 17),
            26,
        )
