import logging
import click
import json

from confluent_kafka import Producer

from snuba import settings
from snuba.datasets.cdc import CdcDataset
from snuba.datasets.factory import get_dataset, DATASET_NAMES
from snuba.snapshots.postgres_snapshot import PostgresSnapshot
from snuba.stateful_consumer.control_protocol import TransactionData, SnapshotLoaded


@click.command()
@click.option('--control-topic', default=None,
              help='Topic to produce messages onto.')
@click.option('--bootstrap-server', default=None, multiple=True,
              help='Kafka bootstrap server to use.')
@click.option('--dataset', type=click.Choice(DATASET_NAMES),
              help='The dataset to bulk load')
@click.option('--source',
              help='Source of the dump. Depending on the dataset it may have different meaning.')
@click.option('--log-level', default=settings.LOG_LEVEL, help='Logging level to use.')
def confirm_load(control_topic, bootstrap_server, dataset, source, log_level):
    """
    Confirms the snapshot has been loaded by sending the
    snapshot-loaded message on the control topic.
    """
    import sentry_sdk

    sentry_sdk.init(dsn=settings.SENTRY_DSN)
    logging.basicConfig(level=getattr(logging, log_level.upper()), format='%(asctime)s %(message)s')

    logger = logging.getLogger('snuba.loaded-snapshot')
    logger.info("Sending load completion message for dataset %s, from source %s", dataset, source)

    dataset = get_dataset(dataset)
    assert isinstance(dataset, CdcDataset), \
        "Only CDC dataset have a control topic thus are supported."

    control_topic = control_topic or dataset.get_default_control_topic()

    snapshot_source = PostgresSnapshot.load(
        product=settings.SNAPSHOT_LOAD_PRODUCT,
        path=source,
    )

    descriptor = snapshot_source.get_descriptor()

    if not bootstrap_server:
        bootstrap_server = settings.DEFAULT_DATASET_BROKERS.get(
            dataset,
            settings.DEFAULT_BROKERS,
        )

    producer = Producer({
        'bootstrap.servers': ','.join(bootstrap_server),
        'partitioner': 'consistent',
        'message.max.bytes': 50000000,  # 50MB, default is 1MB
    })

    msg = SnapshotLoaded(
        id=descriptor.id,
        transaction_info=TransactionData(
            xmin=descriptor.xmin,
            xmax=descriptor.xmax,
            xip_list=descriptor.xip_list,
        ),
    )
    json_string = json.dumps(msg.to_dict())

    def delivery_callback(error, message):
        if error is not None:
            raise error
        else:
            logger.info("Message sent %r", message.value())

    producer.produce(
        control_topic,
        value=json_string,
        on_delivery=delivery_callback,
    )

    producer.flush()
