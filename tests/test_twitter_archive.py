import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from archivage import state
from archivage.cli import (
    accountSyncMode,
    archiveAccount,
    collectionSyncPlan,
    syncCollection,
)
from archivage.storage import normalizeTweetId, tweetIdentity
from archivage.twitter import AccountUnavailable, QUERY_IDS, TwitterClient
from archivage.twitter_index import (
    indexFile,
    indexTweets,
    readThread,
    readTweet,
    recentTweets,
    searchTweets,
)


def sampleTweet(tweet_id: str, text: str, conversation_id: str | None = None):
    return {
        'core': {
            'user_results': {
                'result': {
                    'rest_id': '388426142',
                    'core': {
                        'screen_name': 'Markmanson',
                        'name': 'Mark Manson',
                    },
                },
            },
        },
        'legacy': {
            'id_str': tweet_id,
            'full_text': text,
            'conversation_id_str': conversation_id or tweet_id,
            'created_at': 'Fri Aug 15 12:00:00 +0000 2026',
        },
        'rest_id': tweet_id,
    }


class IdentityTests(unittest.TestCase):
    def test_tweet_identity_uses_stable_user_id(self):
        identity = tweetIdentity([sampleTweet('100', 'Archived tweet')])

        self.assertEqual(identity['user_id'], '388426142')
        self.assertEqual(identity['current_handle'], 'Markmanson')

    def test_state_retains_original_and_changed_handles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setAccountState(
                    'IAmMarkManson',
                    user_id='388426142',
                    current_handle='Markmanson',
                )
                account = state.getAccountState('IAmMarkManson')

        self.assertEqual(account['user_id'], '388426142')
        self.assertEqual(account['current_handle'], 'Markmanson')
        self.assertEqual(
            account['aliases'],
            ['IAmMarkManson', 'Markmanson'],
        )

    def test_normalizes_status_url(self):
        self.assertEqual(
            normalizeTweetId('https://x.com/Markmanson/status/1234567890?s=20'),
            '1234567890',
        )

    def test_transient_error_preserves_sync_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setAccountState(
                    'example',
                    newest_id='200',
                    oldest_id='100',
                    status='complete',
                )
                state.markAccountError('example', 'temporary failure')
                account = state.getAccountState('example')

        self.assertEqual(account['newest_id'], '200')
        self.assertEqual(account['oldest_id'], '100')
        self.assertEqual(account['status'], 'error')
        self.assertEqual(account['consecutive_errors'], 1)


class UnavailableAccountTests(unittest.TestCase):
    def test_deleted_account_is_classified_without_erasing_state(self):
        client = TwitterClient(Path('/tmp/not-used'))
        client._call = lambda *args, **kwargs: {
            'data': {
                'user': {
                    'result': {
                        '__typename': 'UserUnavailable',
                        'reason': 'AccountSuspended',
                    },
                },
            },
        }

        with self.assertRaises(AccountUnavailable):
            client.getUserId('AudrandS')

    def test_missing_user_result_is_unavailable(self):
        client = TwitterClient(Path('/tmp/not-used'))
        client._call = lambda *args, **kwargs: {
            'data': {'user': {}},
            'errors': [{'message': 'User is unavailable'}],
        }

        with self.assertRaisesRegex(AccountUnavailable, 'User is unavailable'):
            client.getUserTweets('731746399746961408')


class QueryIdRecoveryTests(unittest.TestCase):
    def test_bookmarks_falls_back_when_live_discovery_is_unauthorized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TwitterClient(Path('/tmp/not-used'))
            client.query_cache = Path(temp_dir) / 'query-ids.json'
            client.query_ids['Bookmarks'] = 'stale-query-id'
            client.client = Mock()
            client.client.cookies = {}
            client.client.headers = {}
            client.client.get.side_effect = [
                httpx.Response(
                    404,
                    request=httpx.Request('GET', 'https://x.com/stale'),
                ),
                httpx.Response(
                    401,
                    request=httpx.Request('GET', 'https://x.com/'),
                ),
                httpx.Response(
                    200,
                    json={'data': {'bookmark_timeline_v2': {}}},
                    request=httpx.Request('GET', 'https://x.com/working'),
                ),
            ]

            result = client._call('Bookmarks', {})
            urls = [call.args[0] for call in client.client.get.call_args_list]

        self.assertIn('bookmark_timeline_v2', result['data'])
        self.assertIn('/stale-query-id/Bookmarks', urls[0])
        self.assertEqual(urls[1], 'https://x.com/')
        self.assertIn(f"/{QUERY_IDS['Bookmarks']}/Bookmarks", urls[2])


class CollectionRecoveryTests(unittest.TestCase):
    def test_interrupted_collection_without_cursor_recovers_incrementally(self):
        state_value = {
            'status': 'in_progress',
            'newest_id': '200',
            'oldest_id': '100',
            'count': 50,
        }

        self.assertEqual(collectionSyncPlan(state_value), ('incremental', None))

    def test_full_option_ignores_saved_cursor(self):
        state_value = {
            'status': 'in_progress',
            'newest_id': '200',
            'cursor': 'saved-cursor',
            'sync_mode': 'incremental',
        }

        self.assertEqual(collectionSyncPlan(state_value, full=True), ('full', None))

    def test_failed_full_run_keeps_full_mode_before_first_page(self):
        state_value = {
            'status': 'error',
            'newest_id': '200',
            'sync_mode': 'full',
        }

        self.assertEqual(collectionSyncPlan(state_value), ('full', None))

    def test_failed_collection_records_error_and_retry_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            output_path = Path(temp_dir) / 'bookmarks.jsonl.gz'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setCollectionState(
                    'bookmarks',
                    status='complete',
                    newest_id='200',
                    oldest_id='100',
                    count=50,
                )

                def failFetch(cursor, count):
                    raise RuntimeError('temporary failure')

                with self.assertRaisesRegex(RuntimeError, 'temporary failure'):
                    syncCollection(
                        None,
                        'bookmarks',
                        output_path,
                        set(),
                        failFetch,
                    )
                failed = state.getCollectionState('bookmarks')

        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['sync_mode'], 'incremental')
        self.assertEqual(failed['newest_id'], '200')
        self.assertEqual(collectionSyncPlan(failed), ('incremental', None))

    def test_forced_full_run_discards_a_stale_cursor_before_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            output_path = Path(temp_dir) / 'bookmarks.jsonl.gz'
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setCollectionState(
                    'bookmarks',
                    status='error',
                    newest_id='200',
                    cursor='stale-cursor',
                    sync_mode='incremental',
                )

                def failFetch(cursor, count):
                    self.assertIsNone(cursor)
                    raise RuntimeError('temporary failure')

                with self.assertRaisesRegex(RuntimeError, 'temporary failure'):
                    syncCollection(
                        None,
                        'bookmarks',
                        output_path,
                        set(),
                        failFetch,
                        full=True,
                    )
                failed = state.getCollectionState('bookmarks')

        self.assertNotIn('cursor', failed)
        self.assertEqual(failed['sync_mode'], 'full')
        self.assertEqual(collectionSyncPlan(failed), ('full', None))


class AccountRecoveryTests(unittest.TestCase):
    def test_interrupted_incremental_account_stays_incremental(self):
        state_value = {
            'status': 'in_progress',
            'newest_id': '200',
            'sync_mode': 'incremental',
        }

        self.assertEqual(accountSyncMode(state_value), 'incremental')

    def test_interrupted_full_account_stays_full(self):
        state_value = {
            'status': 'in_progress',
            'newest_id': '200',
            'sync_mode': 'full',
        }

        self.assertEqual(accountSyncMode(state_value), 'full')

    def test_legacy_interruption_with_boundary_recovers_incrementally(self):
        state_value = {
            'status': 'in_progress',
            'newest_id': '200',
        }

        self.assertEqual(accountSyncMode(state_value), 'incremental')

    def test_shared_client_is_not_closed_between_accounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / 'state.json'
            archive_dir = Path(temp_dir) / 'archive'
            archive_dir.mkdir()
            client = Mock()
            with patch.object(state, '_stateFile', return_value=state_file):
                state.setAccountState(
                    'example',
                    user_id='123',
                    newest_id='200',
                    status='complete',
                )
                with patch('archivage.cli.syncForward') as sync_forward:
                    archiveAccount(
                        'example',
                        Path('/tmp/not-used'),
                        archive_dir,
                        client=client,
                    )

        sync_forward.assert_called_once()
        client.close.assert_not_called()


class IndexTests(unittest.TestCase):
    def test_index_read_search_and_thread_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_file = root / 'IAmMarkManson.jsonl.gz'
            database = root / 'search.sqlite'
            tweets = [
                sampleTweet('100', 'Archived ideas remain searchable.'),
                sampleTweet('101', 'A reply in the same thread.', '100'),
            ]
            with gzip.open(archive_file, 'wt') as output:
                for tweet in tweets:
                    output.write(json.dumps(tweet) + '\n')

            self.assertEqual(indexFile(archive_file, 'IAmMarkManson', database), 2)
            self.assertEqual(indexFile(archive_file, 'IAmMarkManson', database), 0)
            self.assertEqual(readTweet('100', database)['handle'], 'Markmanson')
            self.assertEqual(searchTweets('searchable', path=database)[0]['id'], '100')
            self.assertEqual(
                [tweet['id'] for tweet in readThread('101', database)],
                ['100', '101'],
            )

    def test_recent_filters_by_time_account_and_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / 'search.sqlite'
            first = sampleTweet('100', 'Current setup')
            first['legacy']['created_at'] = 'Mon Aug 18 12:00:00 +0000 2026'
            reply = sampleTweet('101', 'Reply noise')
            reply['legacy']['created_at'] = 'Mon Aug 18 13:00:00 +0000 2026'
            reply['legacy']['in_reply_to_status_id_str'] = '100'
            old = sampleTweet('99', 'Old setup')
            old['legacy']['created_at'] = 'Fri Aug 15 12:00:00 +0000 2026'
            indexTweets([first, reply, old], 'RodolpheSteffan', database)

            rows = recentTweets(
                datetime(2026, 8, 17, tzinfo=timezone.utc),
                handles=['rodolphesteffan'],
                path=database,
            )

        self.assertEqual([row['id'] for row in rows], ['100'])


if __name__ == '__main__':
    unittest.main()
