from tests.base import BaseDatasetTest

from snuba.datasets.factory import get_dataset
from snuba.query.query import Query
from snuba.request.request_settings import RequestSettings


class TestDiscover(BaseDatasetTest):
    def setup_method(self, test_method):
        super().setup_method(
            test_method,
            'discover',
        )
        self.events_source = get_dataset('events') \
            .get_dataset_schemas() \
            .get_read_schema() \
            .get_data_source()
        self.transactions_source = get_dataset('transactions') \
            .get_dataset_schemas() \
            .get_read_schema() \
            .get_data_source()

    def process_query(self, query):
        request_settings = RequestSettings(turbo=False, consistent=False, debug=False)

        for processor in self.dataset.get_query_processors():
            processor.process_query(query, request_settings)

    def test_data_source(self):
        query = Query(
            {
                'conditions': [['type', '=', 'transaction']],
            },
            self.events_source
        )
        self.process_query(query)
        assert query.get_data_source() == self.transactions_source

        query = Query(
            {
                'conditions': [['type', '!=', 'transaction']],
            },
            self.events_source
        )
        self.process_query(query)
        assert query.get_data_source() == self.events_source

        query = Query(
            {
                'conditions': [],
            },
            self.events_source
        )
        self.process_query(query)
        assert query.get_data_source() == self.events_source

        query = Query(
            {
                'conditions': [['duration', '=', 0]],
            },
            self.events_source
        )
        self.process_query(query)
        assert query.get_data_source() == self.transactions_source
