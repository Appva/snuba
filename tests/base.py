import calendar
from hashlib import md5
from datetime import datetime, timedelta
import uuid

from snuba import settings
from snuba.datasets.factory import enforce_table_writer, get_dataset
from snuba.clickhouse.native import ClickhousePool
from snuba.redis import redis_client


def wrap_raw_event(event):
    "Wrap a raw event like the Sentry codebase does before sending to Kafka."

    unique = "%s:%s" % (str(event['project']), event['id'])
    primary_hash = md5(unique.encode('utf-8')).hexdigest()

    return {
        'event_id': event['id'],
        'group_id': int(primary_hash[:16], 16),
        'primary_hash': primary_hash,
        'project_id': event['project'],
        'message': event['message'],
        'platform': event['platform'],
        'datetime': event['datetime'],
        'data': event
    }


def get_event():
    from tests.fixtures import raw_event
    timestamp = datetime.utcnow()
    raw_event['datetime'] = (timestamp - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raw_event['received'] = int(calendar.timegm((timestamp - timedelta(seconds=1)).timetuple()))
    return wrap_raw_event(raw_event)


class BaseTest(object):
    def setup_method(self, test_method, dataset_name=None):
        assert settings.TESTING, "settings.TESTING is False, try `SNUBA_SETTINGS=test` or `make test`"

        self.database = 'default'
        self.dataset_name = dataset_name

        if self.dataset_name:
            self.dataset = get_dataset(self.dataset_name)
            self.clickhouse = ClickhousePool()

            for statement in self.dataset.get_dataset_schemas().get_drop_statements():
                self.clickhouse.execute(statement)

            for statement in self.dataset.get_dataset_schemas().get_create_statements():
                self.clickhouse.execute(statement)

        redis_client.flushdb()

    def teardown_method(self, test_method):
        if self.dataset_name:
            for statement in self.dataset.get_dataset_schemas().get_drop_statements():
                self.clickhouse.execute(statement)

        redis_client.flushdb()


class BaseDatasetTest(BaseTest):
    def write_processed_records(self, records):
        if not isinstance(records, (list, tuple)):
            records = [records]

        rows = []
        for event in records:
            rows.append(event)

        return self.write_rows(rows)

    def write_rows(self, rows):
        if not isinstance(rows, (list, tuple)):
            rows = [rows]
        enforce_table_writer(self.dataset).get_writer().write(rows)


class BaseEventsTest(BaseDatasetTest):
    def setup_method(self, test_method, dataset_name='events'):
        super(BaseEventsTest, self).setup_method(test_method, dataset_name)
        self.table = enforce_table_writer(self.dataset).get_schema().get_table_name()
        self.event = get_event()

    def create_event_for_date(self, dt, retention_days=settings.DEFAULT_RETENTION_DAYS):
        event = {
            'event_id': uuid.uuid4().hex,
            'project_id': 1,
            'group_id': 1,
            'deleted': 0,
        }
        event['timestamp'] = dt
        event['retention_days'] = retention_days
        return event

    def write_raw_events(self, events):
        if not isinstance(events, (list, tuple)):
            events = [events]

        out = []
        for event in events:
            if 'primary_hash' not in event:
                event = wrap_raw_event(event)
            processed = enforce_table_writer(self.dataset) \
                .get_stream_loader() \
                .get_processor() \
                .process_message(event)
            out.extend(processed.data)

        return self.write_processed_records(out)

    def write_processed_events(self, events):
        if not isinstance(events, (list, tuple)):
            events = [events]

        rows = []
        for event in events:
            rows.append(event)

        return self.write_rows(rows)

    def write_rows(self, rows):
        if not isinstance(rows, (list, tuple)):
            rows = [rows]

        enforce_table_writer(self.dataset).get_writer().write(rows)


class BaseApiTest(BaseEventsTest):
    def setup_method(self, test_method, dataset_name='events'):
        super().setup_method(test_method, dataset_name)
        from snuba.views import application
        assert application.testing is True
        application.config['PROPAGATE_EXCEPTIONS'] = False
        self.app = application.test_client()
