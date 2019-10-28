from abc import ABC
from datetime import timedelta
from itertools import chain
from typing import Any, Mapping, MutableSequence, Sequence

from snuba.datasets.dataset import TimeSeriesDataset
from snuba.datasets.dataset_schemas import DatasetSchemas
from snuba.datasets.factory import get_dataset
from snuba.datasets.schemas import Schema, RelationalSource
from snuba.query.extensions import QueryExtension
from snuba.query.parsing import ParsingContext
from snuba.query.project_extension import ProjectExtension, ProjectWithGroupsProcessor
from snuba.query.query import Query
from snuba.query.query_processor import QueryProcessor
from snuba.query.timeseries import TimeSeriesExtension
from snuba.request.request_settings import RequestSettings
from snuba.util import columns_in_expr, is_condition


EVENTS = 'events'
TRANSACTIONS = 'transactions'
TRANSACTIONS_ONLY_COLUMNS = [
    'trace_id',
    'span_id',
    'transaction_name',
    'transaction_hash',
    'transaction_op',
    'start_ts',
    'start_ms',
    'finish__ts',
    'finish_ms',
    'duration',
]

EVENTS_ONLY_COLUMNS = [
    'group_id',
    'transaction',
]


# Based on query.get_all_referenced_columns()
def get_condition_columns(query: Query) -> Sequence[Any]:
    col_exprs: MutableSequence[Any] = []
    # Conditions need flattening as they can be nested as AND/OR
    if query.get_conditions():
        flat_conditions = list(
            chain(*[[c] if is_condition(c) else c for c in query.get_conditions()]))
        col_exprs.extend([c[0] for c in flat_conditions])

    # Return the set of all columns referenced in any expression
    return set(chain(*[columns_in_expr(ex) for ex in col_exprs]))


def detect_dataset(query: Query) -> str:
    """
    Given a query, we attempt to guess whether it is better to fetch data from the
    "events" or "transactions" dataset. This is going to be wrong in some cases.
    """
    # First check for a top level condition that matches either type = transaction
    # type != transaction.
    conditions = query.get_conditions()
    if conditions:
        for idx, condition in enumerate(conditions):
            if is_condition(condition):
                if tuple(condition) == ('type', '!=', 'transaction'):
                    return EVENTS
                elif tuple(condition) == ('type', '=', 'transaction'):
                    return TRANSACTIONS

    # If there is a condition that references a transactions only field, just switch
    # to the transactions dataset
    if [col for col in TRANSACTIONS_ONLY_COLUMNS if col in get_condition_columns(query)]:
        return TRANSACTIONS

    # Use events by default
    return EVENTS


class AllEventsProcessor(QueryProcessor):
    def process_query(self, query: Query, request_settings: RequestSettings) -> None:
        """
        Switches the data source from the default (Events) to the transactions
        table if a transaction specific column is detected.
        Sets all the other columns to none

        """
        detected_dataset = detect_dataset(query)

        source = get_dataset(detected_dataset) \
            .get_dataset_schemas() \
            .get_read_schema() \
            .get_data_source()
        query.set_data_source(source)

        if detected_dataset == TRANSACTIONS:
            with_columns = [
                ("'transaction'", 'type'),
                ('finish_ts', 'timestamp'),
                ('user_name', 'username'),
                ('user_email', 'email'),
            ] + [('NULL', col) for col in EVENTS_ONLY_COLUMNS]

        else:
            with_columns = [('NULL', col) for col in TRANSACTIONS_ONLY_COLUMNS]

        query.set_with(with_columns)


class AllEventsSchema(Schema, ABC):
    def get_data_source(self) -> RelationalSource:
        """
        This is a placeholder, we switch out the data source in the processor
        depending on the detected dataset
        """
        return get_dataset(EVENTS) \
            .get_dataset_schemas() \
            .get_read_schema() \
            .get_data_source()


class AllEventsDataset(TimeSeriesDataset):
    """
    Experimental dataset for Discover
    that coerces the columns of Events and Transactions into the same format
    and sends a query to either one.

    Currently does this by switching between events and transactions tables
    depending on the conditions in the provided query.
    """
    EVENTS_ALIAS = 'events'
    TRANSACTIONS_ALIAS = 'transactions'

    def __init__(self) -> None:
        super().__init__(
            dataset_schemas=DatasetSchemas(
                read_schema=AllEventsSchema(),
                write_schema=None,
            ),
            time_group_columns={
                'time': 'timestamp',
            },
            time_parse_columns=['timestamp'],
        )

    def get_query_processors(self) -> Sequence[QueryProcessor]:
        return [
            AllEventsProcessor(),
        ]

    def get_extensions(self) -> Mapping[str, QueryExtension]:
        return {
            'project': ProjectExtension(
                processor=ProjectWithGroupsProcessor(project_column='project_id')
            ),
            'timeseries': TimeSeriesExtension(
                default_granularity=3600,
                default_window=timedelta(days=5),
                timestamp_column='timestamp',
            ),
        }

    def column_expr(self, column_name, query: Query, parsing_context: ParsingContext):
        detected_dataset = detect_dataset(query)
        return get_dataset(detected_dataset) \
            .column_expr(column_name, query, parsing_context)
