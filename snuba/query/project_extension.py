from typing import Sequence

from snuba import settings, util
from snuba.query.extensions import ExtensionQueryProcessor, QueryExtension
from snuba.query.query import Query
from snuba.query.query_processor import ExtensionData
from snuba.replacer import get_projects_query_flags
from snuba.request.request_settings import RequestSettings
from snuba.state import get_config, get_configs
from snuba.state.rate_limit import RateLimitParameters, PROJECT_RATE_LIMIT_NAME


PROJECT_EXTENSION_SCHEMA = {
    'type': 'object',
    'properties': {
        'project': {
            'anyOf': [
                {'type': 'integer', 'minimum': 1},
                {
                    'type': 'array',
                    'items': {'type': 'integer', 'minimum': 1},
                    'minItems': 1,
                },
            ]
        },
    },
    # Need to select down to the project level for customer isolation and performance
    'required': ['project'],
    'additionalProperties': False,
}


class ProjectExtensionProcessor(ExtensionQueryProcessor):
    """
    Extension processor for datasets that require a project ID to be given in the request.

    It extracts the project IDs from the query and adds project specific rate limits.
    """

    def __init__(self, project_column: str) -> None:
        self.__project_column = project_column

    def _get_rate_limit_params(self, project_ids: Sequence[int]) -> RateLimitParameters:
        project_id = project_ids[0] if project_ids else 0  # TODO rate limit on every project in the list?

        prl, pcl = get_configs([
            ('project_per_second_limit', 1000),
            ('project_concurrent_limit', 1000),
        ])

        # Specific projects can have their rate limits overridden
        (per_second, concurr) = get_configs([
            ('project_per_second_limit_{}'.format(project_id), prl),
            ('project_concurrent_limit_{}'.format(project_id), pcl),
        ])

        return RateLimitParameters(
            rate_limit_name=PROJECT_RATE_LIMIT_NAME,
            bucket=str(project_id),
            per_second_limit=per_second,
            concurrent_limit=concurr,
        )

    def do_post_processing(
            self,
            project_ids: Sequence[int],
            query: Query,
            request_settings: RequestSettings,
    ) -> None:
        pass

    def process_query(
            self,
            query: Query,
            extension_data: ExtensionData,
            request_settings: RequestSettings,
    ) -> None:
        project_ids = util.to_list(extension_data['project'])

        if project_ids:
            query.add_conditions([(self.__project_column, 'IN', project_ids)])

        request_settings.add_rate_limit(self._get_rate_limit_params(project_ids))

        self.do_post_processing(project_ids, query, request_settings)


class ProjectWithGroupsProcessor(ProjectExtensionProcessor):
    """
    Extension processor that makes changes to the query by
    1. Adding the project
    2. Taking into consideration groups that should be excluded (groups are excluded because of replacement).
    """

    def do_post_processing(
            self,
            project_ids: Sequence[int],
            query: Query,
            request_settings: RequestSettings,
    ) -> None:
        if not request_settings.get_turbo():
            final, exclude_group_ids = get_projects_query_flags(project_ids)
            if not final and exclude_group_ids:
                # If the number of groups to exclude exceeds our limit, the query
                # should just use final instead of the exclusion set.
                max_group_ids_exclude = get_config('max_group_ids_exclude', settings.REPLACER_MAX_GROUP_IDS_TO_EXCLUDE)
                if len(exclude_group_ids) > max_group_ids_exclude:
                    query.set_final(True)
                else:
                    query.add_conditions([(['assumeNotNull', ['group_id']], 'NOT IN', exclude_group_ids)])
            else:
                query.set_final(final)


class ProjectExtension(QueryExtension):
    def __init__(self, processor: ProjectExtensionProcessor) -> None:
        super().__init__(
            schema=PROJECT_EXTENSION_SCHEMA,
            processor=processor,
        )
