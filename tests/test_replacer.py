import pytz
import re
from datetime import datetime
from functools import partial
import simplejson as json

from snuba import replacer
from snuba.clickhouse import DATETIME_FORMAT
from snuba.settings import PAYLOAD_DATETIME_FORMAT
from snuba.utils.metrics.backends.dummy import DummyMetricsBackend
from snuba.utils.streams.kafka import KafkaMessage, TopicPartition
from tests.base import BaseEventsTest


class TestReplacer(BaseEventsTest):
    def setup_method(self, test_method):
        super(TestReplacer, self).setup_method(test_method)

        from snuba.views import application
        assert application.testing is True

        self.app = application.test_client()
        self.app.post = partial(self.app.post, headers={'referer': 'test'})
        self.replacer = replacer.ReplacerWorker(self.clickhouse, self.dataset, DummyMetricsBackend(strict=True))

        self.project_id = 1

    def _wrap(self, msg: str) -> KafkaMessage:
        return KafkaMessage(
            TopicPartition('replacements', 0),
            0,
            json.dumps(msg).encode('utf-8'),
        )

    def _issue_count(self, project_id, group_id=None):
        args = {
            'project': [project_id],
            'aggregations': [['count()', '', 'count']],
            'groupby': ['issue'],
        }

        if group_id:
            args.setdefault('conditions', []).append(('group_id', '=', group_id))

        return json.loads(self.app.post('/query', data=json.dumps(args)).data)['data']

    def test_delete_groups_process(self):
        timestamp = datetime.now(tz=pytz.utc)
        message = (2, 'end_delete_groups', {
            'project_id': self.project_id,
            'group_ids': [1, 2, 3],
            'datetime': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        })

        replacement = self.replacer.process_message(self._wrap(message))

        assert re.sub("[\n ]+", " ", replacement.count_query_template).strip() == \
            "SELECT count() FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id IN (%(group_ids)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert re.sub("[\n ]+", " ", replacement.insert_query_template).strip() == \
            "INSERT INTO %(dist_write_table_name)s (%(required_columns)s) SELECT %(select_columns)s FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id IN (%(group_ids)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert replacement.query_args == {
            'group_ids': '1, 2, 3',
            'project_id': self.project_id,
            'required_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days',
            'select_columns': 'event_id, project_id, group_id, timestamp, 1, retention_days',
            'timestamp': timestamp.strftime(DATETIME_FORMAT),
        }
        assert replacement.query_time_flags == (replacer.EXCLUDE_GROUPS, self.project_id, [1, 2, 3])

    def test_merge_process(self):
        timestamp = datetime.now(tz=pytz.utc)
        message = (2, 'end_merge', {
            'project_id': self.project_id,
            'new_group_id': 2,
            'previous_group_ids': [1, 2],
            'datetime': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        })

        replacement = self.replacer.process_message(self._wrap(message))

        assert re.sub("[\n ]+", " ", replacement.count_query_template).strip() == \
            "SELECT count() FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id IN (%(previous_group_ids)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert re.sub("[\n ]+", " ", replacement.insert_query_template).strip() == \
            "INSERT INTO %(dist_write_table_name)s (%(all_columns)s) SELECT %(select_columns)s FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id IN (%(previous_group_ids)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert replacement.query_args == {
            'all_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'select_columns': 'event_id, project_id, 2, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'previous_group_ids': ", ".join(str(gid) for gid in [1, 2]),
            'project_id': self.project_id,
            'timestamp': timestamp.strftime(DATETIME_FORMAT),
        }
        assert replacement.query_time_flags == (replacer.EXCLUDE_GROUPS, self.project_id, [1, 2])

    def test_unmerge_process(self):
        timestamp = datetime.now(tz=pytz.utc)
        message = (2, 'end_unmerge', {
            'project_id': self.project_id,
            'previous_group_id': 1,
            'new_group_id': 2,
            'hashes': ["a" * 32, "b" * 32],
            'datetime': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        })

        replacement = self.replacer.process_message(self._wrap(message))

        assert re.sub("[\n ]+", " ", replacement.count_query_template).strip() == \
            "SELECT count() FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id = %(previous_group_id)s AND primary_hash IN (%(hashes)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert re.sub("[\n ]+", " ", replacement.insert_query_template).strip() == \
            "INSERT INTO %(dist_write_table_name)s (%(all_columns)s) SELECT %(select_columns)s FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND group_id = %(previous_group_id)s AND primary_hash IN (%(hashes)s) AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted"
        assert replacement.query_args == {
            'all_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'select_columns': 'event_id, project_id, 2, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'hashes': "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
            'previous_group_id': 1,
            'project_id': self.project_id,
            'timestamp': timestamp.strftime(DATETIME_FORMAT),
        }
        assert replacement.query_time_flags == (replacer.NEEDS_FINAL, self.project_id)

    def test_delete_promoted_tag_process(self):
        timestamp = datetime.now(tz=pytz.utc)
        message = (2, 'end_delete_tag', {
            'project_id': self.project_id,
            'tag': 'sentry:user',
            'datetime': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        })

        replacement = self.replacer.process_message(self._wrap(message))

        assert re.sub("[\n ]+", " ", replacement.count_query_template).strip() == \
            "SELECT count() FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted AND %(tag_column)s IS NOT NULL"
        assert re.sub("[\n ]+", " ", replacement.insert_query_template).strip() == \
            "INSERT INTO %(dist_write_table_name)s (%(all_columns)s) SELECT %(select_columns)s FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted AND %(tag_column)s IS NOT NULL"
        assert replacement.query_args == {
            'all_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'select_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, NULL, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, arrayFilter(x -> (indexOf(`tags.key`, x) != indexOf(`tags.key`, \'sentry:user\')), `tags.key`), arrayMap(x -> arrayElement(`tags.value`, x), arrayFilter(x -> x != indexOf(`tags.key`, \'sentry:user\'), arrayEnumerate(`tags.value`))), contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'tag_column': '`sentry:user`',
            'tag_str': "'sentry:user'",
            'project_id': self.project_id,
            'timestamp': timestamp.strftime(DATETIME_FORMAT),
        }
        assert replacement.query_time_flags == (replacer.NEEDS_FINAL, self.project_id)

    def test_delete_unpromoted_tag_process(self):
        timestamp = datetime.now(tz=pytz.utc)
        message = (2, 'end_delete_tag', {
            'project_id': self.project_id,
            'tag': "foo:bar",
            'datetime': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        })

        replacement = self.replacer.process_message(self._wrap(message))

        assert re.sub("[\n ]+", " ", replacement.count_query_template).strip() == \
            "SELECT count() FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted AND has(`tags.key`, %(tag_str)s)"
        assert re.sub("[\n ]+", " ", replacement.insert_query_template).strip() == \
            "INSERT INTO %(dist_write_table_name)s (%(all_columns)s) SELECT %(select_columns)s FROM %(dist_read_table_name)s FINAL WHERE project_id = %(project_id)s AND received <= CAST('%(timestamp)s' AS DateTime) AND NOT deleted AND has(`tags.key`, %(tag_str)s)"
        assert replacement.query_args == {
            'all_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, tags.key, tags.value, contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'select_columns': 'event_id, project_id, group_id, timestamp, deleted, retention_days, platform, message, primary_hash, received, search_message, title, location, user_id, username, email, ip_address, geo_country_code, geo_region, geo_city, sdk_name, sdk_version, type, version, offset, partition, os_build, os_kernel_version, device_name, device_brand, device_locale, device_uuid, device_model_id, device_arch, device_battery_level, device_orientation, device_simulator, device_online, device_charging, level, logger, server_name, transaction, environment, `sentry:release`, `sentry:dist`, `sentry:user`, site, url, app_device, device, device_family, runtime, runtime_name, browser, browser_name, os, os_name, os_rooted, arrayFilter(x -> (indexOf(`tags.key`, x) != indexOf(`tags.key`, \'foo:bar\')), `tags.key`), arrayMap(x -> arrayElement(`tags.value`, x), arrayFilter(x -> x != indexOf(`tags.key`, \'foo:bar\'), arrayEnumerate(`tags.value`))), contexts.key, contexts.value, http_method, http_referer, exception_stacks.type, exception_stacks.value, exception_stacks.mechanism_type, exception_stacks.mechanism_handled, exception_frames.abs_path, exception_frames.filename, exception_frames.package, exception_frames.module, exception_frames.function, exception_frames.in_app, exception_frames.colno, exception_frames.lineno, exception_frames.stack_level, culprit, sdk_integrations, modules.name, modules.version',
            'tag_column': '`foo:bar`',
            'tag_str': "'foo:bar'",
            'project_id': self.project_id,
            'timestamp': timestamp.strftime(DATETIME_FORMAT),
        }
        assert replacement.query_time_flags == (replacer.NEEDS_FINAL, self.project_id)

    def test_delete_groups_insert(self):
        self.event['project_id'] = self.project_id
        self.event['group_id'] = 1
        self.write_raw_events(self.event)

        assert self._issue_count(self.project_id) == [{'count': 1, 'issue': 1}]

        timestamp = datetime.now(tz=pytz.utc)

        project_id = self.project_id

        message = KafkaMessage(
            TopicPartition('replacements', 1),
            42,
            json.dumps((2, 'end_delete_groups', {
                'project_id': project_id,
                'group_ids': [1],
                'datetime': timestamp.strftime(PAYLOAD_DATETIME_FORMAT),
            })).encode('utf-8'),
        )

        processed = self.replacer.process_message(message)
        self.replacer.flush_batch([processed])

        assert self._issue_count(self.project_id) == []

    def test_merge_insert(self):
        self.event['project_id'] = self.project_id
        self.event['group_id'] = 1
        self.write_raw_events(self.event)

        assert self._issue_count(self.project_id) == [{'count': 1, 'issue': 1}]

        timestamp = datetime.now(tz=pytz.utc)

        project_id = self.project_id

        message = KafkaMessage(
            TopicPartition('replacements', 1),
            42,
            json.dumps((2, 'end_merge', {
                'project_id': project_id,
                'new_group_id': 2,
                'previous_group_ids': [1],
                'datetime': timestamp.strftime(PAYLOAD_DATETIME_FORMAT),
            })).encode('utf-8'),
        )

        processed = self.replacer.process_message(message)
        self.replacer.flush_batch([processed])

        assert self._issue_count(1) == [{'count': 1, 'issue': 2}]

    def test_unmerge_insert(self):
        self.event['project_id'] = self.project_id
        self.event['group_id'] = 1
        self.event['primary_hash'] = 'a' * 32
        self.write_raw_events(self.event)

        assert self._issue_count(self.project_id) == [{'count': 1, 'issue': 1}]

        timestamp = datetime.now(tz=pytz.utc)

        project_id = self.project_id

        message = KafkaMessage(
            TopicPartition('replacements', 1),
            42,
            json.dumps((2, 'end_unmerge', {
                'project_id': project_id,
                'previous_group_id': 1,
                'new_group_id': 2,
                'hashes': ['a' * 32],
                'datetime': timestamp.strftime(PAYLOAD_DATETIME_FORMAT),
            })).encode('utf-8'),
        )

        processed = self.replacer.process_message(message)
        self.replacer.flush_batch([processed])

        assert self._issue_count(self.project_id) == [{'count': 1, 'issue': 2}]

    def test_delete_tag_promoted_insert(self):
        self.event['project_id'] = self.project_id
        self.event['group_id'] = 1
        self.event['data']['tags'].append(['browser.name', 'foo'])
        self.event['data']['tags'].append(['notbrowser', 'foo'])
        self.write_raw_events(self.event)

        project_id = self.project_id

        def _issue_count(total=False):
            return json.loads(self.app.post('/query', data=json.dumps({
                'project': [project_id],
                'aggregations': [['count()', '', 'count']],
                'conditions': [['tags[browser.name]', '=', 'foo']] if not total else [],
                'groupby': ['issue'],
            })).data)['data']

        assert _issue_count() == [{'count': 1, 'issue': 1}]
        assert _issue_count(total=True) == [{'count': 1, 'issue': 1}]

        timestamp = datetime.now(tz=pytz.utc)

        message = KafkaMessage(
            TopicPartition('replacements', 1),
            42,
            json.dumps((2, 'end_delete_tag', {
                'project_id': project_id,
                'tag': 'browser.name',
                'datetime': timestamp.strftime(PAYLOAD_DATETIME_FORMAT),
            })).encode('utf-8'),
        )

        processed = self.replacer.process_message(message)
        self.replacer.flush_batch([processed])

        assert _issue_count() == []
        assert _issue_count(total=True) == [{'count': 1, 'issue': 1}]

    def test_query_time_flags(self):
        project_ids = [1, 2]

        assert replacer.get_projects_query_flags(project_ids) == (False, [])

        replacer.set_project_needs_final(100)
        assert replacer.get_projects_query_flags(project_ids) == (False, [])

        replacer.set_project_needs_final(1)
        assert replacer.get_projects_query_flags(project_ids) == (True, [])

        replacer.set_project_needs_final(2)
        assert replacer.get_projects_query_flags(project_ids) == (True, [])

        replacer.set_project_exclude_groups(1, [1, 2])
        replacer.set_project_exclude_groups(2, [3, 4])
        assert replacer.get_projects_query_flags(project_ids) == (True, [1, 2, 3, 4])
