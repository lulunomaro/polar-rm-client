import inspect
import textwrap
import unittest
from unittest.mock import patch
from rm_client.backend import manager
from tests.test_video_source_snapshot import VideoSourceSnapshotTests
source = textwrap.dedent(inspect.getsource(manager.BackendManager.set_active_video_source))
start = source.index('    if not _retry and previous == request and (')
end = source.index('    if _retry or previous != request:', start)
mutated = source[:start] + source[end:]
namespace = vars(manager).copy()
exec(mutated, namespace)
suite = unittest.TestSuite([VideoSourceSnapshotTests('test_same_target_refresh_does_not_retry_ended_native_close_for_udp_target')])
with patch.object(manager.BackendManager, 'set_active_video_source', namespace['set_active_video_source']):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
assert len(result.failures) == 1 and not result.errors, 'negative control did not produce exact assertion failure'
assert 'is not' in result.failures[0][1], result.failures[0][1]
print('EXPECTED NEGATIVE: removing same-request guard replaces failed native stopper')
